"""ТЗ по частям: разбор на разделы и задачи, постраничная выдача, оглавление."""

from __future__ import annotations

from studio.agent import brief_parts

BRIEF = (
    "# ТЗ\n\nшапка\n\n## 1. Что нужно сделать\n\nТаблица\n\n## 2. Правила\n\n### Подзаголовок\nтекст\n\n"
    "## 4. Задачи\n\n### T1. План «Список»\nпервая\n\n### T2. План «Создать»\nвторая\nещё строка\n\n"
    "## 5. Карта\n\nкарта\n"
)


def test_разбор_на_разделы_и_задачи() -> None:
    parts = brief_parts.split_brief(BRIEF)
    assert [s["key"] for s in parts["sections"]] == ["1", "2", "4", "5"]
    assert [t["number"] for t in parts["tasks"]] == [1, 2]
    first, second = parts["tasks"]
    assert first["title"] == "План «Список»" and "первая" in first["text"]
    assert "вторая" in second["text"] and "ещё строка" in second["text"]
    assert "вторая" not in first["text"] and "Карта" not in second["text"]
    rules = next(s for s in parts["sections"] if s["key"] == "2")
    assert "Подзаголовок" in rules["text"] and "первая" not in rules["text"]
    tasks_section = next(s for s in parts["sections"] if s["key"] == "4")
    assert "первая" in tasks_section["text"] and "вторая" in tasks_section["text"]
    assert brief_parts.split_brief("") == {"sections": [], "tasks": []}


def test_оглавление_сжимает_записи_задач() -> None:
    records = [{"number": 1, "type": "plan", "slug": "list_x", "layers": [1] * 100},
               {"number": 2, "kind": "endpoint", "anchor": "get_x"}, {"number": "x"}]
    result = brief_parts.outline(BRIEF, records)
    assert result["chars"] == len(BRIEF)
    assert [(t["number"], t["kind"], t["name"]) for t in result["tasks"]] == [
        (1, "plan", "list_x"), (2, "endpoint", "get_x")]
    assert all(t["chars"] > 0 for t in result["tasks"])
    assert [s["key"] for s in result["sections"]] == ["1", "2", "4", "5"]
    assert "layers" not in str(result)  # тяжёлые поля записей в оглавление не попадают
    assert brief_parts.outline("", None) == {"chars": 0, "tasks": [], "sections": []}


def test_постраничная_выдача_длинного_текста() -> None:
    text = "а" * 25
    first = brief_parts.page(text, 0, limit=10)
    assert first == {"markdown": "а" * 10, "offset": 0, "next_offset": 10, "chars": 25}
    last = brief_parts.page(text, 20, limit=10)
    assert last["markdown"] == "а" * 5 and last["next_offset"] is None
    assert brief_parts.page(text, -5, limit=100)["offset"] == 0
    assert brief_parts.page("короткий")["next_offset"] is None


def test_пустые_разделы_помечаются() -> None:
    text = (
        "## 3. Правила проекта\n\nПравила проекта не заданы (файла нет).\n\n"
        "## 5. Карта\n\n- `GET /x` связан с чем-то и описан достаточно подробно, "
        "чтобы это было настоящим содержанием раздела, а не заглушкой.\n\n"
        "## 6. Не готово\n\n(нет)\n\n## 7. Пусто\n"
    )
    flags = {s["key"]: s["empty"] for s in brief_parts.outline(text)["sections"]}
    assert flags == {"3": True, "5": False, "6": True, "7": True}
