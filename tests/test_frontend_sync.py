"""Тесты промпта синхронизации описаний фронтенда с кодом."""

from __future__ import annotations

import pytest

from studio.agent.sync_json import SyncJsonError, extract_json
from studio.frontend.graph import build_front_graph
from studio.frontend.meta import load_meta
from studio.frontend.sync import build_front_sync_prompt, check_front_sync_response
from studio.server.app import frontend_sync_apply_op


def _graph(tmp_path):
    root = tmp_path / "project"
    frontend = root / "frontend"
    for rel in (
        "src/ui/pages/booking-view/index.vue",
        "src/ui/app/router.js",
        "src/ui/components/LessonBlock.vue",
        "src/ui/components/EmptyCard.vue",
        "src/ui/pages/reporting/index.vue",
    ):
        target = root / "frontend" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
    (frontend / "src/ui/pages/booking-view/index.vue").write_text(
        "<template><LessonBlock/><EmptyCard/></template>\n"
        "<script setup>\nimport LessonBlock from '../../components/LessonBlock.vue'\n"
        "import EmptyCard from '../../components/EmptyCard.vue'\n</script>",
        encoding="utf-8",
    )
    (frontend / "src/ui/components/LessonBlock.vue").write_text(
        "<template><span/></template>", encoding="utf-8"
    )
    (frontend / "src/ui/components/EmptyCard.vue").write_text(
        "<template><span/></template>", encoding="utf-8"
    )
    (frontend / "src/ui/pages/reporting/index.vue").write_text(
        "<template><div/></template>", encoding="utf-8"
    )
    (frontend / "src/ui/app/router.js").write_text(
        "export const bookingRouter = [\n"
        "  { path: '/booking-view', name: 'booking-view' },\n"
        "  { path: '/reporting', name: 'reporting' },\n]\n",
        encoding="utf-8",
    )
    graph = build_front_graph(root)
    return root, graph


def test_синхронизация_только_факты_и_таблица(tmp_path):
    root, graph = _graph(tmp_path)
    text = build_front_sync_prompt(graph, "all")
    assert "frontend.json" in text
    assert "not_synced" in text and "\"updates\"" in text
    # факты страниц
    assert "| страница | `page:reporting` |" in text
    assert "| страница | `page:booking-view` |" in text
    # блоки-факты
    assert "`block:booking-view:LessonBlock`" in text
    # плановых узлов в таблице нет (флаги в правилах текста не считаются)
    assert "| страница | `page-plan:" not in text
    assert "| блок | `block-plan:" not in text


def test_синхронизация_области_и_страницы(tmp_path):
    root, graph = _graph(tmp_path)
    # страница-узел: только booking-view
    text_page = build_front_sync_prompt(graph, "page", page_id="page:booking-view")
    assert "`page:booking-view`" in text_page
    assert "`page:reporting`" not in text_page
    assert text_page.count("| страница |") == 1


def test_синхронизация_только_без_описания(tmp_path):
    root, graph = _graph(tmp_path)
    from studio.frontend.meta import save_page_meta

    # даём описание странице booking-view: узел «без описания» окрываем другим узлом
    save_page_meta(root, {"id": "page:booking-view", "description": "Список броней"})
    fresh = build_front_graph(root)
    text = build_front_sync_prompt(fresh, "unready")
    assert "`page:booking-view`" not in text
    assert "`block:booking-view:EmptyCard`" in text  # блок без описания остаётся в unready
    assert "`page:reporting`" in text  # страница без описания тоже


def test_таблица_экранирует_вертикальную_черту_и_переводы_строк(tmp_path):
    root, _ = _graph(tmp_path)
    from studio.frontend.meta import save_page_meta

    save_page_meta(root, {"id": "page:reporting", "description": "а | б\nв"})
    text = build_front_sync_prompt(build_front_graph(root), "all")
    assert "а \\| б в" in text


def test_ответ_агента_проверка_отличий(tmp_path):
    root, graph = _graph(tmp_path)
    payload = {
        "updates": [
            {"id": "page:reporting", "description": "Отчёты", "acceptance": ["Есть таблица"]},
            {"id": "block:booking-view:LessonBlock", "description": "Урок", "acceptance": []},
            {"id": "page:ghost", "description": "x", "acceptance": []},
            {"id": "page:reporting", "description": "дубль", "acceptance": []},
            {"id": "page:booking-view", "description": "", "acceptance": []},
            {"id": "page:booking-view", "description": "ok", "acceptance": "не список"},
        ],
        "not_synced": [{"id": "block:booking-view:EmptyCard", "reason": "не понял"}],
    }
    report, _ = check_front_sync_response(graph, payload)
    statuses = [row["status"] for row in report["rows"]]
    assert statuses == ["change", "change"] + ["rejected"] * 4
    assert report["counts"] == {"change": 2, "same": 0, "rejected": 4, "not_synced": 1}
    assert "не найден" in report["rows"][2]["reason"]
    assert "повторяется" in report["rows"][3]["reason"]


def test_ответ_агента_применение_только_по_apply(tmp_path):
    root, graph = _graph(tmp_path)
    body = (
        '{"updates": [{"id": "page:reporting", "description": "Отчёты",'
        ' "acceptance": ["Есть таблица"]},'
        ' {"id": "block:booking-view:LessonBlock", "description": "Урок", "acceptance": []}]}'
    )
    raw = "Вот результат:\n```json\n" + body + "\n```\nГотово."
    preview = frontend_sync_apply_op(root, {"json": raw})
    assert preview["applied"] == 0
    assert not load_meta(root)["pages"].get("page:reporting", {}).get("description")

    done = frontend_sync_apply_op(root, {"json": raw, "apply": True, "ids": ["page:reporting"]})
    assert done["applied"] == 1
    assert load_meta(root)["pages"]["page:reporting"]["description"] == "Отчёты"
    block_page = load_meta(root)["pages"].get("page:booking-view", {})
    assert "block:booking-view:LessonBlock" not in (block_page.get("blocks") or {})

    again = frontend_sync_apply_op(root, {"json": raw})
    assert again["counts"]["same"] == 1


def test_извлечение_json_и_ошибки():
    assert extract_json('{"updates": []}') == {"updates": []}
    assert extract_json('текст {"updates": []} конец') == {"updates": []}
    with pytest.raises(SyncJsonError):
        extract_json("   ")
    with pytest.raises(SyncJsonError):
        extract_json("никакого json здесь нет")


def test_тз_фронтенда_требует_json_отчёт(tmp_path):
    from studio.frontend.brief import brief_frontend

    root, _ = _graph(tmp_path)
    text = brief_frontend(root)["markdown"]
    assert "## 6. Итоговый отчёт (JSON)" in text
    assert '"id": "page:<папка страницы>"' in text


def test_отчёт_агента_фронтенда_показывает_report_и_отклоняет_отсутствующее(tmp_path):
    root, _ = _graph(tmp_path)
    raw = {
        "updates": [
            {"id": "page:reporting", "description": "Отчёты", "acceptance": []},
            {"id": "page:новая", "description": "нет в коде", "acceptance": []},
        ],
        "report": {"tests": "vitest: ok", "questions": ["Нужен ли фильтр?"]},
    }
    result = frontend_sync_apply_op(root, {"json": raw})
    assert [row["status"] for row in result["rows"]] == ["change", "rejected"]
    assert result["report"]["tests"] == "vitest: ok"
    assert result["report"]["questions"] == ["Нужен ли фильтр?"]


def test_отмена_применения_фронтенда(tmp_path):
    from studio.server.app import frontend_sync_undo_op

    root, _ = _graph(tmp_path)
    raw = {
        "updates": [
            {"id": "page:reporting", "description": "Отчёты", "acceptance": ["Таблица"]},
            {"id": "block:booking-view:LessonBlock", "description": "Урок", "acceptance": []},
        ]
    }
    done = frontend_sync_apply_op(root, {"json": raw, "apply": True})
    assert done["applied"] == 2 and len(done["undo"]) == 2

    # страницу правят после применения — её отмена пропускается, блок возвращается
    from studio.frontend.meta import save_page_meta

    save_page_meta(root, {"id": "page:reporting", "description": "Моя правка"})
    undone = frontend_sync_undo_op(root, {"undo": done["undo"]})
    assert undone["restored"] == 1 and undone["skipped"] == ["page:reporting"]
    meta = load_meta(root)["pages"]
    assert meta["page:reporting"]["description"] == "Моя правка"
    block = meta["page:booking-view"]["blocks"]["block:booking-view:LessonBlock"]
    assert block["description"] == ""

    with pytest.raises(Exception, match="отмен"):
        frontend_sync_undo_op(root, {"undo": []})
