"""Юнит-тесты HTTP-слоя без запуска сервера: обработчики и чистые ветки."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from studio.agent import BriefError, BriefNotFound
from studio.creator import CreateError
from studio.metadata import MetadataError
from studio.metamodel import Graph, Node
from studio.server import Ctx
from studio.server import app as app_mod
from studio.server.app import (
    _bind,
    _print_summary,
    _state_of,
    _try_bind,
    make_handler,
    serve,
)
from studio.server.state import NodeNotFound, ProjectState


def _state(tmp_path: Path) -> ProjectState:
    state = ProjectState(tmp_path)
    graph = Graph(version=7, nodes=[Node(id="endpoint:a.py:a", kind="endpoint", label="GET /a", file_path="a.py", line=1)])
    state.graph = graph
    state.ensure_graph = lambda: graph
    state.snapshot = lambda: {"revision": state.revision}
    state.wait_for_change = lambda rev: state.revision if rev < state.revision else state.revision
    state.verify_brief = lambda target: {"target": target}
    state.save_metadata = lambda data: {"ok": True, "data": data}
    state.plan_preview = lambda data: {"preview": data}
    state.plan_create = lambda data: {"created": data}
    state.plan_update = lambda data: {"updated": data}
    state.plan_delete = lambda data: {"deleted": data}
    state.brief = lambda data: {"brief": data}
    state.save_brief = lambda data: {"saved": data}
    state.rescan = lambda reason: graph
    return state


def _handler(state: ProjectState, path: str = "/", command: str = "GET", headers=None, body: bytes = b""):
    Handler = make_handler(state)
    h = Handler.__new__(Handler)
    h.headers = headers or {}
    h.command = command
    h.path = path
    h.rfile = io.BytesIO(body)
    h.wfile = io.BytesIO()
    h.server = SimpleNamespace(server_address=("127.0.0.1", 8765))
    h.sent: list[tuple] = []
    h.closed = False
    h.send_response = lambda code: h.sent.append(("status", code))
    h.send_header = lambda key, value: h.sent.append((key, value))
    h.end_headers = lambda: h.sent.append(("end", None))
    h.close_connection = False
    return h


def _json_response(h) -> dict:
    import json

    body = h.wfile.getvalue()
    return json.loads(body.decode("utf-8")) if body else {}


def test_state_of_variants(tmp_path: Path) -> None:
    state = ProjectState(tmp_path)
    assert _state_of(state) is state
    assert _state_of(SimpleNamespace(state=state)) is state
    ctx = Ctx(tmp_path)
    assert isinstance(_state_of(ctx), ProjectState)
    plain = SimpleNamespace(root=tmp_path)
    assert isinstance(_state_of(plain), ProjectState)


def test_send_bytes_and_errors(tmp_path: Path, monkeypatch) -> None:
    state = _state(tmp_path)
    h = _handler(state)
    h._send_bytes(b"ok", 201, "text/plain", {"Cache-Control": "no-cache"})
    assert ("status", 201) in h.sent and ("Cache-Control", "no-cache") in h.sent
    h._send_bytes(b"", 204)
    assert ("status", 204) in h.sent

    def broken(_body):
        raise BrokenPipeError()

    h.wfile.write = broken  # type: ignore[method-assign]
    h._send_bytes(b"x", 200)

    h = _handler(state, command="POST")
    h._error(400, "bad", "плохо", {"x": 1})
    assert h.close_connection is True
    assert ("Connection", "close") in h.sent
    assert _json_response(h)["error"]["details"] == {"x": 1}
    h.wfile = io.BytesIO()
    h._plan_error(404, "plan_not_found", "нет")
    assert _json_response(h)["code"] == "plan_not_found"


def test_discard_body(tmp_path: Path) -> None:
    state = _state(tmp_path)
    h = _handler(state, headers={"Content-Length": "abc"}, body=b"x")
    h._discard_body()
    assert h.rfile.tell() == 0
    h = _handler(state, headers={"Content-Length": "1"}, body=b"x")
    h._discard_body()
    assert h.rfile.tell() == 1
    h = _handler(state, headers={"Content-Length": str(2 * 1024 * 1024)}, body=b"")
    h._discard_body()
    assert h.rfile.tell() == 0


def test_security_host_origin_token(tmp_path: Path) -> None:
    state = _state(tmp_path)
    h = _handler(state, headers={"Host": "evil"})
    with pytest.raises(app_mod._HTTPProblem) as exc:
        h._security()
    assert exc.value.status == 403 and exc.value.code == "bad_host"
    h = _handler(state, headers={"Host": "127.0.0.1:8765", "Origin": "http://evil"})
    with pytest.raises(app_mod._HTTPProblem) as exc:
        h._security()
    assert exc.value.code == "bad_origin"
    h = _handler(state, path="/api/graph", headers={"Host": "127.0.0.1:8765"})
    with pytest.raises(app_mod._HTTPProblem) as exc:
        h._security()
    assert exc.value.code == "bad_token"
    h = _handler(
        state,
        path="/api/graph",
        headers={"Host": "127.0.0.1:8765", "X-Studio-Token": state.token},
    )
    h._security()
    assert h.sent == []


def test_security_post_body_limits(tmp_path: Path) -> None:
    state = _state(tmp_path)
    base = {"Host": "127.0.0.1:8765", "X-Studio-Token": state.token}
    h = _handler(state, path="/api/brief", command="POST", headers=dict(base))
    with pytest.raises(app_mod._HTTPProblem) as exc:
        h._security()
    assert exc.value.status == 411
    h = _handler(state, path="/api/brief", command="POST", headers={**base, "Content-Length": "x"})
    with pytest.raises(app_mod._HTTPProblem) as exc:
        h._security()
    assert exc.value.code == "bad_length"
    h = _handler(state, path="/api/brief", command="POST", headers={**base, "Content-Length": "-1"})
    with pytest.raises(app_mod._HTTPProblem) as exc:
        h._security()
    assert exc.value.code == "bad_length"
    h = _handler(
        state,
        path="/api/brief",
        command="POST",
        headers={**base, "Content-Length": str(2 * 1024 * 1024)},
    )
    with pytest.raises(app_mod._HTTPProblem) as exc:
        h._security()
    assert exc.value.status == 413
    h = _handler(
        state,
        path="/api/brief",
        command="POST",
        headers={**base, "Content-Length": "1", "Content-Type": "text/plain"},
    )
    with pytest.raises(app_mod._HTTPProblem) as exc:
        h._security()
    assert exc.value.status == 415
    h = _handler(
        state,
        path="/api/brief",
        command="POST",
        headers={**base, "Content-Length": "1", "Content-Type": "application/json; charset=utf-8"},
    )
    h._security()
    assert h.sent == []


def test_read_body_variants(tmp_path: Path) -> None:
    state = _state(tmp_path)
    h = _handler(state, body=b"")
    with pytest.raises(app_mod._HTTPProblem) as exc:
        h._read_body()
    assert exc.value.code == "bad_json"
    h = _handler(state, body=b"\xff")
    with pytest.raises(app_mod._HTTPProblem):
        h._read_body()
    h = _handler(state, body=b"[1]")
    with pytest.raises(app_mod._HTTPProblem):
        h._read_body()
    h = _handler(state, headers={"Content-Length": "7"}, body=b'{"a":1}')
    assert h._read_body() == {"a": 1}


def test_guard_exception_mapping(tmp_path: Path) -> None:
    state = _state(tmp_path)
    h = _handler(state)
    h._security = lambda: None  # type: ignore[method-assign]
    h._guard(lambda: (_ for _ in ()).throw(NodeNotFound("x")))
    assert _json_response(h)["error"]["code"] == "node_not_found"
    h = _handler(state)
    h._security = lambda: None  # type: ignore[method-assign]
    h._guard(lambda: (_ for _ in ()).throw(MetadataError("anchor_not_found", "нет")))
    assert _json_response(h)["error"]["code"] == "node_not_found"
    h = _handler(state)
    h._security = lambda: None  # type: ignore[method-assign]
    h._guard(lambda: (_ for _ in ()).throw(MetadataError("not_writable", "нет")))
    assert ("status", 422) in h.sent
    h = _handler(state)
    h._security = lambda: None  # type: ignore[method-assign]
    h._guard(lambda: (_ for _ in ()).throw(MetadataError("invalid_input", "нет")))
    assert ("status", 400) in h.sent
    h = _handler(state)
    h._security = lambda: None  # type: ignore[method-assign]
    h._guard(lambda: (_ for _ in ()).throw(BriefNotFound("нет")))
    assert _json_response(h)["error"]["code"] == "brief_not_found"
    h = _handler(state)
    h._security = lambda: None  # type: ignore[method-assign]
    h._guard(lambda: (_ for _ in ()).throw(BriefError("not_writable", "нет")))
    assert ("status", 422) in h.sent
    h = _handler(state)
    h._security = lambda: None  # type: ignore[method-assign]
    h._guard(lambda: (_ for _ in ()).throw(BriefError("bad", "нет")))
    assert ("status", 400) in h.sent
    h = _handler(state, command="POST")
    h._security = lambda: None  # type: ignore[method-assign]
    h._guard(lambda: (_ for _ in ()).throw(CreateError("конфликт", "W_PLAN_CONFLICT")))
    assert _json_response(h)["code"] == "W_PLAN_CONFLICT"
    h = _handler(state, command="POST")
    h._security = lambda: None  # type: ignore[method-assign]
    h._guard(lambda: (_ for _ in ()).throw(CreateError("конфликт", "plan_not_found")))
    assert ("status", 404) in h.sent
    h = _handler(state, command="POST")
    h._security = lambda: None  # type: ignore[method-assign]
    h._guard(lambda: (_ for _ in ()).throw(CreateError("конфликт", "not_writable")))
    assert ("status", 422) in h.sent
    h = _handler(state)
    h._security = lambda: None  # type: ignore[method-assign]
    h._guard(lambda: (_ for _ in ()).throw(CreateError("конфликт", "other")))
    assert ("status", 409) in h.sent
    h = _handler(state)
    h._security = lambda: None  # type: ignore[method-assign]
    h._guard(lambda: (_ for _ in ()).throw(BrokenPipeError()))
    h = _handler(state)
    h._security = lambda: None  # type: ignore[method-assign]
    h._guard(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ("status", 500) in h.sent


def test_get_routes(tmp_path: Path, monkeypatch) -> None:
    state = _state(tmp_path)
    h = _handler(state)
    h._get()
    assert ("status", 200) in h.sent
    assert any(key == "Content-Security-Policy" for key, _ in h.sent)

    h = _handler(state, path="/static/missing.js")
    h._get()
    assert ("status", 404) in h.sent
    h = _handler(state, path="/static/app.js")
    h._get()
    assert ("status", 404) in h.sent

    h = _handler(state, path="/api/graph")
    h._get()
    assert ("status", 200) in h.sent and _json_response(h)["version"] == 7
    h = _handler(state, path="/api/graph", headers={"If-None-Match": f'"{state.revision}"'})
    h._get()
    assert ("status", 304) in h.sent
    h = _handler(state, path="/api/state")
    h._get()
    assert _json_response(h) == {"revision": 0}
    h = _handler(state, path="/api/wait?rev=x")
    h._get()
    assert _json_response(h) == {"revision": 0}
    h = _handler(state, path="/api/wait?rev=5")
    h._get()
    assert _json_response(h) == {"revision": 0}
    h = _handler(state, path="/api/verify?brief=x")
    h._get()
    assert _json_response(h) == {"target": "x"}
    h = _handler(state, path="/nope")
    h._get()
    assert ("status", 404) in h.sent


def test_get_index_missing_and_static_oserror(tmp_path: Path, monkeypatch) -> None:
    state = _state(tmp_path)
    monkeypatch.setattr(app_mod, "INDEX", tmp_path / "missing.html")
    h = _handler(state)
    h._get()
    assert _json_response(h)["error"]["code"] == "ui_missing"
    monkeypatch.undo()

    static = tmp_path / "static"
    static.mkdir()
    target = static / "a.js"
    target.write_text("x", encoding="utf-8")
    h = _handler(state, path="/static/a.js")
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(OSError("нет")))
    h._get()
    assert ("status", 404) in h.sent
    monkeypatch.setattr(Path, "read_bytes", original)


def test_post_routes_and_methods(tmp_path: Path) -> None:
    state = _state(tmp_path)
    routes = {
        "/api/rescan": b"{}",
        "/api/metadata/node": b'{"node_id": "x"}',
        "/api/plan/preview": b"{}",
        "/api/plan": b"{}",
        "/api/plan/update": b"{}",
        "/api/plan/delete": b"{}",
        "/api/brief": b"{}",
        "/api/brief/save": b"{}",
    }
    for path, body in routes.items():
        h = _handler(
            state,
            path=path,
            command="POST",
            headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
            body=body,
        )
        h._post()
        assert h.wfile.getvalue(), path
    h = _handler(state, path="/nope", command="POST", headers={"Content-Length": "2"}, body=b"{}")
    h._post()
    assert ("status", 404) in h.sent
    for name in ("do_GET", "do_POST", "do_PUT", "do_DELETE", "do_PATCH"):
        h = _handler(state, command="POST" if name == "do_POST" else "GET")
        getattr(h, name)()
        assert h.sent
    h = _handler(state, command="GET")
    h.log_message("%s", "x")
    state.verbose = True
    h.log_message("%s", "x")


def test_bind_and_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    class FakeServer:
        def __init__(self, address, handler, bind_and_activate=False):
            self.server_address = address
            self.closed = False
            self.activated = False

        def server_bind(self):
            if self.server_address[1] == 1:
                raise OSError("занято")

        def server_activate(self):
            self.activated = True

        def server_close(self):
            self.closed = True

    monkeypatch.setattr(app_mod, "ThreadingHTTPServer", FakeServer)
    server = _try_bind("127.0.0.1", 0, object)
    assert server is not None and server.activated
    assert _try_bind("127.0.0.1", 1, object) is None
    assert _bind("127.0.0.1", 0, object).server_address[1] == 0
    with pytest.raises(app_mod.PortInUse):
        _bind("127.0.0.1", 1, object)
    assert isinstance(_bind("127.0.0.1", None, object), FakeServer)
    class AlwaysBusy(FakeServer):
        def server_bind(self):
            raise OSError("занято")

    monkeypatch.setattr(app_mod, "ThreadingHTTPServer", AlwaysBusy)
    with pytest.raises(app_mod.PortInUse):
        _bind("127.0.0.1", None, object)

    state = _state(tmp_path)
    _print_summary(state, "http://x")
    out = capsys.readouterr().out
    assert "studio dev" in out and "Эндпоинтов 1" in out


def test_serve_without_real_server(tmp_path: Path, monkeypatch, capsys) -> None:
    state = _state(tmp_path)
    state.rescan = lambda reason: state.graph
    state.start_watcher = lambda: None
    state.stop_watcher = lambda: None
    monkeypatch.setattr(ProjectState, "start_watcher", lambda self: None)
    monkeypatch.setattr(ProjectState, "stop_watcher", lambda self: None)
    monkeypatch.setattr(app_mod.slog, "is_active", lambda root: True)

    class FakeServer:
        server_address = ("127.0.0.1", 9999)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(app_mod, "_bind", lambda host, port, handler: FakeServer())
    monkeypatch.setattr(app_mod.os, "environ", {})
    assert serve(tmp_path, no_open=True) == 9999