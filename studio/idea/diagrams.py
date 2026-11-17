"""Диаграмма последовательности процесса: хранение, проверка, разбор текста, экспорт.

Диаграмма лежит в ``attrs.diagram`` карточки как структура, а не как картинка:

    {"type": "sequence",
     "participants": ["Пациент", "Регистратура"],
     "steps": [{"from": "Пациент", "to": "Регистратура", "text": "Звонит и просит запись",
                "pain": {"what": "Не дозвониться", "often": "часто", "cost": "20 минут",
                         "importance": "высокая"},
                "mark": ""}]}

``pain`` — боль на шаге (необязательно, только в «как есть»); ``mark`` — отличие в «как будет»:
``new`` (новый шаг), ``changed`` (изменён), ``auto`` (автоматизирован), ``removed`` (убран).
Рисует диаграмму ``static/sequence.js``; здесь — то же самое для Python: проверка, разбор текста
«кто → кому: что» и экспорт в Mermaid (для документации).
"""

from __future__ import annotations

import re

IMPORTANCE = ("низкая", "средняя", "высокая", "критическая")
MARKS = ("", "new", "changed", "auto", "removed")
MARK_SIGNS = {"+": "new", "~": "changed", "*": "auto", "-": "removed"}
SIGN_OF = {v: k for k, v in MARK_SIGNS.items()}
MAX_PARTICIPANTS, MAX_STEPS = 12, 60
ARROW = re.compile(r"\s*(?:→|->|=>|-->)\s*")
PAIN = re.compile(r"\[\s*боль\s*:(.*?)\]\s*$", re.I)
BY = re.compile(r"\[\s*решение\s*:(.*?)\]\s*$", re.I)


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _pain(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    pain = {
        "what": _text(raw.get("what"), 200),
        "often": _text(raw.get("often"), 80),
        "cost": _text(raw.get("cost"), 120),
        "importance": _text(raw.get("importance"), 20).lower(),
    }
    if pain["importance"] not in IMPORTANCE:
        pain["importance"] = ""
    return pain if any(pain.values()) else None


def normalize(raw) -> dict:
    """Привести диаграмму к чистому виду; недостающие участники добавляются по шагам."""
    if not isinstance(raw, dict):
        raise ValueError("диаграмма должна быть объектом")
    participants: list[str] = []
    for item in raw.get("participants") or []:
        name = _text(item, 60)
        if name and name not in participants:
            participants.append(name)
    steps = []
    for item in (raw.get("steps") or [])[:MAX_STEPS]:
        if not isinstance(item, dict):
            continue
        source, target, text = (
            _text(item.get("from"), 60),
            _text(item.get("to"), 60),
            _text(item.get("text"), 300),
        )
        if not (source and target and text):
            continue
        for name in (source, target):
            if name not in participants:
                participants.append(name)
        step = {"from": source, "to": target, "text": text}
        pain = _pain(item.get("pain"))
        if pain:
            step["pain"] = pain
        mark = _text(item.get("mark"), 10).lower()
        step["mark"] = mark if mark in MARKS else ""
        by = _text(item.get("by"), 200)
        if by and step["mark"]:
            step["by"] = by  # какое выбранное решение вводит этот шаг («решение»)
        steps.append(step)
    if len(participants) > MAX_PARTICIPANTS:
        raise ValueError(f"участников не больше {MAX_PARTICIPANTS}")
    return {"type": "sequence", "participants": participants, "steps": steps}


def problems(raw) -> list[str]:
    """Что не так с диаграммой (пустой список — всё в порядке)."""
    try:
        diagram = normalize(raw)
    except ValueError as error:
        return [str(error)]
    out = []
    if not diagram["steps"]:
        out.append("в диаграмме нет шагов")
    if len(diagram["participants"]) < 2 and diagram["steps"]:
        out.append("в диаграмме меньше двух участников")
    declared = {_text(p, 60) for p in (raw.get("participants") or [])}
    for step in raw.get("steps") or []:
        if isinstance(step, dict):
            for name in (_text(step.get("from"), 60), _text(step.get("to"), 60)):
                if name and declared and name not in declared:
                    out.append(
                        f"участник «{name}» есть в шагах, но не объявлен в списке участников"
                    )
    return list(dict.fromkeys(out))


def parse_text(text: str) -> dict:
    """Разобрать текст шагов «кто → кому: что» в диаграмму.

    Строка: ``[N.] [знак] Кто → Кому: что происходит [боль: что | как часто | цена | важность]``;
    знак отличия: ``+`` новый, ``~`` изменён, ``*`` автоматизирован, ``-`` убран.
    Пустые и непонятные строки пропускаются.
    """
    steps = []
    for line in str(text or "").splitlines():
        line = re.sub(r"^\s*\d+[.)]\s*", "", line).strip()
        if not line:
            continue
        mark = ""
        if line[0] in MARK_SIGNS and (
            len(line) > 1 and line[1] in " \t" or not line[1:2].isalnum()
        ):
            mark, line = MARK_SIGNS[line[0]], line[1:].strip()
        pain, by = None, ""
        made = BY.search(line)
        if made:
            line, by = line[: made.start()].strip(), made.group(1).strip()
        found = PAIN.search(line)
        if found:
            line = line[: found.start()].strip()
            parts = [p.strip() for p in found.group(1).split("|")] + ["", "", "", ""]
            pain = {"what": parts[0], "often": parts[1], "cost": parts[2], "importance": parts[3]}
        head, _, text_part = line.partition(":")
        sides = ARROW.split(head.strip(), maxsplit=1)
        if len(sides) != 2 or not text_part.strip():
            continue
        step = {"from": sides[0], "to": sides[1], "text": text_part, "mark": mark, "by": by}
        if pain:
            step["pain"] = pain
        steps.append(step)
    return normalize({"participants": [], "steps": steps})


def to_text(diagram: dict) -> str:
    """Обратное преобразование: диаграмма → текст шагов (для правки в текстовом поле)."""
    lines = []
    for number, step in enumerate(normalize(diagram)["steps"], 1):
        sign = SIGN_OF.get(step.get("mark", ""), "")
        line = (
            f"{number}. {sign + ' ' if sign else ''}{step['from']} → {step['to']}: {step['text']}"
        )
        pain = step.get("pain")
        if pain:
            line += (
                f" [боль: {pain.get('what', '')} | {pain.get('often', '')} | "
                f"{pain.get('cost', '')} | {pain.get('importance', '')}]"
            )
        if step.get("by"):
            line += f" [решение: {step['by']}]"
        lines.append(line)
    return "\n".join(lines)


def to_mermaid(diagram: dict) -> str:
    """Экспорт в текст Mermaid (sequenceDiagram) — для документации."""
    clean = normalize(diagram)
    lines = ["sequenceDiagram"]
    for index, name in enumerate(clean["participants"], 1):
        lines.append(f"    participant P{index} as {name}")
    ids = {name: f"P{i}" for i, name in enumerate(clean["participants"], 1)}
    for step in clean["steps"]:
        text = step["text"].replace(";", ",")
        if step.get("mark") == "removed":
            text = "(убран) " + text
        lines.append(f"    {ids[step['from']]}->>{ids[step['to']]}: {text}")
        pain = step.get("pain")
        if pain:
            lines.append(f"    Note over {ids[step['to']]}: боль: {pain['what']}".rstrip(": "))
    return "\n".join(lines)


# ------------------------------------------------ диаграмма вариантов использования (UML)


def normalize_usecase(raw) -> dict:
    """Диаграмма вариантов использования: граница системы, варианты и актёры при каждом.

    ``{"system": "Запись к врачу", "cases": [{"name": "Выбрать время", "actors": ["Пациент"]}]}``
    """
    if not isinstance(raw, dict):
        raise ValueError("диаграмма должна быть объектом")
    cases = []
    for item in (raw.get("cases") or [])[:8]:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), 80)
        actors = []
        for actor in item.get("actors") or []:
            actor = _text(actor, 60)
            if actor and actor not in actors:
                actors.append(actor)
        if name and actors:
            cases.append({"name": name, "actors": actors[:6]})
    return {"system": _text(raw.get("system"), 80), "cases": cases}
