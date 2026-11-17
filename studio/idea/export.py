"""Выгрузка проекта в .md: документация по сценариям будущей системы.

Только то, что уже есть в графе и банке знаний. Принятое — основным текстом,
предложенное — отдельным разделом в конце.
Диаграммы — таблицами шагов. Кодов у пунктов нет: связи по названиям.
"""

from __future__ import annotations

from studio.pipeline.graph import ACCEPTED

from . import flow

FILE_NAME = "Документация.md"


def _cell(text) -> str:
    return " ".join(str(text or "").replace("|", "/").split()) or "—"


def _table(head: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return []
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines + [""]


def _steps(diagram: dict) -> list[str]:
    steps = (diagram or {}).get("steps") or []
    if not steps:
        return ["_Шаги не описаны._", ""]
    rows = [
        [number, step.get("from"), step.get("to"), step.get("text"), step.get("by")]
        for number, step in enumerate(steps, 1)
    ]
    return _table(["№", "От кого", "Кому", "Что", "Решение"], rows)


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
    bounds = [t[len(flow.BOUNDARY) :] for t in texts if t.startswith(flow.BOUNDARY)]
    wishes = [t for t in texts if t and not t.startswith(flow.BOUNDARY)]
    out += ["", "**Границы системы** (вопрос → ответ):", ""] + (
        [f"- {b}" for b in bounds] or ["- не уточнялись"]
    )
    out += (
        ["", "**Пожелания пользователя:**", ""]
        + ([f"- {w}" for w in wishes] or ["- не заданы"])
        + [""]
    )

    out += ["## 2. Конкуренты", ""]
    competitors = project.accepted_competitors()
    if competitors:
        out += _table(
            ["Конкурент", "Сайт", "Чем занимается"],
            [
                [r["data"].get("name"), r["data"].get("url"), r["data"].get("why")]
                for r in competitors
            ],
        )
    else:
        out += ["_Принятых конкурентов нет._", ""]

    roles = project._accepted_all(flow.ROLE)
    out += [
        "## 3. Роли и компоненты системы",
        "",
        "Единое пространство имён: участники всех диаграмм ниже — только эти имена.",
        "",
    ]
    out += _table(
        ["Имя", "Тип", "Что делает"],
        [[r["title"], project._role_type(r), r.get("body")] for r in roles],
    ) or ["_Принятых ролей нет._", ""]

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
            out += _table(
                ["Вариант использования", "Роли"],
                [[c.get("name"), ", ".join(c.get("actors") or [])] for c in usecase["cases"]],
            )
        problems = project._accepted(flow.PROBLEM, sid)
        out += ["**Проблемы:**", ""]
        out += _table(
            ["Проблема", "Важность", "Описание"],
            [
                [p["title"], (p.get("attrs") or {}).get("importance"), p.get("body")]
                for p in problems
            ],
        ) or ["_Принятых проблем нет._", ""]
        rows = []
        for problem in problems:
            requirements = project._accepted(flow.REQUIREMENT, problem["id"])
            for requirement in requirements:
                chosen = project._accepted(flow.VARIANT, requirement["id"])
                solution = (
                    "; ".join(
                        f"{v['title']}: {v['body']}" if v.get("body") else v["title"]
                        for v in chosen
                    )
                    or "решение не выбрано"
                )
                rows.append(
                    [requirement["title"], problem["title"], requirement.get("body"), solution]
                )
        if rows:
            out += ["**Требования к системе и решения:**", ""]
            out += _table(["Требование", "Из проблемы", "Описание", "Решение"], rows)
        for card in project.cards(flow.SOLUTION, sid)[:1]:
            note = "" if card["status"] == ACCEPTED else " (не утверждено)"
            out += [f"**Как будет (решение){note}:**", ""]
            if card.get("body"):
                out += [card["body"], ""]
            out += _steps((card.get("attrs") or {}).get("diagram"))

    out += _pending(project, scenarios)
    return "\n".join(out).rstrip() + "\n"


def _pending(project, scenarios: list[dict]) -> list[str]:
    """Предложено агентами, но человек ещё не принял."""
    rows = []
    for competitor in project.records("competitor"):
        if (competitor["data"] or {}).get("state") == "proposed":
            rows.append(
                ["конкурент", competitor["data"].get("name"), "", competitor["data"].get("why")]
            )
    for kind in (flow.ROLE, flow.SCENARIO):
        for card in project.cards(kind):
            if card["status"] != ACCEPTED:
                rows.append([kind, card["title"], "", card.get("body")])
    for scenario in scenarios:
        for problem in project.cards(flow.PROBLEM, scenario["id"]):
            if problem["status"] != ACCEPTED:
                rows.append(["проблема", problem["title"], scenario["title"], problem.get("body")])
        for problem in project._accepted(flow.PROBLEM, scenario["id"]):
            for requirement in project.cards(flow.REQUIREMENT, problem["id"]):
                if requirement["status"] != ACCEPTED:
                    rows.append(
                        [
                            "требование",
                            requirement["title"],
                            f"{scenario['title']} / {problem['title']}",
                            requirement.get("body"),
                        ]
                    )
        for requirement in project._requirements_of(scenario["id"]):
            for variant in project.cards(flow.VARIANT, requirement["id"]):
                if variant["status"] != ACCEPTED:
                    rows.append(
                        [
                            "вариант решения",
                            variant["title"],
                            f"{scenario['title']} / {requirement['title']}",
                            variant.get("body"),
                        ]
                    )
    if not rows:
        return []
    return ["## 5. Предложено, но не утверждено", ""] + _table(
        ["Что", "Название", "Где", "Описание"], rows
    )
