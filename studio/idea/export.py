"""Выгрузка проекта в .md: документация по сценариям будущей системы.

Только то, что уже есть в графе и банке знаний. Принятое — основным текстом, предложенное — отдельным разделом в конце.
Диаграммы — таблицами шагов. Кодов у пунктов нет: связи по названиям.
"""

from __future__ import annotations

from studio.idea.graph import ACCEPTED

from . import flow

FILE_NAME = "Документация.md"
MARKS = {"new": "новый шаг", "changed": "изменён", "auto": "автоматически", "removed": "убран"}


def _cell(text) -> str:
    return " ".join(str(text or "").replace("|", "/").split()) or "—"


def _table(head: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return []
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines + [""]


def _steps(diagram: dict, solution: bool) -> list[str]:
    rows = []
    for number, step in enumerate((diagram or {}).get("steps") or [], 1):
        extra = ""
        if solution:
            extra = MARKS.get(str(step.get("mark") or ""), "")
        elif isinstance(step.get("pain"), dict):
            extra = "Проблема: " + str(step["pain"].get("what") or "")
        rows.append([number, step.get("from"), step.get("to"), step.get("text"), extra])
    head = ["№", "От кого", "Кому", "Что", "Отметка" if solution else "Проблема"]
    if solution:                                                              # какое выбранное решение вводит изменённый шаг
        head.append("Решение")
        for row, step in zip(rows, (diagram or {}).get("steps") or []):
            row.append(step.get("by"))
    if not rows:
        return ["_Шаги не описаны._", ""]
    return _table(head, rows)


def build(project) -> str:
    """Текст документа по текущему состоянию проекта."""
    sphere = project.sphere()
    out = [f"# {project.store.title or (sphere or {}).get('title', 'Проект')}: документация", ""]
    if sphere is None:
        return "\n".join(out + ["_Сфера бизнеса ещё не задана._", ""])

    out += ["## 1. Сфера и границы системы", "", f"**Сфера:** {sphere['title']}"]
    if sphere.get("body"):
        out += ["", sphere["body"]]
    texts = [str((r["data"] or {}).get("text", "")) for r in project.records("requirement")]
    bounds = [t[len(flow.BOUNDARY):] for t in texts if t.startswith(flow.BOUNDARY)]
    wishes = [t for t in texts if t and not t.startswith(flow.BOUNDARY)]
    out += ["", "**Границы системы** (вопрос → ответ):", ""] + ([f"- {b}" for b in bounds] or ["- не уточнялись"])
    out += ["", "**Требования пользователя:**", ""] + ([f"- {w}" for w in wishes] or ["- не заданы"]) + [""]

    out += ["## 2. Конкуренты", ""]
    competitors = project.accepted_competitors()
    if competitors:
        out += _table(["Конкурент", "Сайт", "Чем занимается"],
                      [[r["data"].get("name"), r["data"].get("url"), r["data"].get("why")] for r in competitors])
    else:
        out += ["_Принятых конкурентов нет._", ""]

    roles = project._accepted_all(flow.ROLE)
    out += ["## 3. Роли и компоненты системы", "", "Единое пространство имён: участники всех диаграмм ниже — только эти имена.", ""]
    out += _table(["Имя", "Тип", "Что делает"], [[r["title"], project._role_type(r), r.get("body")] for r in roles]) \
        or ["_Принятых ролей нет._", ""]

    out += ["## 4. Сценарии", ""]
    scenarios = project._accepted_all(flow.SCENARIO)
    if not scenarios:
        out += ["_Принятых сценариев нет._", ""]
    for scenario in scenarios:
        sid = scenario["id"]
        out += [f"### {scenario['title']}", ""]
        if scenario.get("body"):
            out += [scenario["body"], ""]
        participants = [r["title"] for r in project.roles_of(sid)]
        out += [f"**Роли:** {', '.join(participants) if participants else 'не указаны'}", ""]
        usecase = (scenario.get("attrs") or {}).get("usecase") or {}
        if usecase.get("cases"):
            out += ["**Варианты использования:**", ""]
            out += _table(["Вариант использования", "Роли"], [[c.get("name"), ", ".join(c.get("actors") or [])] for c in usecase["cases"]])
        problems = project._accepted(flow.PROBLEM, sid)
        out += ["**Проблемы и решения:**", ""]
        rows = []
        for problem in problems:
            chosen = project._accepted(flow.VARIANT, problem["id"])
            solution = "; ".join(f"{v['title']}: {v['body']}" if v.get("body") else v["title"] for v in chosen) or "решение не выбрано"
            rows.append([problem["title"], (problem.get("attrs") or {}).get("importance"), problem.get("body"), solution])
        out += _table(["Проблема", "Важность", "Описание", "Решение"], rows) or ["_Принятых проблем нет._", ""]
        for kind, label, is_solution in ((flow.ASIS, "Как есть", False), (flow.SOLUTION, "Как будет (решение)", True)):
            for card in project.cards(kind, sid)[:1]:
                note = "" if card["status"] == ACCEPTED else " (не утверждено)"
                out += [f"**{label}{note}:**", ""]
                if card.get("body"):
                    out += [card["body"], ""]
                out += _steps((card.get("attrs") or {}).get("diagram"), is_solution)

    out += _pending(project, scenarios)
    return "\n".join(out).rstrip() + "\n"


def _pending(project, scenarios: list[dict]) -> list[str]:
    """Предложено агентами, но человек ещё не принял."""
    rows = []
    for competitor in project.records("competitor"):
        if (competitor["data"] or {}).get("state") == "proposed":
            rows.append(["конкурент", competitor["data"].get("name"), "", competitor["data"].get("why")])
    for kind in (flow.ROLE, flow.SCENARIO):
        for card in project.cards(kind):
            if card["status"] != ACCEPTED:
                rows.append([kind, card["title"], "", card.get("body")])
    for scenario in scenarios:
        for problem in project.cards(flow.PROBLEM, scenario["id"]):
            if problem["status"] != ACCEPTED:
                rows.append(["проблема", problem["title"], scenario["title"], problem.get("body")])
        for problem in project._accepted(flow.PROBLEM, scenario["id"]):
            for variant in project.cards(flow.VARIANT, problem["id"]):
                if variant["status"] != ACCEPTED:
                    rows.append(["вариант решения", variant["title"], f"{scenario['title']} / {problem['title']}", variant.get("body")])
    if not rows:
        return []
    return ["## 5. Предложено, но не утверждено", ""] + _table(["Что", "Название", "Где", "Описание"], rows)
