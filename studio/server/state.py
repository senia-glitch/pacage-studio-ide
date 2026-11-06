"""Единое состояние студии: граф в памяти, ревизии, watcher, метаданные."""

from __future__ import annotations

import contextlib
import hashlib
import json
import secrets
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

from studio import log as slog
from studio import store
from studio.agent import (
    BriefNotFound,
    BriefOptions,
    build_brief,
    build_decompose_prompt,
    inbox,
    load_brief,
    save_brief_sidecars,
)
from studio.agent import (
    save_brief as save_brief_file,
)
from studio.agent import (
    verify_brief as verify_brief_data,
)
from studio.agent.brief import load_rules, rules_override_path, standard_rules
from studio.agent.decompose import (
    decompose_override_path,
    load_decompose_template,
    parse_decompose_answer,
    standard_decompose_template,
)
from studio.agent.report import build_report, save_report
from studio.agent.sync import build_sync_prompt, check_sync_response
from studio.agent.sync_json import (
    SyncJsonError,
    check_undo_entries,
    extract_json,
    same_state,
)
from studio.creator import CreateError
from studio.creator.plan import create_plan, delete_plan, preview_plan, update_plan
from studio.env import read_env
from studio.frontend.brief import FRONT_BRIEF_FILENAME, front_prompt_state, load_front_brief
from studio.frontend.links import check_links
from studio.metadata import (
    MetadataError,
    delete_domain_plan,
    delete_graph_node,
    metadata_path,
    read_metadata,
    restore_graph_node,
    save_domain_plan,
    save_graph_node,
    save_node_metadata,
    save_planned_change,
)
from studio.metamodel import EDGE_KINDS, NODE_KINDS, Graph, Node
from studio.scanner import scan
from studio.scanner.anchors import AnchorResolver
from studio.server import access
from studio.source import load_sources, stat_fingerprint

DEFAULT_POLL = 1.0
DEBOUNCE = 0.3


def _metadata_stamp(root: Path) -> str:
    """Содержимое metadata.json для подписи ревизии: правка из IDE видна сразу."""
    try:
        return hashlib.sha1(metadata_path(root).read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""
    except OSError:
        return "unreadable"


class NodeNotFound(Exception):
    pass


class ProjectState:
    """ОДИН замок на скан, запись метаданных и создание."""

    def __init__(self, project_root: Path, poll_interval: float = DEFAULT_POLL):
        self.root = Path(project_root).resolve()
        self.lock = threading.RLock()
        self._cond = threading.Condition(self.lock)
        self.graph: Graph | None = None
        self.revision = 0
        self.scanning = False
        self.token = secrets.token_urlsafe(24)
        self.host = "127.0.0.1"
        self.verbose = False
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self._watch_last: tuple | None = None
        self._signature: tuple | None = None
        # Второй счётчик: правки frontend.json и ideas.json (их пишет и агент). Скан кода
        # они не требуют, но интерфейс должен увидеть их без перезагрузки вкладки.
        self.aux = 0
        self._aux_stamp: str | None = None

    # ------------------------------------------------------------------ scan

    def rescan(self, reason: str) -> Graph:
        with self.lock:
            if self.scanning and self.graph is not None:
                return self.graph
            self.scanning = True
            try:
                graph = scan(self.root)
                metadata = read_metadata(self.root)
                planned_changes = metadata.extra.get("planned_changes")
                if not isinstance(planned_changes, dict):
                    planned_changes = {}
                planned_domains = metadata.extra.get("planned_domains")
                if not isinstance(planned_domains, list):
                    planned_domains = []
                domain_changes = metadata.extra.get("domain_changes")
                if not isinstance(domain_changes, dict):
                    domain_changes = {}
                for node in graph.nodes:
                    change = planned_changes.get(node.id)
                    node.planned_change = dict(change) if isinstance(change, dict) else {}
                graph.planned_domains = [
                    dict(item) for item in planned_domains if isinstance(item, dict)
                ]
                graph.domain_changes = {
                    str(key): dict(value)
                    for key, value in domain_changes.items()
                    if isinstance(value, dict)
                }
                try:
                    store.write_graph(self.root, graph)
                except OSError as exc:
                    slog.step(f"graph.json не записан: {exc}")
                env = read_env(self.root)
                signature = (
                    graph.fingerprint,
                    env.routes_package,
                    env.core_discover,
                    env.api_prefix,
                    env.models_spec,
                    tuple(path.name for path in env.models_paths),
                    _metadata_stamp(self.root),
                    tuple(sorted((d.code, d.file, d.line) for d in graph.diagnostics)),
                )
                changed = self.graph is None or signature != self._signature
                if changed:
                    self.graph = graph
                    self._signature = signature
                    self.revision += 1
                    slog.step(
                        f"скан ({reason}): rev={self.revision} узлов={len(graph.nodes)} "
                        f"связей={len(graph.edges)} диагностик={len(graph.diagnostics)}"
                    )
                    self._cond.notify_all()
                else:
                    self.graph = graph
                return graph
            finally:
                self.scanning = False

    def ensure_graph(self) -> Graph:
        with self.lock:
            if self.graph is None:
                return self.rescan("start")
            return self.graph

    # --------------------------------------------------------------- editing

    def _resolve_node(self, node_id: str) -> Node:
        graph = self.ensure_graph()
        node = graph.by_id().get(node_id)
        if node is not None:
            return node
        resolved, _code = AnchorResolver(graph).resolve(node_id)
        if resolved is None:
            raise NodeNotFound(node_id)
        node = graph.by_id().get(resolved)
        if node is None:
            raise NodeNotFound(node_id)
        return node

    def save_metadata(self, data: dict) -> dict:
        with self.lock:
            node_id = str(data.get("node_id") or "")
            if not node_id:
                raise MetadataError("invalid_input", "Нужен node_id")
            node = self._resolve_node(node_id)
            description = data.get("description")
            acceptance = data.get("acceptance")
            domain = data.get("domain")
            if description is None:
                description = node.description
            if acceptance is None:
                acceptance = list(node.acceptance)
            if domain is None:
                domain = node.domain
            if not isinstance(description, str):
                raise MetadataError("invalid_input", "Описание должно быть строкой")
            if isinstance(acceptance, str):
                acceptance_list = [
                    line.strip() for line in acceptance.splitlines() if line.strip()
                ]
            elif isinstance(acceptance, list):
                acceptance_list = [str(item) for item in acceptance if str(item).strip()]
            else:
                raise MetadataError("invalid_input", "Критерии приёмки должны быть списком строк")
            if not isinstance(domain, str):
                raise MetadataError("invalid_input", "Домен должен быть строкой")
            if node.kind != "endpoint":
                domain = ""

            result = save_node_metadata(
                self.root,
                node,
                description,
                acceptance_list,
                domain,
            )
            graph = self.rescan("save")
            return {
                "graph": graph.model_dump(),
                "changed": result.changed,
            }

    def save_planned_change(self, data: dict) -> dict:
        with self.lock:
            node_id = str(data.get("node_id") or "")
            node = self._resolve_node(node_id)
            if node.planned:
                raise MetadataError(
                    "invalid_input", "Пометка правки доступна только фактическим узлам"
                )
            acceptance = data.get("acceptance") or []
            if isinstance(acceptance, str):
                acceptance = [line.strip() for line in acceptance.splitlines() if line.strip()]
            if not isinstance(acceptance, list):
                raise MetadataError("invalid_input", "Критерии должны быть списком строк")
            result = save_planned_change(
                self.root, node_id, str(data.get("note") or ""), acceptance
            )
            graph = self.rescan("planned_change")
            return {"graph": graph.model_dump(), "changed": result.changed}

    def save_domain_plan(self, data: dict) -> dict:
        with self.lock:
            graph = self.ensure_graph()
            source = str(data.get("source") or "").strip()
            name = str(data.get("name") or "").strip()
            factual_domains = {
                part.strip()
                for node in graph.nodes if node.kind == "endpoint"
                for part in str(node.domain or "").split(",") if part.strip()
            }
            planned_domains = read_metadata(self.root).extra.get("planned_domains") or []
            if source and source not in factual_domains:
                raise MetadataError("invalid_input", "Фактический домен не найден — обновите карту")
            other_domains = factual_domains - ({source} if source else set())
            if name in other_domains:
                raise MetadataError("invalid_input", f"Домен «{name}» уже существует")
            if any(
                isinstance(item, dict) and str(item.get("name") or "").strip() == name
                and str(item.get("id") or "") != str(data.get("id") or "")
                for item in planned_domains
            ):
                raise MetadataError("invalid_input", f"Плановый домен «{name}» уже существует")
            result = save_domain_plan(self.root, data)
            graph = self.rescan("domain_plan")
            return {"graph": graph.model_dump(), "changed": result.changed}

    def delete_domain_plan(self, data: dict) -> dict:
        with self.lock:
            result = delete_domain_plan(self.root, str(data.get("id") or ""))
            graph = self.rescan("domain_plan_delete")
            return {"graph": graph.model_dump(), "changed": result.changed}

    def save_graph_node(self, data: dict) -> dict:
        """Persist a visual TODO node and its optional parent relationship."""
        with self.lock:
            graph = self.ensure_graph()
            node_id = str(data.get("node_id") or f"studio:{uuid.uuid4().hex}")
            kind = str(data.get("kind") or "")
            name = str(data.get("name") or "").strip()
            if kind not in NODE_KINDS:
                raise MetadataError("invalid_input", "Выберите допустимый тип узла")
            if not name:
                raise MetadataError("invalid_input", "Укажите название узла")
            if len(name) > 180:
                raise MetadataError("invalid_input", "Название узла слишком длинное")
            description = data.get("description") or ""
            acceptance = data.get("acceptance") or []
            domain = data.get("domain") or ""
            if not isinstance(description, str) or not isinstance(domain, str):
                raise MetadataError("invalid_input", "Описание и домен должны быть строками")
            if isinstance(acceptance, str):
                acceptance = [line.strip() for line in acceptance.splitlines() if line.strip()]
            if not isinstance(acceptance, list):
                raise MetadataError("invalid_input", "Критерии должны быть списком строк")
            parent_id = str(data.get("parent_id") or "")
            edge_kind = str(data.get("edge_kind") or "")
            existing = graph.by_id().get(node_id)
            if existing is not None:
                record = read_metadata(self.root).nodes.get(node_id)
                if not existing.planned or not record or not record.get("studio_owned"):
                    raise MetadataError(
                        "invalid_input",
                        "Редактировать можно только узлы, созданные в графе",
                    )
                if (
                    data.get("_actor") == "agent"
                    and (existing.details or {}).get("created_by") != "agent"
                ):
                    raise MetadataError(
                        "mode_forbidden",
                        "Узел создан человеком: агент может добавлять к нему дочерние узлы, "
                        "но не менять его",
                    )
            if existing is not None and not parent_id:
                incoming = next((edge for edge in graph.edges if edge.to_node == node_id), None)
                if incoming is not None:
                    parent_id, edge_kind = incoming.from_node, incoming.kind
                elif kind == "test":
                    test_edge = next(
                        (
                            edge for edge in graph.edges
                            if edge.from_node == node_id and edge.kind == "tests"
                        ),
                        None,
                    )
                    if test_edge is not None:
                        parent_id, edge_kind = test_edge.to_node, test_edge.kind
            if parent_id == node_id:
                raise MetadataError("invalid_input", "Узел нельзя связать с самим собой")
            parent = graph.by_id().get(parent_id) if parent_id else None
            if parent_id and parent is None:
                raise MetadataError(
                    "node_not_found",
                    "Родительский узел не найден — обновите карту",
                )
            if parent_id and edge_kind not in EDGE_KINDS:
                raise MetadataError("invalid_input", "Выберите допустимый тип связи")
            details = dict((existing.details or {}) if existing is not None else {"name": name})
            if existing is None and data.get("_actor") == "agent":
                details["created_by"] = "agent"
            if kind == "endpoint":
                details.update(
                    {
                        "function_name": name,
                        "http_method": str(
                            data.get("http_method", details.get("http_method") or "") or ""
                        ),
                        "path": str(data.get("path", details.get("path") or "") or ""),
                    }
                )
                domain_plan_id = str(data.get("domain_plan_id") or "")
                if domain_plan_id:
                    details["domain_plan_id"] = domain_plan_id
            elif kind == "scenario":
                details.update({"name": name, "class_name": name})
            elif kind == "io":
                details["method"] = name
                if parent and parent.kind == "scenario":
                    details.update(
                        {
                            "scenario_class": (parent.details or {}).get("class_name", ""),
                            "scenario_id": parent_id,
                        }
                    )
            elif kind == "model":
                details.update({"class_name": name, "tablename": name, "table": name})
            elif kind == "test":
                details["test_name"] = name
            elif kind == "nested":
                details.update(
                    {
                        "name": name,
                        "role": str(data.get("role") or details.get("role") or "module"),
                        "parent_id": parent_id or details.get("parent_id", ""),
                    }
                )
            target_file = str(
                data.get("target_file")
                or (parent.file_path if parent else (existing.file_path if existing else ""))
            )
            result = save_graph_node(
                self.root, node_id, kind, name, description,
                [str(item) for item in acceptance], domain, target_file,
                details, parent_id, edge_kind,
            )
            graph = self.rescan("graph_node")
            return {"graph": graph.model_dump(), "changed": result.changed, "node_id": node_id}

    def delete_graph_node(self, data: dict) -> dict:
        with self.lock:
            graph = self.ensure_graph()
            metadata = read_metadata(self.root)
            node_id = str(data.get("node_id") or "")
            if not node_id:
                raise MetadataError("invalid_input", "Нужен node_id")
            if not graph.by_id().get(node_id):
                raise NodeNotFound(node_id)
            # Remove child TODO nodes as one visual branch, while preserving real code nodes.
            removable = {node_id}
            changed = True
            while changed:
                changed = False
                for edge in graph.edges:
                    # Test edges point from a test toward the scenario it verifies;
                    # for deletion, treat that relation as a visual child of the scenario.
                    if edge.kind == "tests":
                        parent_id, child_id = edge.to_node, edge.from_node
                    else:
                        parent_id, child_id = edge.from_node, edge.to_node
                    if parent_id not in removable or child_id in removable:
                        continue
                    child = graph.by_id().get(child_id)
                    record = metadata.nodes.get(child_id) if child is not None else None
                    parents = [
                        item for item in graph.edges
                        if (item.from_node if item.kind == "tests" else item.to_node) == child_id
                    ]
                    only_this_branch = all(
                        (item.to_node if item.kind == "tests" else item.from_node) in removable
                        for item in parents
                    )
                    if (
                        child is not None
                        and child.planned
                        and record
                        and record.get("studio_owned")
                        and only_this_branch
                    ):
                        removable.add(child.id)
                        changed = True
            if data.get("_actor") == "agent":
                foreign = [
                    item
                    for item in removable
                    if (graph.by_id()[item].details or {}).get("created_by") != "agent"
                ]
                if foreign:
                    raise MetadataError(
                        "mode_forbidden",
                        "Агент удаляет только то, что создал сам; в этой ветке есть узлы человека",
                    )
            result = delete_graph_node(self.root, node_id, removable)
            graph = self.rescan("graph_node_delete")
            # Снапшот удалённой ветки для отмены: записи узлов и касающиеся их связи.
            undo_nodes = {
                item_id: record
                for item_id, record in (metadata.nodes or {}).items()
                if item_id in removable and isinstance(record, dict) and record.get("studio_owned")
            }
            undo_edges = [
                edge
                for edge in (metadata.edges or [])
                if isinstance(edge, dict)
                and (edge.get("from") in undo_nodes or edge.get("to") in undo_nodes)
            ]
            return {
                "graph": graph.model_dump(),
                "changed": result.changed,
                "removed": sorted(removable),
                "undo": {"nodes": undo_nodes, "edges": undo_edges},
            }

    def restore_graph_node(self, data: dict) -> dict:
        undo = data.get("undo") if isinstance(data, dict) else None
        if not isinstance(undo, dict) or not isinstance(undo.get("nodes"), dict):
            raise MetadataError("invalid_input", "Нужен снапшот undo с узлами")
        with self.lock:
            result = restore_graph_node(self.root, undo)
            graph = self.rescan("graph_node_restore")
            return {
                "graph": graph.model_dump(),
                "changed": result.changed,
                "restored": sorted(undo.get("nodes", {})),
            }

    # ----------------------------------------------------------------- plans

    @staticmethod
    def _plan_state(graph, slug: str):
        for item in graph.plans:
            if item.slug == slug:
                return item
        return None

    def plan_preview(self, data: dict) -> dict:
        with self.lock:
            return preview_plan(data, read_env(self.root))

    def plan_create(self, data: dict) -> dict:
        with self.lock:
            env = read_env(self.root)
            result = create_plan(data, env)
            graph = self.rescan("plan")
            state = self._plan_state(graph, result.plan.slug)
            return {
                "created": [
                    {"path": item.path, "kind": item.kind, "name": item.name}
                    for item in result.created
                ],
                "plan": state.model_dump() if state is not None else None,
                "graph": graph.model_dump(),
            }

    def plan_update(self, data: dict) -> dict:
        with self.lock:
            env = read_env(self.root)
            plan = update_plan(data, env)
            graph = self.rescan("plan")
            state = self._plan_state(graph, plan.slug)
            return {
                "plan": state.model_dump() if state is not None else None,
                "graph": graph.model_dump(),
            }

    def plan_delete(self, data: dict) -> dict:
        with self.lock:
            env = read_env(self.root)
            result = delete_plan(data, env)
            graph = self.rescan("plan")
            return {
                "slug": result.slug,
                "deleted_files": result.deleted_files,
                "kept_files": result.kept_files,
                "removed_nodes": result.removed_nodes,
                "removed_edges": result.removed_edges,
                "graph": graph.model_dump(),
            }

    # ------------------------------------------------------------ decompose

    def decompose_prompt(self, data: dict) -> dict:
        with self.lock:
            raw = data.get("task")
            task = raw.strip() if isinstance(raw, str) else ""
            if not task:
                raise MetadataError("invalid_input", "Нужен текст задачи")
            mode = "improve" if data.get("mode") == "improve" else "new"
            env = read_env(self.root)
            self.ensure_graph()
            return {"prompt": build_decompose_prompt(self.graph, env, task, mode)}

    def decompose_parse(self, data: dict) -> dict:
        """Разобрать ответ LLM по идее: сценарии бэкенда и страницы фронтенда."""
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise MetadataError("invalid_input", "Вставьте ответ LLM")
        try:
            return parse_decompose_answer(text)
        except SyncJsonError as exc:
            raise MetadataError("invalid_input", str(exc)) from exc

    # -------------------------------------------------- шаблонные промпты

    _PROMPT_PATHS = {
        "rules": ".studio/prompts/rules.md",
        "decompose": ".studio/prompts/decompose-prompt.md",
        "decompose-improve": ".studio/prompts/decompose-improve.md",
        "frontend-brief": ".studio/prompts/frontend-brief.md",
        "frontend-report": ".studio/prompts/frontend-report.md",
    }

    # ------------------------------------------------------------- идеи

    def _ideas_path(self) -> Path:
        return Path(self.root) / ".studio" / "ideas.json"

    def load_ideas(self) -> dict:
        """Доска идей проекта; файл может не существовать — доска пустая."""
        try:
            data = json.loads(self._ideas_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": 1, "activeId": "", "ideas": []}
        if not isinstance(data, dict) or not isinstance(data.get("ideas"), list):
            return {"version": 1, "activeId": "", "ideas": []}
        return data

    @staticmethod
    def _board_revision(board: dict) -> int:
        value = board.get("revision") if isinstance(board, dict) else 0
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    def save_ideas(self, data: dict) -> dict:
        """Сохранить доску. Если передан revision, он должен совпадать с серверным.

        Так UI и агент не затирают друг другу записи: проигравший получает 409
        ideas_conflict с актуальной доской в details.board и перечитывает её.
        Без revision запись принимается (совместимость со старыми клиентами).
        """
        if not isinstance(data, dict) or not isinstance(data.get("ideas"), list):
            raise MetadataError("invalid_input", "Ожидалась доска идей с массивом ideas")
        actor = str(data.pop("_actor", "") or "")
        if len(data["ideas"]) > 500:
            raise MetadataError("invalid_input", "Слишком много идей в доске (максимум 500)")
        with self.lock:
            current = self.load_ideas()
            current_revision = self._board_revision(current)
            if "revision" in data and self._board_revision(data) != current_revision:
                error = MetadataError(
                    "ideas_conflict",
                    "Доска идей изменилась с момента загрузки (её правил агент или другая "
                    "вкладка). Перечитайте её: GET /api/ideas",
                )
                error.details = {"board": current}
                raise error
            if actor == "agent":
                incoming = {
                    str(item.get("id")): item for item in data["ideas"] if isinstance(item, dict)
                }
                for old in current.get("ideas") or []:
                    if not isinstance(old, dict) or old.get("created_by"):
                        continue
                    new = incoming.get(str(old.get("id")))
                    same = new is not None and {
                        k: v for k, v in new.items() if k != "created_by"
                    } == {k: v for k, v in old.items() if k != "created_by"}
                    if not same:
                        raise MetadataError(
                            "mode_forbidden",
                            f"Идея «{old.get('title') or old.get('id')}» создана человеком: агент "
                            "не может её удалить или изменить — отправьте её без изменений",
                        )
            known = {
                str(item.get("id")): item
                for item in current.get("ideas") or []
                if isinstance(item, dict)
            }
            ideas = []
            for item in data["ideas"]:
                if isinstance(item, dict):
                    item = dict(item)
                    item.pop("created_by", None)  # метку ставит только сервер
                    old = known.get(str(item.get("id")))
                    if old is not None and old.get("created_by"):
                        item["created_by"] = old["created_by"]
                    elif old is None and actor == "agent":
                        item["created_by"] = "agent"
                ideas.append(item)
            board = {**data, "ideas": ideas, "revision": current_revision + 1}
            path = self._ideas_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(board, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )
            tmp.replace(path)
            return {"ok": True, "count": len(data["ideas"]), "revision": board["revision"]}

    # ------------------------------------------------------------- агент

    def saved_brief(self) -> dict:
        """ТЗ, которое пользователь сохранил в проект: его и должен реализовать агент."""
        from studio.agent.brief import BRIEF_FILENAME

        with self.lock:
            path = Path(self.root) / ".studio" / BRIEF_FILENAME
            if not path.is_file():
                raise BriefNotFound(
                    "Сохранённого ТЗ нет: пользователь должен сохранить его в окне «ТЗ» студии"
                )
            try:
                markdown = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise BriefNotFound(f"Файл ТЗ не читается: {exc}") from exc
            data = load_brief(self.root, path)
            return {
                "markdown": markdown,
                "brief_id": str(data.get("brief_id") or ""),
                "fingerprint": str(data.get("fingerprint") or ""),
                "path": f".studio/{BRIEF_FILENAME}",
                "tasks": data.get("tasks") or [],
            }

    def check_front_links(self, front: dict) -> dict:
        """Сверить объявленные вызовы фронтенда (calls) с эндпоинтами бэкенда."""
        return check_links(front, self.ensure_graph(), read_env(self.root).api_prefix)

    def _briefs_status(self) -> dict:
        """Сколько задач в сохранённых ТЗ бэкенда и фронтенда (None — ТЗ нет)."""
        status: dict = {"backend": None, "frontend": None}
        with contextlib.suppress(BriefNotFound):
            status["backend"] = len(self.saved_brief().get("tasks") or [])
        path = Path(self.root) / ".studio" / FRONT_BRIEF_FILENAME
        if path.is_file():
            with contextlib.suppress(FileNotFoundError):
                brief = load_front_brief(self.root, path.as_posix())
                status["frontend"] = len(brief.get("tasks") or [])
        return status

    def mode_token(self, mode: str) -> str:
        return access.derive_token(self.token, mode)

    def agent_info(self, mode: str) -> dict:
        """Самоописание API для агента: что разрешено в его режиме и как работать."""
        from studio import __version__

        info: dict = {
            "studio_version": __version__,
            "mode": mode,
            "project_root": str(self.root),
            "revision": self.revision,
            "guide": "/api/agent/guide",
            "rules": [
                "Запросы идут с заголовком X-Studio-Token и Content-Type: application/json.",
                "Токен и адрес даёт пользователь; не сохраняйте их в файлах проекта.",
                "Решения принимает пользователь: агент ничего не применяет (apply запрещён).",
                "Файлы проекта вне .studio/ правит только режим implement и только по ТЗ.",
            ],
        }
        if mode == access.FULL:
            info["tokens"] = {m: self.mode_token(m) for m in access.AGENT_MODES}
            info["routes"] = {m: access.routes_for(m) for m in access.AGENT_MODES}
            info["briefs"] = self._briefs_status()
            return info
        info["routes"] = access.routes_for(mode)
        if mode == access.PLAN:
            info["workflow"] = [
                "GET /api/state, /api/graph, /api/frontend/graph, /api/ideas — прочитать проект.",
                "Предложить изменения плановыми узлами (POST /api/metadata/graph-node, "
                "/api/frontend/meta) и идеями (POST /api/ideas с revision).",
                "Файлы кода не писать и /api/decompose, /api/plan не вызывать.",
                "Пользователь сам отметит в UI, что реализовать.",
            ]
        else:
            info["workflow"] = [
                "GET /api/brief/last — ТЗ бэкенда, выбранное пользователем; делать только его "
                "задачи. Для фронтенда — GET /api/frontend/brief/last.",
                "Реализовать задачи в коде, не трогая .env*, .studio/ и конфигурацию.",
                "GET /api/verify?brief=last (фронтенд — GET /api/frontend/verify) — проверить "
                "выполнение; повторять до «выполнено».",
                "Итоговый JSON-отчёт отправить POST /api/agent/report {part, json}: студия "
                "проверит его и положит в «Отчёты агента»; применяет его пользователь в UI.",
            ]
        return info

    # ------------------------------------------------- синхронизация описаний

    def sync_prompt(self, data: dict) -> dict:
        with self.lock:
            graph = self.ensure_graph()
            scope = str(data.get("scope") or "all")
            domain = str(data.get("domain") or "")
            markdown = build_sync_prompt(graph, scope, domain)
            return {"markdown": markdown}

    def report(self, data: dict) -> dict:
        """Отчёт по бэкенду по выбранным разделам; markdown сохраняется в .studio/report.md."""
        with self.lock:
            graph = self.ensure_graph()
            try:
                result = build_report(graph, data.get("sections"))
            except ValueError as exc:
                raise MetadataError("invalid_input", str(exc)) from exc
            try:
                result["path"] = save_report(self.root, result["markdown"])
            except OSError as exc:
                raise MetadataError("not_writable", str(exc)) from exc
            return result

    def sync_apply(self, data: dict) -> dict:
        """Принять JSON-ответ агента: проверка + отличия; запись только при apply=true."""
        with self.lock:
            graph = self.ensure_graph()
            try:
                report = check_sync_response(graph, extract_json(data.get("json")))
            except SyncJsonError as exc:
                raise MetadataError("invalid_input", str(exc)) from exc
            report["applied"] = 0
            report["undo"] = []
            if data.get("apply") is not True:
                return report
            chosen = data.get("ids")
            allowed = {str(item) for item in chosen} if isinstance(chosen, list) else None
            by_id = graph.by_id()
            for row in report["rows"]:
                if row["status"] != "change" or (allowed is not None and row["id"] not in allowed):
                    continue
                node = by_id[row["id"]]
                save_node_metadata(
                    self.root,
                    node,
                    row["after"]["description"],
                    row["after"]["acceptance"],
                    node.domain or "",
                )
                row["status"] = "applied"
                report["applied"] += 1
                report["undo"].append(
                    {"id": row["id"], "before": row["before"], "after": row["after"]}
                )
            if report["applied"]:
                report["graph"] = self.rescan("sync").model_dump()
            return report

    def sync_undo(self, data: dict) -> dict:
        """Вернуть описания, записанные «Применить». Узел, который успели
        изменить после применения, не трогается — он попадает в skipped."""
        with self.lock:
            graph = self.ensure_graph()
            try:
                entries = check_undo_entries(data.get("undo"))
            except SyncJsonError as exc:
                raise MetadataError("invalid_input", str(exc)) from exc
            by_id = graph.by_id()
            restored, skipped = 0, []
            for entry in entries:
                node = by_id.get(entry["id"])
                if node is None or node.planned:
                    skipped.append(entry["id"])
                    continue
                current = {"description": node.description, "acceptance": node.acceptance}
                if not same_state(current, entry["after"]):
                    skipped.append(entry["id"])
                    continue
                save_node_metadata(
                    self.root,
                    node,
                    entry["before"]["description"],
                    entry["before"]["acceptance"],
                    node.domain or "",
                )
                restored += 1
            result = {"restored": restored, "skipped": skipped}
            if restored:
                result["graph"] = self.rescan("sync-undo").model_dump()
            return result

    def prompts_state(self) -> dict:
        root = Path(self.root)
        states = {
            "rules": (load_rules(root), standard_rules(), rules_override_path(root)),
            "decompose": (
                load_decompose_template(root, "new"),
                standard_decompose_template("new"),
                decompose_override_path(root, "new"),
            ),
            "decompose-improve": (
                load_decompose_template(root, "improve"),
                standard_decompose_template("improve"),
                decompose_override_path(root, "improve"),
            ),
            "frontend-brief": front_prompt_state(root, "frontend-brief"),
            "frontend-report": front_prompt_state(root, "frontend-report"),
        }
        return {
            kind: {
                "current": current,
                "standard": standard,
                "overridden": bool(override and override.is_file()),
            }
            for kind, (current, standard, override) in states.items()
        }

    def save_prompt(self, data: dict) -> dict:
        kind = str(data.get("kind") or "")
        if kind not in self._PROMPT_PATHS:
            raise MetadataError("invalid_input", "Неизвестный шаблон промпта")
        target = Path(self.root) / self._PROMPT_PATHS[kind]
        if data.get("reset"):
            target.unlink(missing_ok=True)
        else:
            text = str(data.get("text") or "")
            if not text.strip():
                raise MetadataError("invalid_input", "Текст шаблона пуст")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        return self.prompts_state()

    def decompose_apply(self, data: dict) -> dict:
        with self.lock:
            scenarios = data.get("scenarios")
            if not isinstance(scenarios, list):
                raise MetadataError("invalid_input", "Нужен массив scenarios")
            if not scenarios:
                raise MetadataError("invalid_input", "Пустой список сценариев")
            env = read_env(self.root)
            created = []
            plan_slugs = []
            for index, item in enumerate(scenarios):
                if not isinstance(item, dict):
                    raise CreateError(
                        f"scenarios[{index}] должен быть объектом", "W_PLAN_INVALID"
                    )
                try:
                    result = create_plan(item, env)
                except CreateError as exc:
                    self.rescan("decompose")
                    suffix = ""
                    if plan_slugs:
                        created_count = len(plan_slugs)
                        total = len(scenarios)
                        slugs = ", ".join(plan_slugs)
                        suffix = f"; создано {created_count} из {total} ({slugs})"
                    raise CreateError(str(exc) + suffix, exc.code) from exc
                created.extend(
                    {"path": entry.path, "kind": entry.kind, "name": entry.name}
                    for entry in result.created
                )
                plan_slugs.append(result.plan.slug)
            graph = self.rescan("decompose")
            return {"created": created, "plans": plan_slugs, "graph": graph.model_dump()}

    # -------------------------------------------------------------- agent

    @staticmethod
    def _brief_options(data: dict) -> BriefOptions:
        scope = str(data.get("scope") or "")
        only = None
        if scope not in ("all", "scenario") and "only" in data:
            raw_only = data.get("only") or []
            if isinstance(raw_only, str):
                raw_only = [raw_only]
            only = tuple(str(item) for item in raw_only if str(item).strip())
        raw_plans = data.get("plans")
        if raw_plans is None:
            plans = None
        else:
            if isinstance(raw_plans, str):
                raw_plans = [raw_plans]
            plans = tuple(str(item) for item in raw_plans if str(item).strip())
        raw_sections = data.get("sections")
        if raw_sections is not None and not isinstance(raw_sections, (list, tuple)):
            raise MetadataError("invalid_input", "Разделы нужно передать списком")
        return BriefOptions(
            map_mode=str(data.get("map") or ""),
            only=only,
            plans=plans,
            scenario=str(data.get("scenario") or ""),
            include_empty=bool(data.get("include_empty")),
            sections=None if raw_sections is None else tuple(str(i) for i in raw_sections),
        )

    def _collect_brief(self, data: dict):
        sources = load_sources(read_env(self.root))
        return build_brief(self.ensure_graph(), sources, self._brief_options(data))

    def brief(self, data: dict) -> dict:
        with self.lock:
            brief = self._collect_brief(data)
            save_brief_sidecars(self.root, brief)
            return {
                "markdown": brief.markdown,
                "brief_id": brief.brief_id,
                "fingerprint": brief.fingerprint,
                "stats": brief.stats,
                "warnings": brief.warnings,
                "tasks": [
                    {
                        "number": item["number"],
                        "kind": item.get("kind") or item.get("type", ""),
                        "anchor": item.get("anchor")
                        or item.get("slug")
                        or item.get("title", ""),
                        "target_file": item.get("target_file", ""),
                    }
                    for item in brief.tasks
                ],
                "unready": brief.unready,
            }

    def save_brief(self, data: dict) -> dict:
        with self.lock:
            brief = self._collect_brief(data)
            edited = data.get("markdown")
            if edited is not None:
                brief = self._with_edited_markdown(brief, edited)
            path = save_brief_file(self.root, brief)
            return {
                "path": path.as_posix(),
                "brief_id": brief.brief_id,
                "stats": brief.stats,
            }

    @staticmethod
    def _with_edited_markdown(brief, edited):
        """Текст ТЗ с правками пользователя; служебный маркер должен остаться на месте."""
        if not isinstance(edited, str) or not edited.strip():
            raise MetadataError("invalid_input", "Текст ТЗ пуст")
        if "<!-- studio-brief:" not in edited or brief.brief_id not in edited:
            raise MetadataError(
                "invalid_input",
                "В тексте пропал служебный маркер ТЗ (<!-- studio-brief: … -->) — без него "
                "studio verify не найдёт задачи. Верните маркер или сохраните без правок.",
            )
        return replace(brief, markdown=edited.rstrip() + "\n")

    def verify_brief(self, target: str | None) -> dict:
        with self.lock:
            data = load_brief(self.root, target)
            return verify_brief_data(self.ensure_graph(), data)

    # -------------------------------------------------------------- long-poll

    def wait_for_change(self, rev: int, timeout: float = 25, aux: int | None = None) -> int:
        """Ждать смены ревизии (а если передан aux — и счётчика frontend/идей)."""
        with self.lock:

            def changed() -> bool:
                return self.revision > rev or (aux is not None and self.aux > aux)

            if not changed():
                self._cond.wait_for(changed, timeout=timeout)
            return self.revision

    def _aux_stamp_now(self) -> str:
        digest = hashlib.sha1()
        for name in ("frontend.json", "ideas.json"):
            try:
                digest.update((self.root / ".studio" / name).read_bytes())
            except OSError:
                digest.update(b"-")
            digest.update(b"|")
        try:  # входящие отчёты агента: появился, принят или отклонён
            for path in sorted(inbox.reports_dir(self.root).glob("r-*.json")):
                info = path.stat()
                digest.update(f"{path.name}:{info.st_mtime_ns}:{info.st_size}|".encode())
        except OSError:
            digest.update(b"-")
        return digest.hexdigest()

    def check_aux(self) -> bool:
        """Сравнить подпись frontend.json и ideas.json; при изменении поднять aux."""
        stamp = self._aux_stamp_now()
        with self.lock:
            first = self._aux_stamp is None
            changed = not first and stamp != self._aux_stamp
            self._aux_stamp = stamp
            if changed:
                self.aux += 1
                self._cond.notify_all()
            return changed

    def snapshot(self) -> dict:
        with self.lock:
            graph = self.graph
            env = read_env(self.root)
            nodes = graph.nodes if graph is not None else []
            counts = {
                "endpoints": sum(1 for n in nodes if n.kind == "endpoint"),
                "scenarios": sum(1 for n in nodes if n.kind == "scenario"),
                "io": sum(1 for n in nodes if n.kind == "io"),
                "models": sum(1 for n in nodes if n.kind == "model"),
                "tests": sum(1 for n in nodes if n.kind == "test"),
                "nested": sum(1 for n in nodes if n.kind == "nested"),
                "diagnostics": len(graph.diagnostics) if graph is not None else 0,
            }
            from studio import __version__

            return {
                "version": __version__,
                "revision": self.revision,
                "fingerprint": graph.fingerprint if graph is not None else "",
                "generated_at": graph.generated_at if graph is not None else "",
                "scanning": self.scanning,
                "project_root": self.root.as_posix(),
                "env": {
                    "routes_package": env.routes_package,
                    "core_discover": env.core_discover,
                    "api_prefix": env.api_prefix,
                    "models": env.models_spec
                    or ", ".join(path.name for path in env.models_paths),
                    "counts": counts,
                },
            }

    # ---------------------------------------------------------------- watcher

    def start_watcher(self) -> None:
        if self._watcher is not None and self._watcher.is_alive():
            return
        self._stop.clear()
        self.check_aux()
        try:
            self._watch_last = stat_fingerprint(read_env(self.root))
        except Exception:  # noqa: BLE001 — watcher не должен мешать старту
            self._watch_last = None
        self._watcher = threading.Thread(
            target=self._watch_loop, name="studio-watcher", daemon=True
        )
        self._watcher.start()

    def stop_watcher(self) -> None:
        self._stop.set()
        watcher = self._watcher
        if watcher is not None and watcher.is_alive():
            watcher.join(timeout=5)
        self._watcher = None

    def _watch_loop(self) -> None:
        interval = max(0.02, float(self.poll_interval or DEFAULT_POLL))
        while not self._stop.is_set():
            try:
                self.check_aux()
                fingerprint = stat_fingerprint(read_env(self.root))
                if self._watch_last is not None and fingerprint != self._watch_last:
                    stable = fingerprint
                    while not self._stop.is_set():
                        time.sleep(DEBOUNCE)
                        fresh = stat_fingerprint(read_env(self.root))
                        if fresh == stable:
                            break
                        stable = fresh
                    if self._stop.is_set():
                        break
                    self._watch_last = stable
                    self.rescan("watch")
                else:
                    self._watch_last = fingerprint
            except Exception as exc:  # noqa: BLE001 — поток не должен умирать
                slog.step(f"watcher: {type(exc).__name__}: {exc}")
            self._stop.wait(interval)


__all__ = [
    "DEFAULT_POLL",
    "NodeNotFound",
    "ProjectState",
    "CreateError",
]

