"""Жёсткие правила для того, что создают агенты: нарушение — отказ с точным текстом, агент исправляет и повторяет.

Цель: после создания или улучшения в графе уже нет разногласий, которые потом пришлось бы исправлять.
"""

from __future__ import annotations

from . import flow

MAX_TEXT = 250            # описание проблемы и варианта решения: 1-2 предложения
MAX_STEPS = 12            # шагов в диаграмме
QUESTION_MAX = 120        # вопрос агента человеку
OPTION_MAX = 60           # вариант ответа
MARKED = ("new", "changed", "auto", "removed")


def text_errors(kind: str, body: str) -> list[str]:
    if kind in (flow.PROBLEM, flow.VARIANT) and len(body) > MAX_TEXT:
        return [f"описание длиннее {MAX_TEXT} символов (сейчас {len(body)}): перепиши в 1-2 коротких предложения"]
    return []


def question_errors(question: str, titles: list[str]) -> list[str]:
    out = []
    if len(question) > QUESTION_MAX:
        out.append(f"вопрос длиннее {QUESTION_MAX} символов (сейчас {len(question)}): задай один простой вопрос про одно")
    out += [f"вариант «{t[:30]}…» длиннее {OPTION_MAX} символов: сделай его короче" for t in titles if len(t) > OPTION_MAX]
    return out


def canonical_names(diagram: dict, names: list[str]) -> dict:
    """Имя участника, совпавшее со списком с точностью до регистра и пробелов, заменяется на имя из списка."""
    from . import diagrams

    canon = {" ".join(n.split()).lower(): n for n in names}
    fix = lambda value: canon.get(" ".join(str(value).split()).lower(), value)       # noqa: E731
    return diagrams.normalize({"participants": [fix(p) for p in diagram["participants"]],
                               "steps": [{**s, "from": fix(s["from"]), "to": fix(s["to"])} for s in diagram["steps"]]})


def participant_errors(diagram: dict, names: list[str]) -> list[str]:
    """Единое пространство имён: участник диаграммы — только роль или компонент из списка."""
    if not names:
        return ["не определены роли и компоненты: сначала нужен список имён"]
    unknown = [p for p in diagram["participants"] if p not in names]
    return [f"участники вне единого списка имён: {', '.join(unknown)}; допустимы только: {', '.join(names)}"] if unknown else []


def diagram_errors(kind: str, diagram: dict, project, scenario_id: str) -> list[str]:
    """Диаграмма «как есть» или «решение» должна точно соответствовать принятым проблемам и выбранным решениям."""
    steps = diagram["steps"]
    out = participant_errors(diagram, project._role_names())
    if len(steps) > MAX_STEPS:
        out.append(f"шагов {len(steps)}, нужно не больше {MAX_STEPS}: объедини мелкие")
    for number, step in enumerate(steps, 1):
        if step["from"].lower() == step["to"].lower():
            out.append(f"шаг {number}: «{step['from']}» отправляет сообщение сам себе — замени на шаг между двумя разными участниками или убери")
    if kind == flow.ASIS:
        titles = {p["title"].strip().lower(): p["title"] for p in project._accepted(flow.PROBLEM, scenario_id)}
        marked = {}
        for number, step in enumerate(steps, 1):
            what = str((step.get("pain") or {}).get("what") or "").strip().lower()
            if what:
                marked.setdefault(what, number)
        for key, title in titles.items():
            if key not in marked:
                out.append(f"принятая проблема «{title}» не отмечена ни на одном шаге: у подходящего шага добавь pain.what ровно «{title}»")
        for key, number in marked.items():
            if key not in titles:
                out.append(f"шаг {number}: pain.what «{key}» — не принятая проблема; бери только точные названия: " + "; ".join(titles.values()))
    else:
        chosen = {s["title"].strip().lower(): s["title"] for s in project._solved(scenario_id)}
        used = set()
        for number, step in enumerate(steps, 1):
            mark, by = step.get("mark", ""), str(step.get("by") or "").strip().lower()
            if mark in MARKED:
                if by not in chosen:
                    out.append(f"шаг {number} (mark={mark}): в поле by нужно точное название выбранного решения: " + "; ".join(chosen.values()))
                else:
                    used.add(by)
            elif by:
                out.append(f"шаг {number}: у шага без изменений (mark пустой) не должно быть поля by")
        for key, title in chosen.items():
            if key not in used:
                out.append(f"выбранное решение «{title}» не отражено ни в одном шаге: добавь или измени шаг с by=«{title}» и подходящим mark")
    return out
