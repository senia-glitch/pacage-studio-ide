"""Что проверяется: сервер интерфейса (просмотр + служебные эндпоинты).

Описание словами: сервер отдаёт страницу, граф, список сценариев и код файла;
граф автоматически сканируется при отсутствии или старой версии; неизвестные
пути — 404; лог перезаписывается при старте, запросы дописываются.
Изменяющие эндпоинты (план/scaffold/export) — в test_server_mutations.py.
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from studio import store
from studio.server import Ctx, load_graph, make_handler, scenarios_payload, serve


def _serve(root: Path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Ctx(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _request(base, path, method="GET"):
    req = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestPage:
    def test_страница_отдаётся(self, demo):
        srv, base = _serve(demo)
        try:
            status, body = _request(base, "/")
            assert status == 200
            assert "text/html" in urllib.request.urlopen(base + "/").headers["Content-Type"]
            assert "index.html" not in body
        finally:
            srv.shutdown()

    def test_управление_видом_на_месте(self, demo):
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/")
            for marker in ("zoomLabel", "zIn", "zOut", "zFit", "zReset", "toWorld", "fitView",
                           "hcat", "Режим", "Масштаб", "Действия"):
                assert marker in body, f"в странице нет {marker}"
            assert "Подробно" not in body, "режим «Подробно» удалён"
        finally:
            srv.shutdown()

    def test_редактирование_включается_кнопкой(self, demo):
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/")
            for marker in ("btnSave", "vView", "vEdit", "palette", "applyGraph", "nodeIdFor", "layoutPlanOrphans"):
                assert marker in body, f"в странице нет {marker}"
            for marker in ("bValidate", "bDiff", "bConfig", "bScan"):
                assert marker not in body, f"кнопка {marker} не должна существовать"
        finally:
            srv.shutdown()

    def test_переключатели_содержимого_на_месте(self, demo):
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/")
            for marker in ("btnDesc", "btnAcc", "contentSeg",
                           "node-sec", "node-sec-label", "node-sec-body",
                           "node-sec-divider", "ds-full", "acc-full",
                           "showDesc", "showAcc", "saveContentToggles",
                           "initContentToggles",
                           "studio.showDescription", "studio.showAcceptance"):
                assert marker in body, f"в странице нет {marker}"
        finally:
            srv.shutdown()

    def test_группировка_метод_домен_на_месте(self, demo):
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/")
            for marker in ("scenSearch", "scenarioGroupsByMethodDomain",
                           "methodOfScenario", "domainOfScenario",
                           "METHOD_ORDER", "METHOD_COLORS", "METHOD_LABELS",
                           "NO_METHOD", "NO_DOMAIN",
                           "dup-badge", "method-plate", "domain-plate",
                           "fDomain", "matchesFilter"):
                assert marker in body, f"в странице нет {marker}"
        finally:
            srv.shutdown()

    def test_кнопка_синхронизации_на_месте(self, demo):
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/")
            for marker in ("btnSync", "onSync", "/api/metadata/preview",
                           "/api/metadata/apply", "diff-add", "diff-del"):
                assert marker in body, f"в странице нет {marker}"
        finally:
            srv.shutdown()

    def test_панель_и_edit_подсказки_на_месте(self, demo):
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/")
            for marker in ("panelState", "panel-legend", "editStatus", "Создать узел",
                           "palette-hint", "PALETTE_HINTS", "errMsg", "insp-heading",
                           "renderCreateForm", "createNodeFromForm", "kindTabs", "bCreateNode"):
                assert marker in body, f"в странице нет {marker}"
        finally:
            srv.shutdown()

    def test_связи_перетаскиванием_на_месте(self, demo):
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/")
            for marker in ("startEdgeDraft", "finishEdgeDraft", "createEdgeFromPorts",
                           "edge-drafting", "portCenter"):
                assert marker in body, f"в странице нет {marker}"
            assert "onPortClick" not in body, "двухкликовый механизм заменён на drag"
        finally:
            srv.shutdown()


class TestApi:
    def test_граф_версии_4(self, demo):
        srv, base = _serve(demo)
        try:
            status, body = _request(base, "/api/graph")
            assert status == 200
            graph = json.loads(body)
            assert graph["version"] == 4
            assert len(graph["endpoints"]) == 4
            assert len(graph["scenarios"]) == 4
            by_id = {n["id"]: n for n in graph["nodes"]}
            scen = next(n for n in graph["nodes"] if n["id"].endswith("CreateOrderScenario"))
            assert scen["origin"] == "code"
            model = by_id["model:models.py:User"]
            assert model["origin"] == "code"
            ep = next(n for n in graph["nodes"] if n["id"].endswith("create_order") and n["kind"] == "endpoint")
            assert ep["origin"] == "code"
        finally:
            srv.shutdown()

    def test_граф_строится_сам(self, tmp_path):
        srv, base = _serve(tmp_path)
        try:
            _, body = _request(base, "/api/graph")
            assert json.loads(body)["version"] == 4
            assert store.graph_path(tmp_path).is_file()
        finally:
            srv.shutdown()

    def test_старая_версия_перестраивается(self, demo):
        store.write_graph(demo, load_graph(demo))
        raw = json.loads(store.graph_path(demo).read_text(encoding="utf-8"))
        raw["version"] = 1
        store.graph_path(demo).write_text(json.dumps(raw), encoding="utf-8")
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/api/graph")
            assert json.loads(body)["version"] == 4
        finally:
            srv.shutdown()

    def test_список_сценариев(self, demo):
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/api/scenarios")
            data = json.loads(body)
            names = {s["name"] for s in data["scenarios"]}
            assert names == {"create_order", "charge_payment", "no_endpoint_scenario", "payout"}
            create = next(s for s in data["scenarios"] if s["name"] == "create_order")
            assert create["endpoints"][0]["method"] == "post"
            assert create["endpoints"][0]["path"] == "/create"
            assert create["models"] == ["model:models.py:Order"]
        finally:
            srv.shutdown()

    def test_payload_без_падения(self, demo):
        graph = load_graph(demo)
        payload = scenarios_payload(graph)
        assert len(payload["scenarios"]) == len(graph.scenarios)

    def test_чтение_файла(self, demo):
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/api/file?path=models.py")
            data = json.loads(body)
            assert "class User" in data["code"]
        finally:
            srv.shutdown()

    def test_чтение_за_границей_пустое(self, demo):
        outside = demo.parent / "outside_secret.txt"
        outside.write_text("SECRET", encoding="utf-8")
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/api/file?path=../outside_secret.txt")
            assert json.loads(body)["code"] == ""
        finally:
            srv.shutdown()

    def test_мусорный_путь_пустой(self, demo):
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/api/file?path=nope.py")
            assert json.loads(body)["code"] == ""
            _, body = _request(base, "/api/file")
            assert json.loads(body)["code"] == ""
        finally:
            srv.shutdown()

    def test_api_file_абсолютный_путь_пусто(self, demo):
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/api/file?path=/etc/passwd")
            assert json.loads(body)["code"] == ""
        finally:
            srv.shutdown()

    def test_api_file_null_byte_пусто(self, demo):
        srv, base = _serve(demo)
        try:
            _, body = _request(base, "/api/file?path=models.py%00.txt")
            assert json.loads(body)["code"] == ""
        finally:
            srv.shutdown()


class TestReadOnly:
    def test_изменяющие_методы_404(self, demo):
        srv, base = _serve(demo)
        try:
            for method in ("POST", "PUT", "DELETE", "PATCH"):
                status, _ = _request(base, "/api/save", method=method)
                assert status == 404, f"{method} должен быть 404"
        finally:
            srv.shutdown()

    def test_неизвестный_путь_404(self, demo):
        srv, base = _serve(demo)
        try:
            status, _ = _request(base, "/api/validate")
            assert status == 404
            status, _ = _request(base, "/nope")
            assert status == 404
        finally:
            srv.shutdown()

    def test_скан_не_меняет_код(self, demo):
        before = {p: p.read_bytes() for p in demo.rglob("*.py")}
        srv, base = _serve(demo)
        try:
            _request(base, "/api/graph")
        finally:
            srv.shutdown()
        for path, data in before.items():
            assert path.read_bytes() == data


class TestLog:
    def test_лог_старт_перезаписывает_а_запросы_дописываются(self, demo):
        (demo / ".studio").mkdir(exist_ok=True)
        (demo / ".studio" / "studio.log").write_text("старый мусор\n", encoding="utf-8")
        port = _free_port()
        threading.Thread(
            target=serve,
            kwargs={"project_root": demo, "host": "127.0.0.1", "port": port},
            daemon=True,
        ).start()
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                _request(base, "/api/graph")
                break
            except OSError:
                time.sleep(0.05)
        log = (demo / ".studio" / "studio.log").read_text(encoding="utf-8")
        assert "старый мусор" not in log
        assert "studio dev" in log.splitlines()[0]
        assert "HTTP" in log and "/api/graph" in log
