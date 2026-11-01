"""HTTP-приложение студии: роуты, безопасность, отдача статики."""

from __future__ import annotations

import contextlib
import json
import os
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from studio import log as slog
from studio._constants import MAX_HTTP_BODY
from studio.agent import BriefError, BriefNotFound
from studio.creator import CreateError
from studio.metadata import MetadataError
from studio.server.security import (
    CSP,
    SECURITY_HEADERS,
    content_type,
    host_allowed,
    inject_token,
    origin_allowed,
    static_path,
    token_ok,
)
from studio.server.state import NodeNotFound, ProjectState
from studio.ui import INDEX, STATIC_DIR

DEFAULT_PORT_START = 8777
DEFAULT_PORT_END = 8796


class PortInUse(Exception):
    pass


class _HTTPProblem(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def _state_of(ctx) -> ProjectState:
    if isinstance(ctx, ProjectState):
        return ctx
    state = getattr(ctx, "state", None)
    if isinstance(state, ProjectState):
        return state
    return ProjectState(Path(ctx.root))


def make_handler(ctx):
    state = _state_of(ctx)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "studio"
        sys_version = ""

        # ------------------------------------------------------------ ответы

        def log_message(self, fmt, *args) -> None:
            if state.verbose:
                slog.step("HTTP " + fmt % args)

        def _common_headers(self, extra: dict | None = None) -> None:
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            for key, value in (extra or {}).items():
                self.send_header(key, value)

        def _send_bytes(
            self,
            body: bytes,
            code: int = 200,
            ctype: str = "application/json; charset=utf-8",
            extra: dict | None = None,
        ) -> None:
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                if not extra or "Cache-Control" not in extra:
                    self.send_header("Cache-Control", "no-store")
                self._common_headers(extra)
                self.end_headers()
                if body:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, payload, code: int = 200, extra: dict | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send_bytes(body, code, "application/json; charset=utf-8", extra)

        def _error(self, status: int, code: str, message: str, details=None) -> None:
            error = {"code": code, "message": message}
            if details is not None:
                error["details"] = details
            extra = {"Connection": "close"} if self.command == "POST" else None
            if self.command == "POST":
                self.close_connection = True
            self._json({"error": error}, status, extra)

        def _plan_error(self, status: int, code: str, message: str) -> None:
            """Ошибка плана без трейсбека: плоские error и code, как в ТЗ."""
            extra = {"Connection": "close"} if self.command == "POST" else None
            if self.command == "POST":
                self.close_connection = True
            self._json({"error": message, "code": code}, status, extra)

        # --------------------------------------------------------- безопасность

        def _discard_body(self) -> None:
            """Прочитать тело, чтобы клиент успел получить ответ об ошибке."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return
            if 0 < length <= MAX_HTTP_BODY:
                with contextlib.suppress(OSError):
                    self.rfile.read(length)

        def _security(self) -> None:
            host = self.headers.get("Host", "")
            bound_port = int(self.server.server_address[1])
            if not host_allowed(host, bound_port, state.host):
                self._discard_body()
                raise _HTTPProblem(403, "bad_host", "Неверный заголовок Host")
            if not origin_allowed(self.headers.get("Origin"), host):
                self._discard_body()
                raise _HTTPProblem(403, "bad_origin", "Неверный Origin")
            path = urllib.parse.urlsplit(self.path).path
            if path.startswith("/api/") and not token_ok(
                state.token, self.headers.get("X-Studio-Token")
            ):
                self._discard_body()
                raise _HTTPProblem(403, "bad_token", "Нет или неверен X-Studio-Token")
            if self.command == "POST":
                ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                raw_length = self.headers.get("Content-Length")
                if raw_length is None:
                    raise _HTTPProblem(411, "length_required", "Нужен Content-Length")
                try:
                    length = int(raw_length)
                except ValueError as exc:
                    raise _HTTPProblem(
                        400, "bad_length", "Некорректный Content-Length"
                    ) from exc
                if length < 0:
                    raise _HTTPProblem(
                        400, "bad_length", "Content-Length не может быть отрицательным"
                    )
                if length > MAX_HTTP_BODY:
                    raise _HTTPProblem(
                        413,
                        "body_too_large",
                        f"Тело запроса больше {MAX_HTTP_BODY // (1024 * 1024)} МБ",
                    )
                if ctype != "application/json":
                    self._discard_body()
                    raise _HTTPProblem(
                        415, "bad_content_type", "Нужен Content-Type: application/json"
                    )

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                data = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise _HTTPProblem(400, "bad_json", "Тело запроса — не корректный JSON") from exc
            if not isinstance(data, dict):
                raise _HTTPProblem(400, "bad_json", "Тело запроса должно быть объектом JSON")
            return data

        def _guard(self, action) -> None:
            try:
                self._security()
                action()
            except _HTTPProblem as problem:
                self._error(problem.status, problem.code, problem.message)
            except NodeNotFound:
                self._error(404, "node_not_found", "Узел не найден — обновите карту")
            except MetadataError as exc:
                status = {
                    "invalid_input": 400,
                    "node_not_found": 404,
                    "anchor_not_found": 404,
                    "not_writable": 422,
                }.get(exc.code, 400)
                code = "node_not_found" if exc.code == "anchor_not_found" else exc.code
                self._error(status, code, exc.message, getattr(exc, "details", None))
            except BriefNotFound as exc:
                self._error(404, "brief_not_found", exc.message)
            except BriefError as exc:
                status = 422 if exc.code == "not_writable" else 400
                self._error(status, exc.code, exc.message)
            except CreateError as exc:
                code = getattr(exc, "code", "create_rejected")
                if code.startswith("W_PLAN_") or code in (
                    "E_INTERNAL",
                    "W_METADATA_INVALID",
                    "plan_not_found",
                    "not_writable",
                ):
                    status = {"plan_not_found": 404, "not_writable": 422}.get(code, 400)
                    self._plan_error(status, code, str(exc))
                else:
                    self._error(409, "create_rejected", str(exc))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:  # noqa: BLE001 — граница HTTP-запроса
                slog.step(f"HTTP 500: {type(exc).__name__}: {exc}")
                self._error(500, "internal", f"Внутренняя ошибка: {type(exc).__name__}")

        def do_GET(self) -> None:
            self._guard(self._get)

        def do_POST(self) -> None:
            self._guard(self._post)

        def do_PUT(self) -> None:
            self._guard(lambda: self._error(404, "not_found", "Не найдено"))

        def do_DELETE(self) -> None:
            self._guard(lambda: self._error(404, "not_found", "Не найдено"))

        def do_PATCH(self) -> None:
            self._guard(lambda: self._error(404, "not_found", "Не найдено"))

        # ---------------------------------------------------------------- GET

        def _get(self) -> None:
            url = urllib.parse.urlsplit(self.path)
            if url.path in ("/", "/index.html"):
                try:
                    html = INDEX.read_text(encoding="utf-8")
                except OSError:
                    return self._error(500, "ui_missing", "index.html не найден")
                html = inject_token(html, state.token)
                return self._send_bytes(
                    html.encode("utf-8"),
                    200,
                    "text/html; charset=utf-8",
                    {"Content-Security-Policy": CSP},
                )
            if url.path.startswith("/static/"):
                candidate = static_path(STATIC_DIR, url.path)
                if candidate is None:
                    return self._error(404, "not_found", "Файл не найден")
                try:
                    body = candidate.read_bytes()
                except OSError:
                    return self._error(404, "not_found", "Файл не найден")
                return self._send_bytes(
                    body,
                    200,
                    content_type(candidate),
                    {"Cache-Control": "no-cache", "Content-Security-Policy": CSP},
                )
            if url.path == "/api/graph":
                graph = state.ensure_graph()
                etag = f'"{state.revision}"'
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("X-Studio-Revision", str(state.revision))
                    self._common_headers()
                    self.end_headers()
                    return
                return self._json(
                    graph.model_dump(),
                    extra={"ETag": etag, "X-Studio-Revision": str(state.revision)},
                )
            if url.path == "/api/state":
                state.ensure_graph()
                return self._json(state.snapshot())
            if url.path == "/api/wait":
                query = urllib.parse.parse_qs(url.query)
                try:
                    rev = int((query.get("rev") or ["0"])[0])
                except ValueError:
                    rev = 0
                return self._json({"revision": state.wait_for_change(rev)})
            if url.path == "/api/verify":
                query = urllib.parse.parse_qs(url.query)
                target = (query.get("brief") or [None])[0]
                return self._json(state.verify_brief(target))
            return self._error(404, "not_found", "Не найдено")

        # --------------------------------------------------------------- POST

        def _post(self) -> None:
            url = urllib.parse.urlsplit(self.path)
            data = self._read_body()
            if url.path == "/api/rescan":
                graph = state.rescan("manual")
                return self._json(graph.model_dump())
            if url.path == "/api/metadata/node":
                return self._json(state.save_metadata(data))
            if url.path == "/api/metadata/graph-node":
                return self._json(state.save_graph_node(data))
            if url.path == "/api/metadata/graph-node/delete":
                return self._json(state.delete_graph_node(data))
            if url.path == "/api/plan/preview":
                return self._json(state.plan_preview(data))
            if url.path == "/api/plan":
                return self._json(state.plan_create(data))
            if url.path == "/api/plan/update":
                return self._json(state.plan_update(data))
            if url.path == "/api/plan/delete":
                return self._json(state.plan_delete(data))
            if url.path == "/api/brief":
                return self._json(state.brief(data))
            if url.path == "/api/brief/save":
                return self._json(state.save_brief(data))
            if url.path == "/api/decompose/prompt":
                return self._json(state.decompose_prompt(data))
            if url.path == "/api/decompose":
                return self._json(state.decompose_apply(data))
            return self._error(404, "not_found", "Не найдено")

    return Handler


def _try_bind(host: str, port: int, handler) -> ThreadingHTTPServer | None:
    """Создать сервер; занятый порт не оставляет полуоткрытый сокет."""
    server = ThreadingHTTPServer((host, port), handler, bind_and_activate=False)
    try:
        server.server_bind()
        server.server_activate()
    except OSError:
        server.server_close()
        return None
    return server


def _bind(host: str, port: int | None, handler) -> ThreadingHTTPServer:
    if port is None:
        for candidate in range(DEFAULT_PORT_START, DEFAULT_PORT_END + 1):
            server = _try_bind(host, candidate, handler)
            if server is not None:
                return server
        raise PortInUse(
            f"Порты {DEFAULT_PORT_START}–{DEFAULT_PORT_END} заняты. "
            "Укажите другой: studio dev --port 8800"
        )
    server = _try_bind(host, port, handler)
    if server is None:
        raise PortInUse(f"Порт {port} занят. Укажите другой: studio dev --port 8800")
    return server


def _print_summary(state: ProjectState, url: str) -> None:
    graph = state.graph
    counts = {"endpoint": 0, "scenario": 0, "io": 0, "model": 0}
    warnings = 0
    if graph is not None:
        for node in graph.nodes:
            if node.kind in counts:
                counts[node.kind] += 1
        warnings = sum(1 for d in graph.diagnostics if d.level != "info")
    print(f"studio dev -> {url}")
    print(f"Проект: {state.root}")
    print(
        f"Эндпоинтов {counts['endpoint']} · сценариев {counts['scenario']} · "
        f"io {counts['io']} · моделей {counts['model']} · предупреждений {warnings}"
    )
    if warnings:
        print("Подробности: studio doctor")
    print("Ctrl+C — остановить")


def serve(
    project_root: Path,
    host: str = "127.0.0.1",
    port: int | None = None,
    no_open: bool = False,
    poll: float | None = None,
    verbose: bool = False,
) -> int:
    root = Path(project_root).resolve()
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "Внимание: сервер доступен по сети; токен защищает запись, "
            "но трафик не шифруется."
        )
    poll_interval = poll
    if poll_interval is None:
        try:
            poll_interval = float(os.environ.get("STUDIO_POLL") or 1.0)
        except ValueError:
            poll_interval = 1.0
    state = ProjectState(root, poll_interval=poll_interval)
    state.host = host
    state.verbose = verbose
    if not slog.is_active(root):
        slog.start_run(root, f"dev {host}:{port or 'auto'}")
    state.rescan("start")
    server = _bind(host, port, make_handler(state))
    bound_port = int(server.server_address[1])
    url = f"http://{host}:{bound_port}"
    _print_summary(state, url)
    if not no_open and not os.environ.get("CI"):
        with contextlib.suppress(Exception):
            webbrowser.open(url)
    state.start_watcher()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Остановлено")
    finally:
        state.stop_watcher()
        server.server_close()
    return bound_port


__all__ = [
    "DEFAULT_PORT_END",
    "DEFAULT_PORT_START",
    "PortInUse",
    "make_handler",
    "serve",
]
