"""Сервер интерфейса студии: stdlib http.server.

При старте — автоматический скан проекта, затем интерфейс (§2).
Изменяющие операции пишут только метаданные (комментарии `# studio: ...`).
"""

from __future__ import annotations

import functools
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from studio import log as slog
from studio import store
from studio.creator import CreateError, create_metadata_files
from studio.env import read_env
from studio.metadata import MetadataError, save_node_metadata
from studio.metamodel import Graph
from studio.scanner import scan
from studio.ui import INDEX

MAX_BODY = 16 * 1024 * 1024


class Ctx:
    def __init__(self, project_root: Path):
        self.root = Path(project_root)


_write_lock = threading.Lock()


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _write_lock:
            return fn(*args, **kwargs)

    return wrapper


def load_graph(project_root: Path) -> Graph:
    graph = store.read_graph(project_root)
    if graph is not None:
        return graph
    graph = scan(project_root)
    store.write_graph(project_root, graph)
    return graph


def _scan_and_save(project_root: Path) -> Graph:
    graph = scan(project_root)
    store.write_graph(project_root, graph)
    return graph


def _send_json(handler: BaseHTTPRequestHandler, obj, code: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_text(
    handler: BaseHTTPRequestHandler,
    text: str,
    ctype: str = "text/plain; charset=utf-8",
    code: int = 200,
) -> None:
    body = text.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0 or length > MAX_BODY:
        return {}
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_node(graph: Graph, node_id: str):
    node = graph.by_id().get(node_id)
    if node is not None:
        return node
    if not node_id.startswith("io:") or "._db." not in node_id:
        return None
    try:
        payload = node_id[len("io:"):]
        file_path, anchor = payload.split(":", 1)
        prefix, line_text = anchor.rsplit(":", 1)
        scenario_class, method = prefix.split("._db.", 1)
        wanted_line = int(line_text)
    except (ValueError, IndexError):
        return None
    candidates = [
        n
        for n in graph.nodes
        if n.kind == "io"
        and n.file_path == file_path
        and (n.details or {}).get("scenario_class") == scenario_class
        and (n.details or {}).get("method") == method
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda n: abs(n.line - wanted_line))


@_locked
def _save_metadata(project_root: Path, data: dict) -> Graph:
    node_id = str(data.get("node_id") or "")
    if not node_id:
        raise MetadataError("нужен node_id")
    # Маркеры в коде — истина на момент открытия формы и сохранения (§9).
    graph = scan(project_root)
    node = _resolve_node(graph, node_id)
    if node is None:
        raise MetadataError("узел не найден — обновите карту из кода")
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
        raise MetadataError("описание должно быть строкой")
    if isinstance(acceptance, str):
        acceptance_list = [line.strip() for line in acceptance.splitlines() if line.strip()]
    elif isinstance(acceptance, list):
        acceptance_list = [str(item) for item in acceptance if str(item).strip()]
    else:
        raise MetadataError("критерии приёмки должны быть списком строк")
    if not isinstance(domain, str):
        raise MetadataError("домен должен быть строкой")
    if node.kind != "endpoint":
        domain = ""
    save_node_metadata(project_root, node, description, acceptance_list, domain)
    return _scan_and_save(project_root)


@_locked
def _create(project_root: Path, data: dict) -> dict:
    env = read_env(project_root)
    created = create_metadata_files(data, env)
    graph = _scan_and_save(project_root)
    return {
        "created": [
            {"path": item.path, "kind": item.kind, "name": item.name} for item in created
        ],
        "graph": graph.model_dump(),
    }


def make_handler(ctx: Ctx):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args) -> None:
            slog.step("HTTP " + fmt % args)

        def _guard(self, action) -> None:
            try:
                action()
            except Exception as exc:  # noqa: BLE001 — граница HTTP-запроса
                slog.step(f"HTTP 500: {type(exc).__name__}: {exc}")
                try:
                    _send_text(self, f"internal error: {type(exc).__name__}: {exc}", code=500)
                except OSError:
                    pass

        def do_GET(self) -> None:
            self._guard(self._get)

        def do_POST(self) -> None:
            self._guard(self._post)

        def do_PUT(self) -> None:
            self._not_found()

        def do_DELETE(self) -> None:
            self._not_found()

        def do_PATCH(self) -> None:
            self._not_found()

        def _get(self) -> None:
            root = ctx.root
            url = urllib.parse.urlsplit(self.path)
            if url.path in ("/", "/index.html"):
                try:
                    html = INDEX.read_text(encoding="utf-8")
                except OSError:
                    return _send_text(self, "ui index.html not found", code=500)
                return _send_text(self, html, "text/html; charset=utf-8")
            if url.path == "/api/graph":
                return _send_json(self, load_graph(root).model_dump())
            return _send_text(self, "not found", code=404)

        def _post(self) -> None:
            root = ctx.root
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/api/rescan":
                slog.step("rescan")
                graph = _scan_and_save(root)
                return _send_json(self, graph.model_dump())
            if url.path == "/api/metadata/node":
                data = _read_body(self)
                try:
                    graph = _save_metadata(root, data)
                except MetadataError as exc:
                    return _send_text(self, str(exc), code=409)
                return _send_json(self, graph.model_dump())
            if url.path == "/api/create":
                data = _read_body(self)
                try:
                    return _send_json(self, _create(root, data))
                except CreateError as exc:
                    return _send_text(self, str(exc), code=409)
            self._not_found()

        def _not_found(self) -> None:
            _send_text(self, "not found", code=404)

    return Handler


def serve(project_root: Path, host: str = "127.0.0.1", port: int = 8777) -> None:
    root = Path(project_root).resolve()
    store.ensure_layout(root)
    if not slog.is_active(root):
        slog.start_run(root, f"dev {host}:{port}")
    # §2: при запуске пакет автоматически проводит скан и только потом UI.
    slog.step("автоматический скан перед запуском интерфейса")
    graph = _scan_and_save(root)
    slog.step(f"скан завершён: узлов={len(graph.nodes)} связей={len(graph.edges)}")
    server = ThreadingHTTPServer((host, port), make_handler(Ctx(root)))
    print(f"studio dev -> http://{host}:{port}  (project: {root})")
    print("Studio: карта пути HTTP-запроса.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
