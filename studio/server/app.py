"""HTTP-приложение студии: роуты, безопасность, отдача статики."""

from __future__ import annotations

import contextlib
import functools
import json
import os
import socket
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from studio import log as slog
from studio._constants import MAX_HTTP_BODY
from studio.frontend import graph as front_graph
from studio.frontend import meta as front_meta
from studio.frontend import scan_frontend
from studio.metadata import MetadataError
from studio.pipeline import PipelineError
from studio.pipeline.graph import GraphError as IdeaGraphError
from studio.server import access, gitops, system
from studio.server.pipeline import PREFIX as PIPELINE_PREFIX
from studio.server.security import (
    CSP,
    SECURITY_HEADERS,
    content_type,
    host_allowed,
    inject_token,
    origin_allowed,
    static_path,
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
            if path.startswith("/api/"):
                mode = access.token_mode(state.token, self.headers.get("X-Studio-Token"))
                if mode is None:
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
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:  # noqa: BLE001 — граница HTTP-запроса
                slog.step(f"HTTP 500: {type(exc).__name__}: {exc}")
                self._error(500, "internal", f"Внутренняя ошибка: {type(exc).__name__}")

        def _pipeline(self, call) -> None:
            """Конвейер: ошибки правил — понятным ответом 400 (код и текст)."""
            try:
                self._json(call())
            except (PipelineError, IdeaGraphError) as exc:
                self._error(400, exc.code, exc.message)

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
                cache_headers = {
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Content-Security-Policy": CSP,
                }
                return self._send_bytes(body, 200, content_type(candidate), cache_headers)
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
                if "aux" not in query:
                    return self._json({"revision": state.wait_for_change(rev)})
                try:
                    aux = int(query["aux"][0])
                except ValueError:
                    aux = -1
                revision = state.wait_for_change(rev, aux=aux)
                return self._json({"revision": revision, "aux": state.aux})
            if url.path == "/api/frontend":
                return self._json(scan_frontend(state.root))
            if url.path == "/api/frontend/graph":
                graph = front_graph.build_front_graph(state.root)
                graph["links"] = state.check_front_links(graph)
                return self._json(graph)
            if url.path == "/api/system":
                # единая карта системы: бэкенд, фронтенд и их связи одним ответом (только чтение)
                front = front_graph.build_front_graph(state.root)
                links = state.check_front_links(front)
                graph = state.ensure_graph()
                overview = state.pipeline.overview()
                return self._json(system.build_system(graph, front, links, overview))
            if url.path == "/api/tests":
                return self._json(
                    {
                        **state.tester.public(),
                        "plans": state.tester.failing_plans(),
                        "config": state.tester.config(),
                    }
                )
            if url.path == "/api/devservers":
                # серверы проекта видит и запускает только человек: агентским токенам маршрут закрыт
                return self._json(_servers_view(state))
            if url.path.startswith(PIPELINE_PREFIX + "/"):
                query = urllib.parse.parse_qs(url.query)
                return self._pipeline(lambda: state.pipeline.get(url.path, query))
            return self._error(404, "not_found", "Не найдено")

        # --------------------------------------------------------------- POST

        def _post(self) -> None:
            url = urllib.parse.urlsplit(self.path)
            data = self._read_body()
            if url.path.startswith(PIPELINE_PREFIX + "/"):
                return self._pipeline(lambda: state.pipeline.post(url.path, data))
            if url.path == "/api/git/rollback":
                result = gitops.rollback(state.root)
                if not result["ok"]:
                    return self._error(409, "git_failed", result["reason"])
                state.rescan("rollback")
                return self._json({**result, "git": gitops.status(state.root)})
            if url.path == "/api/devservers/start":
                return self._json(state.devservers.start(str(data.get("name") or "all")))
            if url.path == "/api/devservers/stop":
                return self._json(state.devservers.stop(str(data.get("name") or "all")))
            if url.path == "/api/devservers/config":
                state.devservers.save_config(data)
                return self._json(_servers_view(state))
            if url.path == "/api/tests/run":
                return self._json(state.tester.start())
            if url.path == "/api/tests/config":
                state.tester.save_config(data)
                return self._json({**state.tester.public(), "config": state.tester.config()})
            if url.path == "/api/rescan":
                graph = state.rescan("manual")
                return self._json(graph.model_dump())
            if url.path == "/api/metadata/node":
                return self._json(state.save_metadata(data))
            if url.path == "/api/metadata/graph-node":
                return self._json(state.save_graph_node(data))
            if url.path == "/api/metadata/graph-node/delete":
                return self._json(state.delete_graph_node(data))
            if url.path == "/api/metadata/planned-change":
                return self._json(state.save_planned_change(data))
            if url.path == "/api/metadata/domain-plan":
                return self._json(state.save_domain_plan(data))
            if url.path == "/api/metadata/domain-plan/delete":
                return self._json(state.delete_domain_plan(data))
            if url.path == "/api/metadata/graph-node/restore":
                return self._json(state.restore_graph_node(data))
            if url.path == "/api/frontend/meta":
                result = frontend_meta_op(state.root, data)
                result["graph"]["links"] = state.check_front_links(result["graph"])
                return self._json(result)
            return self._error(404, "not_found", "Не найдено")

    return Handler


_META_OPS = {
    "save_page": front_meta.save_page_meta,
    "save_block": front_meta.save_block_meta,
    "save_rule": front_meta.save_rule,
    "save_delete_note": front_meta.save_delete_note,
    "delete_planned": front_meta.delete_planned,
    "restore_planned": front_meta.restore_planned,
    "save_domain": front_meta.save_domain,
    "move_block": front_meta.move_block,
}


def _front_errors(func):
    """Понятные ошибки ввода фронтенд-операций (ValueError) → 400, а не 500."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ValueError as exc:
            raise MetadataError("invalid_input", str(exc)) from exc

    return wrapper


@_front_errors
def frontend_meta_op(root: Path, data: dict) -> dict:
    """Диспетчер операций метаданных фронтенда; ответ = meta + граф."""
    action = str(data.get("action") or "")
    op = _META_OPS.get(action)
    if op is None:
        raise ValueError(f"Неизвестная операция фронтенда: {action}")
    payload = {key: value for key, value in data.items() if key != "action"}
    with front_meta.META_LOCK:
        op(root, payload)
    return {
        "meta": front_meta.load_meta(root),
        "graph": front_graph.build_front_graph(root),
    }


def _servers_view(state: ProjectState) -> dict:
    """Серверы проекта и их настройка (только для человека: агентским токенам маршрут закрыт)."""
    return {**state.devservers.public(), "config": state.devservers.config()}


def _try_bind(host: str, port: int, handler) -> ThreadingHTTPServer | None:
    """Создать сервер; занятый порт не оставляет полуоткрытый сокет."""
    server = ThreadingHTTPServer((host, port), handler, bind_and_activate=False)
    if os.name == "nt":
        # На Windows SO_REUSEADDR позволяет второму процессу занять уже слушаемый порт
        # без ошибки: браузер попадает в старый экземпляр студии с другим проектом.
        # Занимаем порт исключительно — занятый порт даст OSError и выбор следующего.
        server.allow_reuse_address = False
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        sock = getattr(server, "socket", None)
        if exclusive is not None and sock is not None:
            sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
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
        state.pipeline.close()
        state.tester.stop()
        state.devservers.stop_all()
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
