"""Проверка согласованности графа перед выгрузкой.

Код сам находит точные разногласия (``issues``): пустое, без решения, несвязанное, устаревшие диаграммы.
Независимая модель-проверяющий один раз читает граф (``graph_text``), находит смысловые разногласия и отвечает по пунктам кода.
Варианты правок и формулировки вопросов строит код по фиксированным шаблонам (``build_items``): модель только указывает, какие карточки
и что рекомендует. Документ строится по графу, поэтому правится граф, а не документ.
"""

from __future__ import annotations

import hashlib
import json

from studio.idea.graph import ACCEPTED

from . import diagrams, flow

FIXES = ("remove", "choose_variant", "suggest_variants", "edit", "rebuild")
IMPORTANCE = ("низкая", "средняя", "высокая", "критическая")
SEMANTIC = ("boundary", "duplicate", "contradiction")           # что находит модель; остальное находит код
NEEDS_ANSWER = ("empty", "no_decision")                          # по этим пунктам модель говорит, как исправить


def ref(card: dict) -> str:
    return f"«{card['title']}» ({card['id']})"


def short(text: str, n: int = 60) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def basis(project, scenario_id: str, kind: str) -> str:
    """Отпечаток того, по чему построена диаграмма: принятые проблемы и (для решения) выбранные варианты."""
    problems = [[p["title"], p["body"]] for p in project._accepted(flow.PROBLEM, scenario_id)]
    solved = [[s["problem"], s["title"], s["body"]] for s in project._solved(scenario_id)] if kind == flow.SOLUTION else []
    return hashlib.sha1(json.dumps([problems, solved], ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def _pains(card: dict) -> set[str]:
    steps = ((card.get("attrs") or {}).get("diagram") or {}).get("steps") or []
    return {str(s["pain"].get("what", "")).strip().lower() for s in steps if isinstance(s.get("pain"), dict) and s["pain"].get("what")}


def issues(project) -> list[dict]:
    """Разногласия, видные без чтения смысла. Каждое: id (i1…), code, text, card (главная карточка), scenario.

    Коды: empty, no_decision, no_roles, unused_role (правит человек или автомат по пункту) и diagram (чинится перестроением в конце).
    Весь расчёт идёт по одному снимку графа.
    """
    with project.frozen():
        return _issues(project)


def _issues(project) -> list[dict]:
    out: list[dict] = []

    def add(code: str, text: str, card: str = "", scenario: str = "") -> None:
        out.append({"id": f"i{len(out) + 1}", "code": code, "text": text, "card": card, "scenario": scenario})

    used: set[str] = set()
    for scenario in project._accepted_all(flow.SCENARIO):
        sid = scenario["id"]
        roles = project.roles_of(sid)
        used |= {r["id"] for r in roles}
        if not roles:
            add("no_roles", f"Сценарий {ref(scenario)}: не указано ни одной роли", sid, sid)
        if not scenario.get("body"):
            add("empty", f"Сценарий {ref(scenario)}: нет описания", sid, sid)
        problems = project._accepted(flow.PROBLEM, sid)
        for problem in problems:
            if not problem.get("body"):
                add("empty", f"Проблема {ref(problem)} (сценарий «{scenario['title']}»): нет описания", problem["id"], sid)
            elif (problem.get("attrs") or {}).get("importance") not in IMPORTANCE:
                add("empty", f"Проблема {ref(problem)} (сценарий «{scenario['title']}»): не указана важность", problem["id"], sid)
            chosen = project._accepted(flow.VARIANT, problem["id"])
            if not chosen:
                proposed = project.cards(flow.VARIANT, problem["id"])
                note = ("; есть предложенные варианты: " + ", ".join(ref(c) for c in proposed)) if proposed else "; вариантов решения нет"
                add("no_decision", f"Проблема {ref(problem)} (сценарий «{scenario['title']}»): принята, а решение не выбрано{note}",
                    problem["id"], sid)
            for variant in chosen:
                if not variant.get("body"):
                    add("empty", f"Решение {ref(variant)} проблемы «{problem['title']}»: нет описания", variant["id"], sid)
        asis = project.cards(flow.ASIS, sid)
        names = project._role_names()
        for label, cards_ in (("как есть", asis), ("как будет", project.cards(flow.SOLUTION, sid))):
            for card in cards_[:1]:
                parts = ((card.get("attrs") or {}).get("diagram") or {}).get("participants") or []
                unknown = [x for x in parts if x not in names]
                if unknown:
                    add("diagram", f"Сценарий {ref(scenario)}: в диаграмме «{label}» участники вне единого списка имён "
                                   f"({', '.join(unknown)}) — нужно перестроить", sid, sid)
        if problems and not asis:
            add("diagram", f"Сценарий {ref(scenario)}: есть принятые проблемы, но нет диаграммы «как есть»", sid, sid)
        if asis and _pains(asis[0]) != {p["title"].strip().lower() for p in problems}:
            add("diagram", f"Сценарий {ref(scenario)}: диаграмма «как есть» не соответствует принятым проблемам — нужно перестроить", sid, sid)
        solution = project.cards(flow.SOLUTION, sid)
        if project._solved(sid) and not solution:
            add("diagram", f"Сценарий {ref(scenario)}: есть выбранные решения, но нет диаграммы «как будет»", sid, sid)
        if not project._solved(sid) and solution:
            add("diagram", f"Сценарий {ref(scenario)}: диаграмма «как будет» есть, а выбранных решений нет — она устарела", sid, sid)
        for kind, cards_ in ((flow.ASIS, asis), (flow.SOLUTION, solution)):
            for card in cards_[:1]:
                saved = (card.get("attrs") or {}).get("basis")
                if saved and saved != basis(project, sid, kind):
                    add("diagram", f"Сценарий {ref(scenario)}: диаграмма «{'как есть' if kind == flow.ASIS else 'как будет'}» построена "
                                   "по старым проблемам или решениям — нужно перестроить", sid, sid)
    in_diagrams: set[str] = set()
    for scenario in project._accepted_all(flow.SCENARIO):
        for kind in (flow.ASIS, flow.SOLUTION):
            for card in project.cards(kind, scenario["id"])[:1]:
                in_diagrams |= set(((card.get("attrs") or {}).get("diagram") or {}).get("participants") or [])
    for role in [] if project.roles_confirmed else project._accepted_all(flow.ROLE):       # подтверждённый список ролей не оспаривается
        if not role.get("body"):
            add("empty", f"Роль {ref(role)}: нет описания", role["id"])
        if project._role_type(role) == flow.COMPONENT_TYPE:
            if in_diagrams and role["title"] not in in_diagrams:
                add("unused_role", f"Компонент {ref(role)}: не участвует ни в одной диаграмме", role["id"])
        elif role["id"] not in used:
            add("unused_role", f"Роль {ref(role)}: не участвует ни в одном принятом сценарии", role["id"])
    return out


def findings(project) -> list[str]:
    return [i["text"] for i in issues(project)]


def graph_text(project) -> str:
    """Граф проекта для чтения агентом: что принято, с id карточек и порядковыми номерами создания. Один снимок графа."""
    with project.frozen():
        return _graph_text(project)


def _graph_text(project) -> str:
    out = ["Роли и компоненты (единое пространство имён участников диаграмм"
           + ("; список подтверждён человеком — его не оценивай и в отчёт не включай):" if project.roles_confirmed else "):")]
    roles = project._accepted_all(flow.ROLE)
    out += [f"- {ref(r)} [{project._role_type(r)}]: {r.get('body') or 'нет описания'}" for r in roles] or ["- нет"]
    for scenario in project._accepted_all(flow.SCENARIO):
        sid = scenario["id"]
        out += ["", f"Сценарий {ref(scenario)}: {scenario.get('body') or 'нет описания'}",
                "  роли: " + (", ".join(r["title"] for r in project.roles_of(sid)) or "нет")]
        cases = ((scenario.get("attrs") or {}).get("usecase") or {}).get("cases") or []
        out += [f"  вариант использования: {c.get('name')} ({', '.join(c.get('actors') or [])})" for c in cases]
        for problem in project._accepted(flow.PROBLEM, sid):
            chosen = project._accepted(flow.VARIANT, problem["id"])
            out.append(f"  проблема {ref(problem)}, важность {(problem.get('attrs') or {}).get('importance') or 'не указана'}: "
                       f"{problem.get('body') or 'нет описания'}")
            out += [f"    РЕШЕНИЕ {ref(v)}: {v.get('body') or 'нет описания'}" for v in chosen] or ["    решение не выбрано"]
            out += [f"    предложенный вариант {ref(v)}: {v.get('body')}" for v in project.cards(flow.VARIANT, problem["id"])
                    if v["status"] != ACCEPTED]
        for kind, label in ((flow.ASIS, "КАК ЕСТЬ"), (flow.SOLUTION, "КАК БУДЕТ")):
            for card in project.cards(kind, sid)[:1]:
                diagram = (card.get("attrs") or {}).get("diagram")
                out.append(f"  {label}: {card.get('body') or ''}")
                out += ["    " + line for line in diagrams.to_text(diagram).splitlines()] if diagram else ["    шагов нет"]
    return "\n".join(out)


def validate_fix(project, fix) -> dict:
    """Правка: известный вид, существующая карточка подходящего вида. Иначе ValueError."""
    if not isinstance(fix, dict) or fix.get("do") not in FIXES:
        raise ValueError("правка fix.do: " + ", ".join(FIXES))
    try:
        card = project.store.card(str(fix.get("id") or ""))
    except Exception:  # noqa: BLE001 — id придумал агент
        raise ValueError(f"нет карточки с id «{fix.get('id')}»: бери id только из графа") from None
    do, kind = fix["do"], card["kind"]
    if kind == flow.ROLE and do in ("remove", "edit") and project.roles_confirmed:
        raise ValueError(f"список ролей подтверждён человеком: {ref(card)} не меняется")
    allowed = {"remove": (flow.ROLE, flow.SCENARIO, flow.PROBLEM, flow.VARIANT),
               "choose_variant": (flow.VARIANT,), "suggest_variants": (flow.PROBLEM,),
               "edit": (flow.ROLE, flow.SCENARIO, flow.PROBLEM, flow.VARIANT), "rebuild": (flow.SCENARIO,)}[do]
    if kind not in allowed:
        raise ValueError(f"правка {do} не подходит к карточке {ref(card)} (вид «{kind}»)")
    clean = {"do": do, "id": card["id"]}
    if do == "edit":
        for field in ("title", "body"):
            if str(fix.get(field) or "").strip():
                clean[field] = " ".join(str(fix[field]).split()) if field == "title" else str(fix[field]).strip()
        if str(fix.get("importance") or "").strip().lower() in IMPORTANCE and kind == flow.PROBLEM:
            clean["importance"] = str(fix["importance"]).strip().lower()
        if len(clean) == 2:
            raise ValueError("в правке edit нужен хотя бы один из title, body, importance")
        if len(clean.get("body", "")) > 250:
            raise ValueError("описание в edit — до 250 символов, 1-2 предложения")
    return clean


# ---------------------------------------------------------------------- отчёт проверяющего → пункты с вариантами

def _num(card_id: str) -> int:
    return int(card_id[1:]) if card_id[1:].isdigit() else 0


def parse_report(project, raw: dict, bounds: list[str], found: list[dict]) -> tuple[dict, list[str]]:
    """Разобрать отчёт проверяющего: (чистый отчёт, список ошибок). Ошибки возвращаются модели, и она исправляет отчёт в том же ходе."""
    errors: list[str] = []
    items, answers = [], {}
    for number, item in enumerate((raw.get("items") or [])[:40], 1):
        if not isinstance(item, dict) or item.get("kind") not in SEMANTIC:
            errors.append(f"items[{number}]: kind — одно из {', '.join(SEMANTIC)}")
            continue
        ids = [str(i) for i in (item.get("cards") or [])]
        want = 2 if item["kind"] != "boundary" else 1
        cards = []
        for i in ids:
            try:
                cards.append(project.store.card(i))
            except Exception:  # noqa: BLE001
                errors.append(f"items[{number}]: нет карточки «{i}», бери id только из графа")
        if len(cards) != want or len(set(ids)) != want:
            errors.append(f"items[{number}] ({item['kind']}): нужно ровно {want} разных карточки в cards")
            continue
        if project.roles_confirmed and any(c["kind"] == flow.ROLE for c in cards):
            errors.append(f"items[{number}]: список ролей подтверждён человеком, роли не оцениваются и в отчёт не входят: убери роль из пункта")
            continue
        if any(c["kind"] not in (flow.SCENARIO, flow.PROBLEM, flow.VARIANT, flow.ROLE) for c in cards):
            errors.append(f"items[{number}]: можно указывать только роли, сценарии, проблемы и варианты")
            continue
        clean = {"kind": item["kind"], "cards": ids}
        if item["kind"] == "boundary":
            index = item.get("boundary")
            if not isinstance(index, int) or not 1 <= index <= len(bounds):
                errors.append(f"items[{number}]: boundary — номер границы из списка (1…{len(bounds)}), которую нарушает карточка")
                continue
            clean["boundary"] = index
            rewrite = item.get("rewrite") if isinstance(item.get("rewrite"), dict) else None
            if rewrite and str(rewrite.get("body") or "").strip():
                clean["rewrite"] = {"title": " ".join(str(rewrite.get("title") or "").split()),
                                    "body": str(rewrite["body"]).strip()}
                if len(clean["rewrite"]["body"]) > 250:
                    errors.append(f"items[{number}]: rewrite.body — до 250 символов")
                    continue
        else:
            drop = str(item.get("remove") or "")
            if drop not in ids:
                errors.append(f"items[{number}] ({item['kind']}): remove — id той из двух карточек, которую убрать")
                continue
            clean["remove"] = drop
        items.append(clean)
    for number, answer in enumerate(raw.get("answers") or [], 1):
        if isinstance(answer, dict) and str(answer.get("issue") or ""):
            answers[str(answer["issue"])] = answer
    for issue in found:
        if issue["code"] not in NEEDS_ANSWER:
            continue
        answer = answers.get(issue["id"])
        if answer is None:
            errors.append(f"нет ответа по пункту {issue['id']} ({issue['text'][:80]}): добавь его в answers")
            continue
        pick = str(answer.get("pick") or "")
        if issue["code"] == "no_decision":
            valid = {c["id"] for c in project.cards(flow.VARIANT, issue["card"])} | {"suggest", "remove"}
            if pick not in valid:
                errors.append(f"пункт {issue['id']}: pick — id одного из предложенных вариантов, либо suggest, либо remove")
        else:
            body = str(answer.get("body") or "").strip()
            if pick not in ("edit", "remove") or (pick == "edit" and not body):
                errors.append(f"пункт {issue['id']}: pick — edit (с body по смыслу названия) или remove")
            elif len(body) > 250:
                errors.append(f"пункт {issue['id']}: body — до 250 символов")
    return {"items": items, "answers": answers}, errors


def _option(project, title: str, *fixes: dict) -> dict:
    return {"title": short(title), "fixes": [validate_fix(project, f) for f in fixes]}


def build_items(project, report: dict, bounds: list[str], found: list[dict]) -> list[dict]:
    """Пункты для человека или автомата: вопрос и варианты строит код. recommended — номер варианта (с 1), его применяет автомат."""
    out = []
    for item in report["items"]:
        cards = [project.store.card(i) for i in item["cards"]]
        if item["kind"] == "boundary":
            card = cards[0]
            options = [_option(project, f"Убрать «{card['title']}»", {"do": "remove", "id": card["id"]})]
            if item.get("rewrite"):
                options.append(_option(project, f"Переписать: {item['rewrite']['body']}",
                                       {"do": "edit", "id": card["id"], "title": item["rewrite"]["title"] or card["title"],
                                        "body": item["rewrite"]["body"]}))
            out.append({"question": short(f"Вне границы {item['boundary']}: «{card['title']}»", 120), "options": options,
                        "recommended": 1, "cards": [card["id"]]})
        else:
            a, b = cards
            options = [_option(project, f"Убрать «{c['title']}»", {"do": "remove", "id": c["id"]}) for c in (a, b)]
            label = "Дубль" if item["kind"] == "duplicate" else "Противоречие"
            out.append({"question": short(f"{label}: «{a['title']}» и «{b['title']}»", 120), "options": options,
                        "recommended": 1 + [a["id"], b["id"]].index(item["remove"]), "cards": [a["id"], b["id"]]})
    for issue in found:
        card = project.store.card(issue["card"]) if issue["card"] else None
        if issue["code"] == "empty":
            answer = report["answers"][issue["id"]]
            options = []
            if answer.get("pick") == "edit":
                fix = {"do": "edit", "id": card["id"], "body": str(answer["body"]).strip()}
                if card["kind"] == flow.PROBLEM and str(answer.get("importance") or "").lower() in IMPORTANCE:
                    fix["importance"] = str(answer["importance"]).lower()
                options.append(_option(project, f"Дописать: {fix['body']}", fix))
            options.append(_option(project, f"Убрать «{card['title']}»", {"do": "remove", "id": card["id"]}))
            out.append({"question": short(f"Пусто: «{card['title']}»", 120), "options": options, "recommended": 1, "cards": [card["id"]]})
        elif issue["code"] == "no_decision":
            answer = report["answers"][issue["id"]]
            options, rec = [], 0
            for variant in project.cards(flow.VARIANT, card["id"]):
                options.append(_option(project, f"Выбрать «{variant['title']}»", {"do": "choose_variant", "id": variant["id"]}))
                if answer["pick"] == variant["id"]:
                    rec = len(options)
            if not options:
                options.append(_option(project, "Предложить варианты", {"do": "suggest_variants", "id": card["id"]}))
                rec = 1 if answer["pick"] != "remove" else 0
            options.append(_option(project, "Убрать проблему", {"do": "remove", "id": card["id"]}))
            out.append({"question": short(f"Нет решения: «{card['title']}»", 120), "options": options,
                        "recommended": rec or len(options), "cards": [card["id"]]})
        elif issue["code"] in ("no_roles", "unused_role"):
            what = "Сценарий без ролей" if issue["code"] == "no_roles" else "Роль без сценариев"
            out.append({"question": short(f"{what}: «{card['title']}»", 120),
                        "options": [_option(project, f"Убрать «{card['title']}»", {"do": "remove", "id": card["id"]})],
                        "recommended": 1, "cards": [card["id"]]})
    return out
