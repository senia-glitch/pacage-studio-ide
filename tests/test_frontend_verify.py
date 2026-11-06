"""Проверка выполнения ТЗ фронтенда: маркер ТЗ, verify по коду, CLI и HTTP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_frontend_links import _project

from studio import cli
from studio.agent.verify import STATUS_DONE, STATUS_NOT_DONE, STATUS_PARTIAL
from studio.frontend import meta as fm
from studio.frontend.brief import brief_frontend, load_front_brief
from studio.frontend.graph import build_front_graph
from studio.frontend.verify import render_front_report, verify_front_brief
from studio.server.app import frontend_brief_last, frontend_verify_op
from studio.server.state import ProjectState

PAGE = "page:booking-view"


def _plan(root: Path) -> None:
    """Плановая страница, плановый блок, удаление блока и невыполненное правило."""
    fm.save_page_meta(root, {"id": "page-plan:o1", "name": "orders-list", "plannedNew": True,
                             "route": "/orders", "description": "Заказы",
                             "calls": ["GET /orders/list"]})
    fm.save_block_meta(root, {"pageId": PAGE, "id": "block-plan:c1", "name": "ClockBlock",
                              "description": "Часы", "calls": ["GET /ghost"]})
    fm.save_block_meta(root, {"pageId": PAGE, "id": "block:booking-view:EmptyCard",
                              "description": "Пустая карточка"})
    fm.save_delete_note(root, {"pageId": PAGE, "blockId": "block:booking-view:EmptyCard",
                               "note": "Не нужна", "acceptance": []})
    fm.save_rule(root, {"pageId": "page-plan:o1", "id": "rule:r1", "name": "Пагинация",
                        "description": "По 20 строк"})


def _implement(root: Path) -> None:
    src = root / "frontend" / "src" / "ui"
    page = src / "pages" / "orders-list"
    page.mkdir(parents=True, exist_ok=True)
    (page / "index.vue").write_text("<template><div/></template>", encoding="utf-8")
    router = src / "app" / "router.js"
    router.write_text(
        router.read_text(encoding="utf-8").replace(
            "]\n", "  { path: '/orders', name: 'orders-list' },\n]\n"
        ),
        encoding="utf-8",
    )
    (src / "components" / "ClockBlock.vue").write_text(
        "<template><span/></template>", encoding="utf-8"
    )
    # на странице используется ClockBlock и больше не используется EmptyCard
    (src / "pages" / "booking-view" / "index.vue").write_text(
        "<template><LessonBlock/><ClockBlock/></template>\n"
        "<script setup>\nimport LessonBlock from '../../components/LessonBlock.vue'\n"
        "import ClockBlock from '../../components/ClockBlock.vue'\n</script>",
        encoding="utf-8",
    )
    fm.save_rule(root, {"pageId": "page-plan:o1", "id": "rule:r1", "done": True})


def _by_kind(report: dict) -> dict:
    return {item["kind"]: item for item in report["items"]}


def test_маркер_и_задачи_в_тз_фронтенда(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)
    _plan(root)
    result = brief_frontend(root)
    assert "<!-- studio-front-brief:v1 " in result["markdown"]
    brief = load_front_brief(root)
    assert brief["brief_id"] == result["brief_id"]
    assert {t["kind"] for t in brief["tasks"]} == {"page", "block", "block_deletion", "rule"}
    assert [t["number"] for t in brief["tasks"]] == list(range(1, len(brief["tasks"]) + 1))
    assert (root / ".studio" / "last-front-brief.json").is_file()
    # то же из файла с маркером
    by_path = load_front_brief(root, ".studio/agent-brief-frontend.md")
    assert by_path == brief


def test_нет_тз_или_маркера(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)
    with pytest.raises(FileNotFoundError, match="сформируйте"):
        load_front_brief(root)
    (root / "x.md").write_text("без маркера", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="маркера"):
        load_front_brief(root, "x.md")


def test_verify_до_и_после_реализации(tmp_path: Path) -> None:
    root, state = _project(tmp_path)
    _plan(root)
    brief_frontend(root)
    brief = load_front_brief(root)

    front = build_front_graph(root)
    before = verify_front_brief(front, brief, state.check_front_links(front))
    assert before["status"] == "не сделано" and before["code"] == 1
    assert all(item["status"] == STATUS_NOT_DONE for item in before["items"])

    _implement(root)
    front = build_front_graph(root)
    after = verify_front_brief(front, brief, state.check_front_links(front))
    kinds = _by_kind(after)
    assert kinds["page"]["status"] == STATUS_DONE
    # вызов GET /ghost не найден в бэкенде — блок есть в коде, но «частично»
    assert kinds["block"]["status"] == STATUS_PARTIAL and "/ghost" in kinds["block"]["message"]
    assert kinds["block_deletion"]["status"] == STATUS_DONE
    assert kinds["rule"]["status"] == STATUS_DONE
    assert after["status"] == "частично" and after["code"] == 1

    fm.save_block_meta(root, {"pageId": PAGE, "id": "block-plan:c1", "calls": ["GET /health"]})
    brief_frontend(root)
    front = build_front_graph(root)
    final = verify_front_brief(
        front, load_front_brief(root), state.check_front_links(front)
    )
    assert final["status"] == "выполнено" and final["code"] == 0
    # выполненное правило в новое ТЗ уже не попадает
    assert "выполнено 3 из 3" in render_front_report(final)


def test_маршрут_страницы_должен_совпасть(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)
    _plan(root)
    brief_frontend(root)
    _implement(root)
    fm.save_page_meta(root, {"id": "page-plan:o1", "route": "/other"})
    brief_frontend(root)
    front = build_front_graph(root)
    report = verify_front_brief(front, load_front_brief(root))
    page = _by_kind(report)["page"]
    assert page["status"] == STATUS_PARTIAL and "/orders" in page["message"]


def test_verify_нарушений_в_области_violations(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)
    result = brief_frontend(root, scope="violations")
    brief = load_front_brief(root)
    kinds = {t["kind"] for t in brief["tasks"]}
    assert kinds <= {"violation"} and result["tasks"] == len(brief["tasks"])
    if brief["tasks"]:
        first = brief["tasks"][0]
        report = verify_front_brief(build_front_graph(root), brief)
        assert report["items"][0]["status"] == STATUS_NOT_DONE
        assert report["items"][0]["name"] == first["code"]


def test_неизвестная_задача_и_пустое_тз() -> None:
    report = verify_front_brief({}, {"tasks": [{"number": 1, "kind": "что-то"}]})
    assert report["items"][0]["status"] == STATUS_NOT_DONE
    assert verify_front_brief({}, {})["status"] == "нет задач"


# ----------------------------------------------------------- CLI и HTTP


def test_cli_verify_part_frontend(tmp_path: Path, capsys) -> None:
    root, _ = _project(tmp_path)
    assert cli.main(["verify", "--part", "frontend", "--project", str(root)]) == 2
    _plan(root)
    brief_frontend(root)
    capsys.readouterr()
    assert cli.main(["verify", "--part", "frontend", "--project", str(root)]) == 1
    assert "ТЗ фронтенда" in capsys.readouterr().out
    assert cli.main(["verify", "--part", "frontend", "--json", "--project", str(root)]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 4 and data["status"] == "не сделано"


def test_операции_api_verify_и_last(tmp_path: Path) -> None:
    from studio.agent import BriefNotFound

    root, _ = _project(tmp_path)
    state = ProjectState(root)
    state.rescan("start")
    with pytest.raises(BriefNotFound):
        frontend_verify_op(root, None, state.check_front_links)
    with pytest.raises(BriefNotFound):
        frontend_brief_last(root)
    _plan(root)
    brief_frontend(root)
    report = frontend_verify_op(root, "last", state.check_front_links)
    assert report["total"] == 4
    last = frontend_brief_last(root)
    assert last["brief_id"] == report["brief_id"] and len(last["tasks"]) == 4
    assert "studio-front-brief" in last["markdown"]


@pytest.mark.integration
def test_http_агент_реализации_читает_тз_и_проверяет(tmp_path: Path) -> None:
    import urllib.error
    import urllib.request

    from test_server import _Server

    from studio.server import access

    root, _ = _project(tmp_path)
    _plan(root)
    with _Server(root) as srv:
        token = access.derive_token(srv.token, "implement")

        def get(path):
            request = urllib.request.Request(srv.url + path, headers={"X-Studio-Token": token})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                try:
                    return exc.code, json.loads(exc.read().decode("utf-8"))
                finally:
                    exc.close()

        assert get("/api/frontend/brief/last")[0] == 404
        assert get("/api/frontend/verify")[0] == 404
        status, _ = srv.post("/api/frontend/brief", {"scope": "all"})
        assert status == 200
        status, last = get("/api/frontend/brief/last")
        assert status == 200 and len(last["tasks"]) == 4
        status, report = get("/api/frontend/verify?brief=last")
        assert status == 200 and report["status"] == "не сделано"
        # агенту пересобирать ТЗ фронтенда нельзя
        status, body = srv.post("/api/frontend/brief", {"scope": "all"},
                                {"X-Studio-Token": token})
        assert status == 403 and body["error"]["code"] == "mode_forbidden"
