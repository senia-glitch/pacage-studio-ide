"""Единое состояние студии: граф в памяти, ревизии, watcher, метаданные."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
import uuid
from pathlib import Path

from studio import log as slog
from studio import store
from studio.env import read_env
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
from studio.metadata.facts import finish_plans
from studio.metadata.plans import PlanError, build_plan, layer_file, layer_name
from studio.metamodel import EDGE_KINDS, NODE_KINDS, Graph, Node
from studio.scanner import scan
from studio.scanner.anchors import AnchorResolver
from studio.server.devservers import DevServers
from studio.server.pipeline import PipelineService
from studio.server.tester import TestRunner
from studio.source import stat_fingerprint

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
        # Второй счётчик: правки frontend.json (их пишут и снаружи). Скан кода
        # они не требуют, но интерфейс должен увидеть их без перезагрузки вкладки.
        self.aux = 0
        self._aux_stamp: str | None = None
        # Независимая проверка тестами проекта (кнопка или этап «Отчёт»).
        self.tester = TestRunner(self.root, lambda: self.graph)
        # Серверы самого проекта (бэкенд, фронтенд) по кнопке; останавливаются вместе со студией.
        self.devservers = DevServers(self.root)
        # Конвейер v3: банк знаний, этапы, ворота (маршруты /api/pipeline/*, только человек).
        self.pipeline = PipelineService(self.root, on_reset=self.devservers.stop_all)

    # ------------------------------------------------------------------ scan

    def rescan(self, reason: str) -> Graph:
        with self.lock:
            if self.scanning and self.graph is not None:
                return self.graph
            self.scanning = True
            try:
                graph = scan(self.root)
                graph = self._finish_plans(graph)
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

    def _finish_plans(self, graph: Graph) -> Graph:
        """План → факт: найденный в коде план закрывается сам, граф пересобирается."""
        try:
            closed = finish_plans(self.root, graph)
        except Exception as exc:  # noqa: BLE001 — сбой автоматики не должен ломать скан
            slog.step(f"план→факт не выполнен: {exc}")
            return graph
        if not closed:
            return graph
        slog.step("план→факт: " + ", ".join(item["slug"] for item in closed))
        return scan(self.root)

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
            # Имя узла в коде берётся из slug (латиница), а не из заголовка карточки.
            title = name
            slug = str(data.get("slug") or "").strip()
            slug_target = ""
            if slug and kind in ("endpoint", "scenario"):
                try:
                    plan = build_plan(title, slug, "", [], "", "", "", False, False,
                                      read_env(self.root))
                except PlanError as exc:
                    raise MetadataError("invalid_input", str(exc)) from exc
                name, slug_target = layer_name(plan, kind), layer_file(plan, kind)
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
            if slug_target:
                details["slug"] = slug
                details["title"] = title
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
                or slug_target
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






    # ------------------------------------------------------------ decompose



    # -------------------------------------------------- шаблонные промпты

    _PROMPT_PATHS = {
        "rules": ".studio/prompts/rules.md",
        "decompose": ".studio/prompts/decompose-prompt.md",
        "decompose-improve": ".studio/prompts/decompose-improve.md",
        "frontend-brief": ".studio/prompts/frontend-brief.md",
        "frontend-report": ".studio/prompts/frontend-report.md",
    }

    # ------------------------------------------------------------- идеи







    # ------------------------------------------------------------- агент


    def check_front_links(self, front: dict) -> dict:
        """Сверить объявленные вызовы фронтенда (calls) с эндпоинтами бэкенда."""
        return check_links(front, self.ensure_graph(), read_env(self.root).api_prefix)




    # ------------------------------------------------- синхронизация описаний








    # -------------------------------------------------------------- agent







    # -------------------------------------------------------------- long-poll

    def wait_for_change(self, rev: int, timeout: float = 25, aux: int | None = None) -> int:
        """Ждать смены ревизии (а если передан aux — и счётчика frontend)."""
        with self.lock:

            def changed() -> bool:
                return self.revision > rev or (aux is not None and self.aux > aux)

            if not changed():
                self._cond.wait_for(changed, timeout=timeout)
            return self.revision

    def _aux_stamp_now(self) -> str:
        digest = hashlib.sha1()
        try:
            digest.update((self.root / ".studio" / "frontend.json").read_bytes())
        except OSError:
            digest.update(b"-")
        return digest.hexdigest()

    def check_aux(self) -> bool:
        """Сравнить подпись frontend.json; при изменении поднять aux."""
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
]

