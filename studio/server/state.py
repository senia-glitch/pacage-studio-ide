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
from studio.agent import (
    BriefOptions,
    build_brief,
    build_decompose_prompt,
    load_brief,
    save_brief_sidecars,
)
from studio.agent import (
    save_brief as save_brief_file,
)
from studio.agent import (
    verify_brief as verify_brief_data,
)
from studio.creator import CreateError
from studio.creator.plan import create_plan, delete_plan, preview_plan, update_plan
from studio.env import read_env
from studio.metadata import (
    MetadataError,
    delete_graph_node,
    metadata_path,
    read_metadata,
    save_graph_node,
    save_node_metadata,
)
from studio.metamodel import EDGE_KINDS, NODE_KINDS, Graph, Node
from studio.scanner import scan
from studio.scanner.anchors import AnchorResolver
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

    # ------------------------------------------------------------------ scan

    def rescan(self, reason: str) -> Graph:
        with self.lock:
            if self.scanning and self.graph is not None:
                return self.graph
            self.scanning = True
            try:
                graph = scan(self.root)
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
            result = delete_graph_node(self.root, node_id, removable)
            graph = self.rescan("graph_node_delete")
            return {
                "graph": graph.model_dump(),
                "changed": result.changed,
                "removed": sorted(removable),
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
            env = read_env(self.root)
            self.ensure_graph()
            return {"prompt": build_decompose_prompt(self.graph, env, task)}

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
        return BriefOptions(
            map_mode=str(data.get("map") or ""),
            only=only,
            plans=plans,
            scenario=str(data.get("scenario") or ""),
            include_empty=bool(data.get("include_empty")),
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
            path = save_brief_file(self.root, brief)
            return {
                "path": path.as_posix(),
                "brief_id": brief.brief_id,
                "stats": brief.stats,
            }

    def verify_brief(self, target: str | None) -> dict:
        with self.lock:
            data = load_brief(self.root, target)
            return verify_brief_data(self.ensure_graph(), data)

    # -------------------------------------------------------------- long-poll

    def wait_for_change(self, rev: int, timeout: float = 25) -> int:
        with self.lock:
            if self.revision > rev:
                return self.revision
            self._cond.wait_for(lambda: self.revision > rev, timeout=timeout)
            return self.revision

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

