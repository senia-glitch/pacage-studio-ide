"""Совместимость: проект со старой папкой `.studio` (до 5.15) открывается и работает."""

from __future__ import annotations

import json
from pathlib import Path

from test_frontend_links import _project

from studio import cli
from studio.frontend.brief import brief_frontend, load_front_brief
from studio.frontend.graph import build_front_graph
from studio.server.state import ProjectState

OLD_FRONTEND = {
    "version": 1,
    "domains": [{"id": "domain:1", "name": "клиент"}],
    "pages": {
        "page:booking-view": {
            "id": "page:booking-view", "description": "Список броней",
            "acceptance": ["Фильтр по датам"], "domain": "клиент", "blocks": {}, "rules": [],
        },
        "page-plan:old": {
            "id": "page-plan:old", "planned": True, "name": "старая-страница",
            "description": "Плановая страница из 5.14", "route": "/old",
            "blocks": {"block-plan:b": {"id": "block-plan:b", "planned": True, "name": "OldBlock",
                                        "description": "блок", "rules": []}},
            "rules": [{"id": "rule:r", "name": "Правило", "description": "задача"}],
        },
    },
}
OLD_IDEAS = {
    "version": 1, "activeId": "a",
    "ideas": [{"id": "a", "title": "Старая идея", "description": "из 5.14",
               "scenarios": [{"id": "s", "title": "Сценарий", "description": "d",
                              "acceptance": [], "domain": "", "source": {}}]}],
}


def _old_project(tmp_path: Path) -> Path:
    root, _ = _project(tmp_path)
    studio = root / ".studio"
    studio.mkdir(exist_ok=True)
    (studio / "frontend.json").write_text(json.dumps(OLD_FRONTEND, ensure_ascii=False),
                                          encoding="utf-8")
    (studio / "ideas.json").write_text(json.dumps(OLD_IDEAS, ensure_ascii=False),
                                       encoding="utf-8")
    return root


def test_старые_метаданные_фронтенда_читаются_с_пустыми_новыми_полями(tmp_path: Path) -> None:
    root = _old_project(tmp_path)
    graph = build_front_graph(root)
    pages = {page["id"]: page for page in graph["pages"]}
    for page in pages.values():
        assert page["calls"] == [] and not page["created_by"]
        for block in page["blocks"]:
            assert block["calls"] == [] and not block["created_by"]
    assert pages["page:booking-view"]["description"] == "Список броней"
    assert pages["page-plan:old"]["blocks"][0]["name"] == "OldBlock"

    state = ProjectState(root)
    state.rescan("start")
    links = state.check_front_links(graph)
    assert links == {"rows": [], "ok": 0, "missing": 0, "in_code": [], "in_code_missing": 0}


def test_старое_тз_фронтенда_без_маркера_даёт_понятную_ошибку_и_пересобирается(
    tmp_path: Path, capsys
) -> None:
    root = _old_project(tmp_path)
    (root / ".studio" / "agent-brief-frontend.md").write_text(
        "# ТЗ из 5.14: без маркера\n", encoding="utf-8"
    )
    assert cli.main(["verify", "--part", "frontend", "--project", str(root)]) == 2
    captured = capsys.readouterr()
    assert "маркера" in captured.err + captured.out
    result = brief_frontend(root)
    assert "studio-front-brief" in result["markdown"]
    assert load_front_brief(root)["brief_id"] == result["brief_id"]


def test_старая_доска_идей_получает_ревизию_и_сохраняется(tmp_path: Path) -> None:
    root = _old_project(tmp_path)
    state = ProjectState(root)
    board = state.load_ideas()
    assert board["ideas"][0]["title"] == "Старая идея" and "revision" not in board
    saved = state.save_ideas({**board, "revision": 0})
    assert saved["revision"] == 1
    again = state.load_ideas()
    assert again["ideas"][0]["scenarios"][0]["title"] == "Сценарий"
    assert "created_by" not in again["ideas"][0]  # идеи 5.14 — человека, не агента


def test_проект_без_папки_отчётов_и_идей_работает(demo: Path) -> None:
    from studio.agent import inbox

    assert inbox.list_reports(demo) == []
    state = ProjectState(demo)
    assert state.check_aux() is False
    assert state.load_ideas() == {"version": 1, "activeId": "", "ideas": []}
