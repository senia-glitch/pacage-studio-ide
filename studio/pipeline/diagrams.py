"""Диаграмма последовательности сценария и схема таблиц: приведение ответа агента к чистому виду.

Шаг диаграммы — «от кого → кому: что» с меткой: ``screen`` (экран → план интерфейса), ``call``
(вызов → контракт API и эндпоинт), ``data`` (данные → схема БД). Из меток рождаются этапы 2–4.
"""

from __future__ import annotations

import re

TAGS = ("screen", "call", "data")
MAX_PARTICIPANTS, MAX_STEPS = 12, 40
COLUMN_TYPES = ("int", "text", "bool", "float", "date", "datetime", "json", "uuid")
_NAME = re.compile(r"[^a-z0-9_]+")


def _s(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def normalize_diagram(raw) -> dict:
    """Чистая диаграмма: участники без повторов, у шага есть «от кого, кому, что» и верная метка."""
    raw = raw if isinstance(raw, dict) else {}
    participants: list[str] = []
    for item in raw.get("participants") or []:
        name = _s(item, 60)
        if name and name not in participants:
            participants.append(name)
    steps = []
    for item in (raw.get("steps") or [])[:MAX_STEPS]:
        if not isinstance(item, dict):
            continue
        source, target, text = (
            _s(item.get(k), n) for k, n in (("from", 60), ("to", 60), ("text", 300))
        )
        if not (source and target and text):
            continue
        tag = _s(item.get("tag"), 10).lower()
        steps.append(
            {"from": source, "to": target, "text": text, "tag": tag if tag in TAGS else ""}
        )
        for name in (source, target):
            if name not in participants:
                participants.append(name)
    return {"participants": participants[:MAX_PARTICIPANTS], "steps": steps}


def snake(name: str) -> str:
    """Имя таблицы или колонки в snake_case латиницей; пустое — если нечего сохранить."""
    text = _NAME.sub("_", str(name or "").strip().lower()).strip("_")
    return text[:60]


def normalize_columns(raw) -> list[dict]:
    """Колонки таблицы: имя, тип, ключ (pk / fk / ''), ссылка fk («таблица.колонка»)."""
    out, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = snake(item.get("name"))
        if not name or name in seen:
            continue
        seen.add(name)
        key = _s(item.get("key"), 2).lower()
        kind = _s(item.get("type"), 12).lower()
        ref = _s(item.get("ref"), 120).lower().replace(" ", "")
        out.append(
            {
                "name": name,
                "type": kind if kind in COLUMN_TYPES else "text",
                "key": key if key in ("pk", "fk") else "",
                "ref": ref if key == "fk" else "",
                "null": bool(item.get("null", False)),
            }
        )
    return out


def schema_problems(tables: list[dict]) -> list[str]:
    """Что не так со схемой: нет первичного ключа, ссылка в никуда, повтор имени таблицы."""
    names = {}
    problems = []
    for table in tables:
        name = table["name"]
        if name in names:
            problems.append(f"таблица «{name}» повторяется")
        names[name] = table
    for table in tables:
        cols = table.get("columns") or []
        if not any(c["key"] == "pk" for c in cols):
            problems.append(f"у таблицы «{table['name']}» нет первичного ключа")
        for col in cols:
            if col["key"] != "fk":
                continue
            target, _, column = col["ref"].partition(".")
            ref = names.get(target)
            if ref is None or (column and not any(c["name"] == column for c in ref["columns"])):
                where = f"{table['name']}.{col['name']}"
                problems.append(f"внешний ключ «{where}» ведёт в несуществующее «{col['ref']}»")
    return problems
