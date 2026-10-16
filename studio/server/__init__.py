"""API для UI (stdlib http.server, без зависимостей — работает офлайн)."""

from __future__ import annotations

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from studio import config as config_mod
from studio import log as slog
from studio import store
from studio import writer as writer_mod
from studio.metamodel import Graph
from studio.scanner import build_graph
from studio.ui import INDEX
from studio.validator import validate_graph


class Ctx:
    def __init__(self, project_root: Path):
        self.root = project_root


def _send_json(h: BaseHTTPRequestHandler, obj, code: int = 200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def _send_text(h: BaseHTTPRequestHandler, text: str, ctype="text/html; charset=utf-8", code: int = 200):
    body = text.encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def make_handler(ctx: Ctx):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            slog.step("HTTP " + fmt % args)

        def _body(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            return self.rfile.read(n) if n else b""

        def do_GET(self):
            root = ctx.root
            url = urllib.parse.urlsplit(self.path)
            path, qs = url.path, urllib.parse.parse_qs(url.query)
            if path in ("/", "/index.html"):
                return _send_text(self, INDEX.read_text(encoding="utf-8"))
            if path == "/api/graph":
                g = store.read_graph(root)
                if g is None:
                    cfg = store.read_config(root)
                    g, hashes = build_graph(root, cfg)
                    store.write_graph(root, g)
                    store.write_hashes(root, hashes)
                return _send_json(self, g.model_dump())
            if path == "/api/config":
                return _send_json(self, store.read_config(root))
            if path == "/api/validate":
                g = store.read_graph(root) or Graph()
                cfg = store.read_config(root)
                vs = validate_graph(g, cfg)
                return _send_json(self, {"violations": [v.__dict__ for v in vs]})
            if path == "/api/diff":
                g = store.read_graph(root) or Graph()
                ops, vs, errs = writer_mod.plan(root, g, store.read_config(root))
                return _send_json(self, {"diff": writer_mod.diff_text(ops),
                                         "violations": [v.__dict__ for v in vs], "errors": errs,
                                         "ops": [o.__dict__ for o in ops]})
            if path == "/api/file":
                rel = (qs.get("path") or [""])[0]
                t = root / Path(*rel.split("/")) if rel else None
                code = t.read_text(encoding="utf-8") if t and t.is_file() else ""
                return _send_json(self, {"path": rel, "code": code})
            return _send_text(self, "not found", "text/plain", 404)

        def do_PUT(self):
            root = ctx.root
            url = urllib.parse.urlsplit(self.path)
            try:
                data = json.loads(self._body() or b"{}")
            except json.JSONDecodeError as e:
                return _send_json(self, {"error": f"bad json: {e}"}, 400)
            if url.path == "/api/graph":
                try:
                    g = Graph.model_validate(data)
                except Exception as e:
                    return _send_json(self, {"error": f"bad graph: {e}"}, 400)
                store.write_graph(root, g)
                return _send_json(self, {"ok": True})
            if url.path == "/api/config":
                cfg = config_mod._normalize(data if isinstance(data, dict) else {})
                store.write_config(root, cfg)
                return _send_json(self, {"ok": True})
            return _send_text(self, "not found", "text/plain", 404)

        def do_POST(self):
            root = ctx.root
            url = urllib.parse.urlsplit(self.path)
            try:
                data = json.loads(self._body() or b"{}")
            except json.JSONDecodeError:
                data = {}
            if url.path == "/api/scan":
                cfg = store.read_config(root)
                old = store.read_graph(root)
                slog.step("scan по запросу из UI")
                g, hashes = build_graph(root, cfg, old=old)
                # пометить нарушения, чтобы UI подсветил
                vs = {v.link_id for v in validate_graph(g, cfg)}
                for link in g.links:
                    link.violation = link.id in vs
                store.write_graph(root, g)
                store.write_hashes(root, hashes)
                slog.step(f"scan готов: узлов={len(g.nodes)} связей={len(g.links)} нарушений={len(vs)}")
                return _send_json(self, {"ok": True, "nodes": len(g.nodes), "links": len(g.links),
                                         "violations": len(vs)})
            if url.path == "/api/save":
                g = store.read_graph(root)
                if g is None:
                    return _send_json(self, {"error": "нет графа: сначала scan"}, 400)
                cfg = store.read_config(root)
                slog.step(f"save по запросу из UI (confirm={bool(data.get('confirm'))})")
                try:
                    ops, diff = writer_mod.apply(root, g, cfg, confirm=bool(data.get("confirm")))
                except RuntimeError as e:
                    slog.step(f"save отклонён: {e}")
                    return _send_json(self, {"error": str(e)}, 422)
                for op in ops:
                    slog.step(f"записано: {op.action} {op.path}")
                # apply без confirm при обязательном показе → вернуть diff, не писать (apply уже это учёл)
                st = cfg.get("studio", cfg).get("save", {})
                if not data.get("confirm") and st.get("show_diff"):
                    mode = st.get("diff_mode", "on_danger")
                    vs = validate_graph(g, cfg)
                    if mode == "always" or (mode == "on_danger" and vs):
                        return _send_json(self, {"need_confirm": True,
                                                 "diff": diff, "ops": [o.__dict__ for o in ops]})
                fresh = store.read_graph(root) or g
                return _send_json(self, {"ok": True, "ops": [o.__dict__ for o in ops], "diff": diff,
                                         "nodes": len(fresh.nodes), "links": len(fresh.links)})
            return _send_text(self, "not found", "text/plain", 404)

    return H


def serve(project_root: Path, host: str = "127.0.0.1", port: int = 8777):
    store.ensure_layout(project_root)
    slog.start_run(project_root, f"dev {host}:{port}")
    if not (store.studio_dir(project_root) / store.CONFIG_NAME).exists():
        store.write_config(project_root, config_mod.default_config())
        slog.step("дефолтный config.toml создан (его не было)")
    slog.step(f"сервер слушает http://{host}:{port}")
    srv = ThreadingHTTPServer((host, port), make_handler(Ctx(project_root)))
    srv = ThreadingHTTPServer((host, port), make_handler(Ctx(project_root)))
    print(f"studio dev -> http://{host}:{port}  (project: {project_root})")
    print("Open in browser. Project is NOT started, only file read/write on Save.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
