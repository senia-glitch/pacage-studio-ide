"""Сервер интерфейса студии: stdlib http.server.

GET — просмотр графа. PUT/DELETE — патчи плана. POST — rescan, scaffold.
Изменяющие эндпоинты пишут только в `.studio/*` и файлы-скелеты (с маркером).
"""

from __future__ import annotations

import datetime as _dt
import difflib
import functools
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from studio import log as slog
from studio import store
from studio.exporter import to_json, to_markdown
from studio.metadata import MetadataOp, apply_metadata, plan_metadata, render_after
from studio.metamodel import Graph
from studio.scaffold import (
    ScaffoldOp,
    _safe_target,
    apply_scaffold,
    collect_model_appends,
    plan_scaffold,
)
from studio.scanner import build_scenario_paths, scan
from studio.store.plan import (
    Plan,
    PlanPatch,
    PlanCorruptedError,
    apply_plan,
    load_plan_for_write,
    remove_node_edges,
    remove_patch,
    rename_node_refs,
    upsert_patch,
    write_plan,
)
from studio.ui import INDEX


class Ctx:
    def __init__(self, project_root: Path):
        self.root = Path(project_root)


# plan.json читается-изменяется-пишется; сервер многопоточный, поэтому операции
# с планом сериализуются, иначе параллельные запросы теряли бы патчи.
_plan_lock = threading.Lock()


def _locked(fn):
    """Сериализовать изменяющую операцию (блокировка снимается и при исключении)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _plan_lock:
            return fn(*args, **kwargs)

    return wrapper


def load_graph(project_root: Path) -> Graph:
    """Граф из .studio/graph.json; если файла нет или версия старая — scan на месте."""
    graph = store.read_graph(project_root)
    if graph is not None:
        return graph
    slog.step("graph.json отсутствует или версия != 4 — сканирую проект")
    graph = scan(project_root)
    store.write_graph(project_root, graph)
    slog.step(f"граф построен: узлов={len(graph.nodes)} связей={len(graph.edges)}")
    return graph


def reload_graph(project_root: Path) -> Graph:
    """Перечитать graph.json; если нет — scan."""
    graph = store.read_graph(project_root)
    if graph is None:
        return load_graph(project_root)
    return graph


def save_graph(project_root: Path, graph: Graph) -> None:
    store.write_graph(project_root, graph)


def scenarios_payload(graph: Graph) -> dict:
    """Сокращённый список сценариев для левой панели UI."""
    nodes = graph.by_id()
    out = []
    for scenario_id in graph.scenarios:
        path = graph.scenario_paths.get(scenario_id)
        if path is None:
            continue
        endpoints = []
        if path.endpoint and path.endpoint in nodes:
            details = nodes[path.endpoint].details
            endpoints.append(
                {
                    "id": path.endpoint,
                    "method": details.get("http_method", ""),
                    "path": details.get("path", ""),
                    "label": nodes[path.endpoint].label,
                }
            )
        out.append(
            {
                "id": scenario_id,
                "name": nodes[scenario_id].label if scenario_id in nodes else scenario_id,
                "endpoints": endpoints,
                "models": list(path.models),
            }
        )
    return {"scenarios": out}


def _send_json(handler: BaseHTTPRequestHandler, obj, code: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_text(
    handler: BaseHTTPRequestHandler, text: str, ctype: str = "text/plain; charset=utf-8", code: int = 200
) -> None:
    body = text.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_file(root: Path, rel: str) -> str:
    """Содержимое файла внутри проекта; выход за границы и мусор — пустая строка."""
    if not rel:
        return ""
    try:
        target = (root / rel).resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError):
        return ""
    if target != root_resolved and root_resolved not in target.parents:
        return ""
    if not target.is_file():
        return ""
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _op_to_dict(op: ScaffoldOp) -> dict:
    return {"action": op.action, "path": op.path, "node_id": op.node_id, "reason": op.reason}


class PlanError(Exception):
    """Операция плана отклонена (понятное пользователю сообщение, ответ 409)."""


def _reassign_io(plan: Plan, io_id: str, scenario_id: str) -> None:
    """Связь «сценарий → io» переносит io-узел плана на этот сценарий (called_from)."""
    for patch in plan.patches:
        node = patch.node
        if patch.is_edge or node is None or node.id != io_id or node.kind != "io":
            continue
        node.details = {**node.details, "called_from": scenario_id}
        return
    raise PlanError(
        "io-узел создан из кода и привязан к своему сценарию в коде — переназначить нельзя"
    )


@_locked
def _plan_patch(project_root: Path, patch: PlanPatch) -> Graph:
    try:
        plan = load_plan_for_write(project_root)
    except PlanCorruptedError as exc:
        slog.step(f"plan.json повреждён: {exc}")
        raise PlanError(str(exc)) from exc
    renamed = patch.node is not None and not patch.is_edge and patch.node.id != patch.target_id
    if renamed:
        rename_node_refs(plan, patch.target_id, patch.node.id)
        patch = patch.model_copy(update={"target_id": patch.node.id})
    if patch.is_edge and patch.edge is not None and patch.edge.kind == "port_call":
        _reassign_io(plan, patch.edge.to_node, patch.edge.from_node)
    upsert_patch(plan, patch)
    plan.meta.last_edit = _dt.datetime.now().isoformat(timespec="seconds")
    write_plan(project_root, plan)
    if renamed:
        # Переименование меняет id узлов кода: граф пересобираем из кода + плана,
        # иначе в graph.json остались бы старый узел и рёбра на него.
        graph = scan(project_root)
    else:
        graph = reload_graph(project_root)
        graph = apply_plan(graph, plan)
    build_scenario_paths(graph)
    save_graph(project_root, graph)
    return graph


@_locked
def _remove_patch(project_root: Path, target_id: str, is_edge: bool) -> Graph:
    plan = load_plan_for_write(project_root)
    remove_patch(plan, target_id, is_edge)
    if not is_edge:
        remove_node_edges(plan, target_id)
    plan.meta.last_edit = _dt.datetime.now().isoformat(timespec="seconds")
    write_plan(project_root, plan)
    # Пересборка из кода + остатшегося плана: apply_plan только добавляет,
    # поэтому без нового скана удалённый узел/ребро остались бы в graph.json.
    graph = scan(project_root)
    save_graph(project_root, graph)
    return graph


def _scaffold_preview(project_root: Path) -> dict:
    graph = reload_graph(project_root)
    ops = plan_scaffold(graph, project_root)
    models = collect_model_appends(graph)
    return {
        "ops": [_op_to_dict(op) for op in ops],
        "models": [{"node_id": m[0], "class_name": m[1], "tablename": m[2]} for m in models],
    }


@_locked
def _scaffold_apply(project_root: Path) -> dict:
    graph = reload_graph(project_root)
    ops = plan_scaffold(graph, project_root)
    apply_scaffold(ops, graph, project_root)
    # Файлы-скелеты теперь в коде — граф пересобирается, иначе свежие скелеты
    # не видны до ручного rescan (metadata сообщает «кода ещё нет»).
    graph = scan(project_root)
    save_graph(project_root, graph)
    created = sum(1 for op in ops if op.action == "create")
    updated = sum(1 for op in ops if op.action == "update_docstring")
    skipped = sum(1 for op in ops if op.action.startswith("skip"))
    return {"created": created, "updated": updated, "skipped": skipped}


def _metadata_op_to_dict(op: MetadataOp) -> dict:
    return {"action": op.action, "path": op.path, "node_id": op.node_id, "reason": op.reason}


def _simulate_apply(file_ops: list[MetadataOp], graph: Graph, root: Path, before: str) -> str:
    nodes_by_id = graph.by_id()
    nodes = [nodes_by_id[op.node_id] for op in file_ops if op.node_id in nodes_by_id]
    return render_after(nodes, before)


def _build_preview_diff(ops: list[MetadataOp], graph: Graph, root: Path) -> dict[str, str]:
    by_file: dict[str, list[MetadataOp]] = {}
    for op in ops:
        if op.action.startswith("skip"):
            continue
        by_file.setdefault(op.path, []).append(op)
    result: dict[str, str] = {}
    for path, file_ops in by_file.items():
        target = _safe_target(root, path)
        if target is None or not target.is_file():
            continue
        try:
            before = target.read_text(encoding="utf-8")
        except OSError:
            continue
        after = _simulate_apply(file_ops, graph, root, before)
        if after == before:
            continue
        diff_lines = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{path} (до)",
            tofile=f"{path} (после)",
        )
        result[path] = "".join(diff_lines)
    return result


def _metadata_preview(project_root: Path) -> dict:
    graph = reload_graph(project_root)
    ops = plan_metadata(graph, project_root)
    diff = _build_preview_diff(ops, graph, project_root)
    return {"ops": [_metadata_op_to_dict(op) for op in ops], "diff": diff}


@_locked
def _metadata_apply(project_root: Path) -> dict:
    graph = reload_graph(project_root)
    ops = plan_metadata(graph, project_root)
    apply_metadata(ops, graph, project_root)
    graph = scan(project_root)
    store.write_graph(project_root, graph)
    written = sum(1 for op in ops if op.action.startswith("write"))
    updated = sum(1 for op in ops if op.action.startswith("update"))
    deleted = sum(1 for op in ops if op.action.startswith("delete"))
    skipped = sum(1 for op in ops if op.action.startswith("skip"))
    return {"written": written, "updated": updated, "deleted": deleted, "skipped": skipped}


def make_handler(ctx: Ctx):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args) -> None:
            slog.step("HTTP " + fmt % args)

        def _guard(self, action) -> None:
            """Исключение в обработчике → ответ 500, а не обрыв соединения."""
            try:
                action()
            except Exception as exc:  # noqa: BLE001 — граница HTTP-запроса
                slog.step(f"HTTP 500: {type(exc).__name__}: {exc}")
                try:
                    _send_text(self, f"internal error: {type(exc).__name__}: {exc}", code=500)
                except OSError:
                    pass  # клиент уже отключился, ответить некому

        def do_GET(self) -> None:
            self._guard(self._get)

        def do_PUT(self) -> None:
            self._guard(self._put)

        def do_DELETE(self) -> None:
            self._guard(self._delete)

        def do_POST(self) -> None:
            self._guard(self._post)

        def do_PATCH(self) -> None:
            self._not_found()

        def _get(self) -> None:
            root = ctx.root
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                try:
                    html = INDEX.read_text(encoding="utf-8")
                except OSError:
                    return _send_text(self, "ui index.html not found", code=500)
                return _send_text(self, html, "text/html; charset=utf-8")
            if url.path == "/api/graph":
                return _send_json(self, load_graph(root).model_dump())
            if url.path == "/api/scenarios":
                return _send_json(self, scenarios_payload(load_graph(root)))
            if url.path == "/api/file":
                rel = (query.get("path") or [""])[0]
                return _send_json(self, {"path": rel, "code": _read_file(root, rel)})
            if url.path == "/api/export":
                fmt = (query.get("format") or ["md"])[0]
                graph = load_graph(root)
                if fmt == "json":
                    return _send_text(self, to_json(graph), "application/json; charset=utf-8")
                return _send_text(self, to_markdown(graph), "text/markdown; charset=utf-8")
            return _send_text(self, "not found", code=404)

        def _put(self) -> None:
            root = ctx.root
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/api/plan/patch":
                data = _read_body(self)
                try:
                    patch = PlanPatch.model_validate(data)
                except Exception:
                    return _send_text(self, "invalid patch", code=400)
                try:
                    graph = _plan_patch(root, patch)
                except PlanError as exc:
                    return _send_text(self, str(exc), code=409)
                return _send_json(self, graph.model_dump())
            self._not_found()

        def _delete(self) -> None:
            root = ctx.root
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            if url.path == "/api/plan/patch":
                target_id = (query.get("target_id") or [""])[0]
                is_edge = (query.get("is_edge") or ["0"])[0] == "1"
                if not target_id:
                    return _send_text(self, "target_id required", code=400)
                graph = _remove_patch(root, target_id, is_edge)
                return _send_json(self, graph.model_dump())
            self._not_found()

        def _post(self) -> None:
            root = ctx.root
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/api/rescan":
                slog.step("rescan")
                graph = scan(root)
                store.write_graph(root, graph)
                slog.step(f"граф обновлён: узлов={len(graph.nodes)} связей={len(graph.edges)}")
                return _send_json(self, graph.model_dump())
            if url.path == "/api/scaffold/preview":
                return _send_json(self, _scaffold_preview(root))
            if url.path == "/api/scaffold/apply":
                result = _scaffold_apply(root)
                return _send_json(self, result)
            if url.path == "/api/metadata/preview":
                return _send_json(self, _metadata_preview(root))
            if url.path == "/api/metadata/apply":
                return _send_json(self, _metadata_apply(root))
            self._not_found()

        def _not_found(self) -> None:
            _send_text(self, "not found", code=404)

    return H


def serve(project_root: Path, host: str = "127.0.0.1", port: int = 8777) -> None:
    store.ensure_layout(project_root)
    if not slog.is_active(project_root):
        # cmd_dev уже открыл лог — не затираем его шаги (авто-scan, проект).
        slog.start_run(project_root, f"dev {host}:{port}")
    slog.step(f"сервер слушает http://{host}:{port}")
    server = ThreadingHTTPServer((host, port), make_handler(Ctx(project_root)))
    print(f"studio dev -> http://{host}:{port}  (project: {project_root})")
    print("Studio: карта проекта для AI-разработки.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
