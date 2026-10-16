"""Read-only сервер интерфейса: stdlib http.server, только GET.

Ни одной POST/PUT/DELETE: студия только смотрит на код, ничего не пишет,
кроме `.studio/graph.json` и `.studio/studio.log`.
"""

from __future__ import annotations

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from studio import log as slog
from studio import store
from studio.metamodel import Graph
from studio.scanner import scan
from studio.ui import INDEX


class Ctx:
    def __init__(self, project_root: Path):
        self.root = Path(project_root)


def load_graph(project_root: Path) -> Graph:
    """Граф из .studio/graph.json; если файла нет или версия старая — scan на месте."""
    graph = store.read_graph(project_root)
    if graph is not None:
        return graph
    slog.step("graph.json отсутствует или версия != 2 — сканирую проект")
    graph = scan(project_root)
    store.write_graph(project_root, graph)
    slog.step(f"граф построен: узлов={len(graph.nodes)} связей={len(graph.edges)}")
    return graph


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


def make_handler(ctx: Ctx):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args) -> None:
            slog.step("HTTP " + fmt % args)

        def do_GET(self) -> None:
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
            return _send_text(self, "not found", code=404)

        def _not_found(self) -> None:
            _send_text(self, "not found", code=404)

        def do_POST(self) -> None:
            self._not_found()

        def do_PUT(self) -> None:
            self._not_found()

        def do_DELETE(self) -> None:
            self._not_found()

        def do_PATCH(self) -> None:
            self._not_found()

    return H


def serve(project_root: Path, host: str = "127.0.0.1", port: int = 8777) -> None:
    store.ensure_layout(project_root)
    slog.start_run(project_root, f"dev {host}:{port}")
    slog.step(f"сервер слушает http://{host}:{port}")
    server = ThreadingHTTPServer((host, port), make_handler(Ctx(project_root)))
    print(f"studio dev -> http://{host}:{port}  (project: {project_root})")
    print("Read-only viewer: the project is NOT started, code is never written.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
