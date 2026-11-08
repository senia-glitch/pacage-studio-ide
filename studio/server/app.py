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
from studio.agent import BriefError, BriefNotFound, inbox
from studio.agent.sync_json import check_undo_entries, extract_json, same_state
from studio.creator import CreateError
from studio.frontend import brief as front_brief
from studio.frontend import graph as front_graph
from studio.frontend import meta as front_meta
from studio.frontend import scan_frontend
from studio.frontend import sync as front_sync
from studio.frontend import verify as front_verify
from studio.metadata import MetadataError
from studio.server import access, chain, discovery, gitops
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
        _mode = access.FULL  # переопределяется в _security по токену запроса

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
            self._mode = access.FULL
            if path.startswith("/api/"):
                mode = access.token_mode(state.token, self.headers.get("X-Studio-Token"))
                if mode is None:
                    self._discard_body()
                    raise _HTTPProblem(403, "bad_token", "Нет или неверен X-Studio-Token")
                if not access.route_allowed(mode, self.command, path):
                    slog.step(f"агент[{mode}] ЗАПРЕЩЕНО {self.command} {path}")
                    self._discard_body()
                    raise _HTTPProblem(
                        403,
                        "mode_forbidden",
                        f"Режим «{mode}» не разрешает {self.command} {path}. "
                        "Список разрешённого — GET /api/agent",
                    )
                self._mode = mode
                if mode != access.FULL:
                    state.workflow.seen(mode)
                    reason = state.workflow.agent_gate(
                        mode, access.WRITE_PATHS.get(mode, frozenset()), self.command, path
                    )
                    if reason:
                        slog.step(f"агент[{mode}] ЗАПРЕЩЕНО (этап) {self.command} {path}")
                        self._discard_body()
                        raise _HTTPProblem(403, "stage_forbidden", reason)
                if mode != access.FULL and path not in ("/api/wait", "/api/workflow"):
                    slog.step(f"агент[{mode}] {self.command} {path}")
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
                if exc.code == "mode_forbidden":
                    slog.step(f"агент[{self._mode}] отказ: {exc.message}")
                status = {
                    "invalid_input": 400,
                    "node_not_found": 404,
                    "anchor_not_found": 404,
                    "not_writable": 422,
                    "ideas_conflict": 409,
                    "wrong_stage": 409,
                    "agent_busy": 409,
                    "mode_forbidden": 403,
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
            if url.path == "/api/verify":
                query = urllib.parse.parse_qs(url.query)
                target = (query.get("brief") or [None])[0]
                return self._json(state.verify_brief(target))
            if url.path == "/api/settings/prompts":
                return self._json(state.prompts_state())
            if url.path == "/api/ideas":
                return self._json(state.load_ideas())
            if url.path == "/api/brief/last":
                return self._json(state.saved_brief())
            if url.path == "/api/agent/reports":
                reports = inbox.list_reports(state.root)
                pending = sum(1 for item in reports if item.get("status") == inbox.PENDING)
                return self._json({"reports": reports, "pending": pending})
            if url.path == "/api/agent/report":
                query = urllib.parse.parse_qs(url.query)
                return self._json(inbox.get_report(state.root, (query.get("id") or [""])[0]))
            if url.path == "/api/frontend/brief/last":
                return self._json(frontend_brief_last(state.root))
            if url.path == "/api/frontend/verify":
                query = urllib.parse.parse_qs(url.query)
                target = (query.get("brief") or [None])[0]
                return self._json(frontend_verify_op(state.root, target, state.check_front_links))
            if url.path == "/api/agent":
                return self._json(state.agent_info(self._mode))
            if url.path == "/api/workflow":
                query = urllib.parse.parse_qs(url.query)
                if "wait" in query:
                    try:
                        rev = int((query.get("rev") or ["0"])[0])
                        wait = float(query["wait"][0])
                    except ValueError:
                        rev, wait = 0, 0.0
                    state.workflow.wait(rev, max(0.0, min(wait, 25.0)))
                return self._json(chain.info(state))
            if url.path == "/api/workflow/candidates":
                return self._json(chain.candidates(state))
            if url.path == "/api/baseline":
                return self._json(chain.baseline(state))
            if url.path == "/api/tests":
                return self._json(
                    {
                        **state.tester.public(),
                        "plans": state.tester.failing_plans(),
                        **({"config": state.tester.config()} if self._mode == access.FULL else {}),
                    }
                )
            if url.path == "/api/runner":
                return self._json(state.runner.view())
            if url.path == "/api/agent/status":
                return self._json(state.runner.agent_view())
            if url.path == "/api/agent/models":
                # Список моделей подписки (может занять секунды: вызывает ``opencode models``)
                query = urllib.parse.parse_qs(url.query)
                return self._json(state.runner.models(
                    query.get("agent", [""])[0], refresh=query.get("refresh", [""])[0] == "1"
                ))
            if url.path == "/api/ui":
                return self._json(state.settings.get())
            if url.path == "/api/devservers":
                # серверы проекта видит и запускает только человек: агентским токенам маршрут закрыт
                return self._json(_servers_view(state))
            return self._error(404, "not_found", "Не найдено")

        # --------------------------------------------------------------- POST

        def _post(self) -> None:
            url = urllib.parse.urlsplit(self.path)
            data = self._read_body()
            # Автор записи: ставит только сервер по токену, значение из тела игнорируется.
            data.pop("_actor", None)
            if self._mode != access.FULL:
                data["_actor"] = "agent"
            if (
                self._mode != access.FULL
                and url.path in access.APPLY_PATHS
                and data.get("apply") is not None
                and data.get("apply") is not False
            ):
                return self._error(
                    403,
                    "mode_forbidden",
                    "Агент может только проверить ответ (без apply): применяет пользователь в UI",
                )
            if url.path == "/api/agent/report":
                return self._json(agent_report_submit(state, data, self._mode))
            if url.path == "/api/agent/report/decision":
                return self._json(
                    inbox.decide_report(
                        state.root, str(data.get("id") or ""), str(data.get("decision") or "")
                    )
                )
            if url.path == "/api/workflow/propose":
                return self._json(chain.propose(state, data))
            if url.path == "/api/workflow/withdraw":
                return self._json(chain.withdraw(state, data))
            if url.path == "/api/workflow/ideas-ready":
                return self._json(chain.ideas_ready(state))
            if url.path == "/api/workflow/start":
                blockers = state.runner.start_blockers(str(data.get("mode") or ""))
                if blockers:
                    # Идеи на одном агенте при неготовом другом — потраченные токены впустую
                    return self._error(
                        409, "agent_not_ready", "Агент не готов: " + "; ".join(blockers)
                    )
                return self._json(chain.start(state, data))
            if url.path == "/api/workflow/stage":
                return self._json(chain.stage(state, data))
            if url.path == "/api/workflow/decide":
                return self._json(chain.decide(state, data))
            if url.path == "/api/workflow/transfer":
                return self._json(chain.transfer(state))
            if url.path == "/api/workflow/transfer-card":
                return self._json(chain.transfer_card(state, data))
            if url.path == "/api/workflow/select":
                return self._json(chain.select(state, data, state.check_front_links))
            if url.path == "/api/workflow/finish":
                return self._json(chain.finish(state))
            if url.path == "/api/workflow/rework":
                return self._json(chain.rework(state, data))
            if url.path == "/api/agent/ask":
                return self._json(state.runner.ask(data.get("question"), data.get("options")))
            if url.path == "/api/agent/answer":
                return self._json(state.runner.answer(data.get("text")))
            if url.path == "/api/agent/connect":
                return self._json(state.runner.connect())
            if url.path == "/api/agent/check":
                return self._json(state.runner.check())
            if url.path == "/api/ui":
                return self._json(state.settings.save(data))
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
            if url.path == "/api/runner/config":
                state.runner.save_config(data)
                return self._json(state.runner.view())
            if url.path == "/api/runner/run":
                return self._json(state.runner.retry())
            if url.path == "/api/runner/stop":
                state.runner.stop()
                return self._json(state.runner.public())
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
            if url.path == "/api/settings/prompts":
                return self._json(state.save_prompt(data))
            if url.path == "/api/ideas":
                return self._json(state.save_ideas(data))
            if url.path == "/api/sync/prompt":
                return self._json(state.sync_prompt(data))
            if url.path == "/api/report":
                return self._json(state.report(data))
            if url.path == "/api/sync/apply":
                return self._json(state.sync_apply(data))
            if url.path == "/api/sync/undo":
                return self._json(state.sync_undo(data))
            if url.path == "/api/metadata/graph-node/restore":
                return self._json(state.restore_graph_node(data))
            if url.path == "/api/decompose/prompt":
                return self._json(state.decompose_prompt(data))
            if url.path == "/api/decompose/parse":
                return self._json(state.decompose_parse(data))
            if url.path == "/api/decompose":
                return self._json(state.decompose_apply(data))
            if url.path == "/api/frontend/meta":
                result = frontend_meta_op(state.root, data)
                result["graph"]["links"] = state.check_front_links(result["graph"])
                return self._json(result)
            if url.path == "/api/frontend/brief":
                return self._json(frontend_brief_op(state.root, data, state.check_front_links))
            if url.path == "/api/frontend/sync":
                return self._json(frontend_sync_op(state.root, data))
            if url.path == "/api/frontend/sync/apply":
                return self._json(frontend_sync_apply_op(state.root, data))
            if url.path == "/api/frontend/sync/undo":
                return self._json(frontend_sync_undo_op(state.root, data))
            if url.path == "/api/frontend/report":
                return self._json(front_report_op(state.root, data))
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
    actor = str(data.get("_actor") or "")
    action = str(data.get("action") or "")
    op = _META_OPS.get(action)
    if op is None:
        raise ValueError(f"Неизвестная операция фронтенда: {action}")
    payload = {key: value for key, value in data.items() if key not in ("action", "_actor")}
    with front_meta.META_LOCK:
        before = front_meta.load_meta(root) if actor == "agent" else None
        if before is not None:
            reason = front_meta.agent_violation(before, action, payload)
            if reason:
                raise MetadataError("mode_forbidden", reason)
        op(root, payload)
        if before is not None:
            front_meta.stamp_agent(root, before, action, payload)
    return {
        "meta": front_meta.load_meta(root),
        "graph": front_graph.build_front_graph(root),
    }


def agent_report_submit(state, data: dict, mode: str) -> dict:
    """Принять итоговый JSON-отчёт агента во входящие; ничего не применяется.

    Отчёт проверяется так же, как при ручном приёме: агент сразу видит, что студия
    отклонит и почему, а пользователь получает запись «ждёт решения».
    """
    part = str(data.get("part") or "backend")
    if part not in ("backend", "frontend"):
        raise MetadataError("invalid_input", "part: backend или frontend")
    summary = str(data.get("summary") or "").strip()
    if mode != access.FULL and not summary:
        raise MetadataError(
            "invalid_input",
            "Нужен summary: понятный человеку отчёт (что сделано, что проверено, что осталось)",
        )
    raw = data.get("json")
    if isinstance(raw, dict):
        text = json.dumps(raw, ensure_ascii=False)
    elif isinstance(raw, str) and raw.strip():
        text = raw
    else:
        raise MetadataError("invalid_input", "Передайте JSON-отчёт в поле json")
    if part == "backend":
        preview = state.sync_apply({"json": text})
    else:
        preview = frontend_sync_apply_op(state.root, {"json": text})
    record = inbox.submit_report(state.root, part, mode, text, preview, summary)
    if summary:
        state.workflow.report_received(record["id"], summary)
    return {
        "id": record["id"],
        "status": record["status"],
        "counts": record["counts"],
        "rejected": [
            {
                "ref": row.get("label") or row.get("ref") or row.get("id"),
                "reason": row.get("reason", ""),
            }
            for row in preview.get("rows") or []
            if row.get("status") == "rejected"
        ],
        "warnings": preview.get("warnings") or [],
    }


def frontend_brief_last(root: Path) -> dict:
    """ТЗ фронтенда, собранное в окне «⇄ ТЗ…» последним: его и реализует агент."""
    path = Path(root) / ".studio" / front_brief.FRONT_BRIEF_FILENAME
    if not path.is_file():
        raise BriefNotFound("ТЗ фронтенда нет: соберите его кнопкой «⇄ ТЗ…» во вкладке «Фронтенд»")
    try:
        markdown = path.read_text(encoding="utf-8")
        brief = front_brief.load_front_brief(root, path.as_posix())
    except (OSError, UnicodeDecodeError, FileNotFoundError) as exc:
        raise BriefNotFound(f"ТЗ фронтенда не читается: {exc}") from exc
    return {
        "markdown": markdown,
        "brief_id": str(brief.get("brief_id") or ""),
        "path": f".studio/{front_brief.FRONT_BRIEF_FILENAME}",
        "tasks": brief.get("tasks") or [],
    }


@_front_errors
def frontend_verify_op(root: Path, target: str | None, check_links=None) -> dict:
    """Проверка выполнения ТЗ фронтенда по текущему коду (как /api/verify)."""
    try:
        brief = front_brief.load_front_brief(root, target)
    except FileNotFoundError as exc:
        raise BriefNotFound(str(exc)) from exc
    front = front_graph.build_front_graph(root)
    links = check_links(front) if check_links else None
    return front_verify.verify_front_brief(front, brief, links)


@_front_errors
def frontend_brief_op(root: Path, data: dict, check_links=None) -> dict:
    """ТЗ фронтенда по области; markdown сохраняется в .studio."""
    scope = str(data.get("scope") or "all")
    node_id = data.get("nodeId") or None
    return front_brief.brief_frontend(
        root,
        scope=scope,
        node_id=str(node_id) if node_id else None,
        sections=data.get("sections"),
        check_links=check_links,
    )


@_front_errors
def frontend_sync_op(root: Path, data: dict) -> dict:
    """Промпт синхронизации описаний фронтенда с кодом."""
    markdown = front_sync.build_front_sync_prompt(
        front_graph.build_front_graph(root),
        scope=str(data.get("scope") or "all"),
        domain=str(data.get("domain") or ""),
        page_id=str(data.get("pageId") or ""),
    )
    return {"markdown": markdown}


@_front_errors
def front_report_op(root: Path, data: dict | None = None) -> dict:
    return front_brief.report_frontend(root, sections=(data or {}).get("sections"))


@_front_errors
def frontend_sync_apply_op(root: Path, data: dict) -> dict:
    """JSON-ответ агента по описаниям фронтенда: проверка, отличия, запись при apply=true."""
    graph = front_graph.build_front_graph(root)
    payload = extract_json(data.get("json"))
    report, block_pages = front_sync.check_front_sync_response(graph, payload)
    report["applied"] = 0
    report["undo"] = []
    if data.get("apply") is not True:
        return report
    chosen = data.get("ids")
    allowed = {str(item) for item in chosen} if isinstance(chosen, list) else None
    for row in report["rows"]:
        if row["status"] != "change" or (allowed is not None and row["id"] not in allowed):
            continue
        payload = {
            "id": row["id"],
            "description": row["after"]["description"],
            "acceptance": row["after"]["acceptance"],
        }
        if row["id"] in block_pages:
            front_meta.save_block_meta(root, {**payload, "pageId": block_pages[row["id"]]})
        else:
            front_meta.save_page_meta(root, payload)
        row["status"] = "applied"
        report["applied"] += 1
        report["undo"].append({"id": row["id"], "before": row["before"], "after": row["after"]})
    if report["applied"]:
        report["meta"] = front_meta.load_meta(root)
        report["graph"] = front_graph.build_front_graph(root)
    return report


def _write_front_state(root: Path, node_id: str, block_pages: dict, state: dict) -> None:
    payload = {"id": node_id, **state}
    if node_id in block_pages:
        front_meta.save_block_meta(root, {**payload, "pageId": block_pages[node_id]})
    else:
        front_meta.save_page_meta(root, payload)


@_front_errors
def frontend_sync_undo_op(root: Path, data: dict) -> dict:
    """Вернуть описания страниц и блоков, записанные «Применить».

    Узлы, изменённые после применения, пропускаются (skipped).
    """
    entries = check_undo_entries(data.get("undo"))
    known, block_pages = front_sync.sync_known_nodes(front_graph.build_front_graph(root))
    restored, skipped = 0, []
    for entry in entries:
        info = known.get(entry["id"])
        if info is None or not same_state(info, entry["after"]):
            skipped.append(entry["id"])
            continue
        _write_front_state(root, entry["id"], block_pages, entry["before"])
        restored += 1
    result: dict = {"restored": restored, "skipped": skipped}
    if restored:
        result["meta"] = front_meta.load_meta(root)
        result["graph"] = front_graph.build_front_graph(root)
    return result


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
    state.runner.probe_async()  # MCP-сервер агента падает тихо: проверяем рукопожатием заранее
    if not no_open and not os.environ.get("CI"):
        with contextlib.suppress(Exception):
            webbrowser.open(url)
    state.start_watcher()
    from studio import __version__

    discovery.write(root, url, state.token, __version__)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Остановлено")
    finally:
        discovery.remove(root)
        state.runner.stop()
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
