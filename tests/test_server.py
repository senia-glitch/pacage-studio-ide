"""Что проверяется: сервер интерфейса.

Описание словами: сервер отдаёт страницу интерфейса, граф, конфиг,
список нарушений и diff без записи; лог перезаписывается при старте
сервера, а каждый запрос дописывается в конец.
"""

import json
import socket
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

from studio import store
from studio.server import Ctx, make_handler, serve


def _demo(root):
    (root / "order" / "domain").mkdir(parents=True)
    (root / "order" / "domain" / "c.py").write_text(
        "from abc import ABC, abstractmethod\nclass C(ABC):\n"
        "    @abstractmethod\n    def do(self): ...\n", encoding="utf-8")


def _serve(root):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Ctx(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    return srv, f"http://127.0.0.1:{port}"


def _get(base, path):
    with urllib.request.urlopen(base + path) as r:
        return r.status, r.read().decode("utf-8")


def _put(base, path, data):
    req = urllib.request.Request(base + path, data=json.dumps(data).encode("utf-8"),
                                 method="PUT", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return r.status, r.read().decode("utf-8")


def test_смена_правил_в_рантайме_без_перезапуска(tmp_path):
    """Убрал routes->schemas из allowed через API — validate тут же видит нарушение."""
    (tmp_path / "schemas").mkdir(parents=True)
    (tmp_path / "routes").mkdir(parents=True)
    (tmp_path / "schemas" / "c.py").write_text(
        "from abc import ABC, abstractmethod\nclass C(ABC):\n"
        "    @abstractmethod\n    def do(self): ...\n", encoding="utf-8")
    (tmp_path / "routes" / "v.py").write_text(
        "def do():\n    return 1\n", encoding="utf-8")
    store.ensure_layout(tmp_path)
    srv, base = _serve(tmp_path)
    try:
        _get(base, "/api/graph")  # первый запрос сам строит граф по коду
        _, body = _get(base, "/api/validate")
        assert json.loads(body)["violations"] == [], "изначально стрелка разрешена"
        _, body = _get(base, "/api/config")
        cfg = json.loads(body)
        cfg["studio"]["rules"]["allowed"] = [
            p for p in cfg["studio"]["rules"]["allowed"] if p != ["routes", "schemas"]]
        _put(base, "/api/config", cfg)
        _, body = _get(base, "/api/validate")
        vs = json.loads(body)["violations"]
        assert len(vs) == 1 and vs[0]["from_layer"] == "routes"
    finally:
        srv.shutdown()


def test_сервер_отдаёт_всё_нужное(tmp_path):
    """Страница, граф, конфиг, нарушения и diff доступны; нарушений ноль."""
    _demo(tmp_path)
    store.ensure_layout(tmp_path)
    srv, base = _serve(tmp_path)
    try:
        s, body = _get(base, "/")
        assert s == 200 and "bSave" in body, "на странице должна быть кнопка Save"
        s, body = _get(base, "/api/graph")
        assert s == 200 and len(json.loads(body)["nodes"]) == 1
        s, body = _get(base, "/api/validate")
        assert s == 200 and json.loads(body)["violations"] == []
        s, body = _get(base, "/api/diff")
        assert s == 200 and "diff" in json.loads(body)
        s, body = _get(base, "/api/config")
        assert s == 200 and "domains" in json.loads(body)["studio"]
    finally:
        srv.shutdown()


def test_канвас_умеет_панораму_и_зум(tmp_path):
    """Страница содержит управление видом: панорама фоном, зум колесом и кнопками, вписать."""
    _demo(tmp_path)
    store.ensure_layout(tmp_path)
    srv, base = _serve(tmp_path)
    try:
        _, body = _get(base, "/")
        for marker in ("zoomLabel", "zIn", "zOut", "zFit", "zReset", "toWorld", "fitView"):
            assert marker in body, f"в странице нет {marker}"
    finally:
        srv.shutdown()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_dev_лог_старт_перезаписывает_а_запросы_дописываются(tmp_path):
    """Старт dev стирает старый лог и пишет заголовок; запрос виден в конце лога."""
    _demo(tmp_path)
    (tmp_path / ".studio").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".studio" / "studio.log").write_text("старый мусор\n", encoding="utf-8")
    port = _free_port()
    threading.Thread(target=serve, kwargs={"project_root": tmp_path,
                                           "host": "127.0.0.1", "port": port},
                     daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            _get(base, "/api/config")
            break
        except OSError:
            time.sleep(0.05)
    log = (tmp_path / ".studio" / "studio.log").read_text(encoding="utf-8")
    assert "старый мусор" not in log, "старт dev должен перезаписать лог"
    assert "studio dev" in log.splitlines()[0]
    assert "HTTP" in log and "/api/config" in log, "запросы должны дописываться"
