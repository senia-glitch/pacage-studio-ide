"""Автоматический ход цикла: итерации идей, автоприём карточек, реализация по сценариям.

Настройки приходят с этапа «Цель» (``workflow.data["options"]``), ход хранится в
``workflow.data["pipeline"]``. Модуль не запускает агента сам: он отвечает на два вопроса
исполнителю (``server.runner``):

- ``job_for`` — что запустить на этапе сейчас (планировщик);
- ``after_job`` — что делать, когда запуск закончился (следующий запуск или переход этапа).

Без настроек (по умолчанию) оба возвращают ``None`` и цепочка идёт как раньше: один запуск
агента на этап. Контролёр (роль «Контроль») работает без прежней сессии: независимый взгляд
на чужую работу, а не продолжение мыслей автора.
"""

from __future__ import annotations

from studio.agent import cards
from studio.metadata import MetadataError
from studio.server import runner as runner_mod
from studio.server.workflow import DEFAULT_OPTIONS

MAX_FIXES = 2  # сколько раз реализующий агент исправляет замечания одной проверки
MAX_LISTED = 120  # карточек в списке «уже есть» в запросе итерации
MAX_DECIDE = 40  # карточек с подробностями в запросе контролёра
MAX_REMARKS = 20
STEP_TITLES = {
    "implement": "реализация", "verify": "проверка", "tests": "тесты",
    "fix-verify": "исправление замечаний", "fix-tests": "исправление после тестов",
}
KIND_TITLES = {"plan": "сценарий бэкенда", "node": "узел бэкенда", "page": "страница фронтенда"}
NO_REPORT = (
    "Агент завершился, но итоговый отчёт (studio_report) не отправил: нажмите «Повторить» "
    "или загляните в .studio/runner.log"
)


def _short(value, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ----------------------------------------------------------------------- настройки


def options(state) -> dict:
    """Настройки цикла в силе: без готового автозапуска агента они не действуют."""
    if not state.runner.config()["enabled"]:
        return dict(DEFAULT_OPTIONS)
    return dict(state.workflow.data["options"])


def ideas_steps(state) -> bool:
    """Идеи идут в несколько запросов (итерации и/или автоприём карточек)."""
    opts = options(state)
    return state.workflow.data["mode"] == "plan" and (
        opts["iterations"] > 1 or opts["auto_decide"]
    )


def unit_mode(state) -> bool:
    """Реализация идёт по сценариям: после каждого проверка и/или усиленные тесты."""
    opts = options(state)
    return bool(opts["verify"] or opts["tests"])


# ------------------------------------------------------------------------- запросы


def _job(stage: str, kind: str, role: str, prompt: str, label: str, sig: list, *,
         fresh: bool = False, begin: bool = False) -> dict:
    return {"stage": stage, "kind": kind, "role": role, "prompt": prompt, "label": label,
            "sig": sig, "fresh": fresh, "begin": begin}


def _cards_listing(board: dict) -> tuple[list[str], int]:
    lines = []
    total = 0
    for _idea, kind, item in cards.iter_cards(board):
        total += 1
        if len(lines) >= MAX_LISTED:
            continue
        source = item.get("source") or {}
        if kind == "scenario":
            route = " ".join(
                part for part in (str(source.get("http_method") or "").upper(),
                                  str(source.get("path") or "")) if part
            )
            head = str(source.get("slug") or item.get("id"))
        else:
            route = str(item.get("route") or "")
            head = str(item.get("name") or item.get("id"))
        lines.append(
            f"- [{item['decision']}] {head} — {_short(item.get('title'), 90)}"
            + (f" ({route})" if route else "")
        )
    return lines, total


def _ideas_prompt(state, iteration: int, total: int) -> str:
    base = runner_mod.PROMPTS["ideas"]
    if total <= 1:
        return base
    head = f"Итерация идей {iteration} из {total}. "
    if iteration == 1:
        extra = (
            "Это первая итерация: дай укрупнённую декомпозицию (основные идеи и ключевые "
            "сценарии). Следующие итерации студия запустит сама и углубит её."
        )
        return head + extra + "\n\n" + base
    lines, count = _cards_listing(state.load_ideas())
    listing = "\n".join(lines) or "(карточек пока нет)"
    rest = f"\n…и ещё {count - len(lines)}" if count > len(lines) else ""
    extra = (
        "Основа — декомпозиция прошлых итераций, это карточки ниже. Углуби её: разбей "
        "крупные сценарии на более мелкие и добавь недостающее (ошибки, права, граничные "
        "случаи, страницы), НЕ повторяя уже существующие карточки (отклонённые тоже не "
        "предлагай заново). Если добавлять нечего, просто вызови studio_ideas_ready.\n\n"
        f"Уже есть ({count}):\n{listing}{rest}"
    )
    return head + extra + "\n\n" + base


def _decide_prompt(state) -> str:
    board = state.load_ideas()
    chosen = []
    for _idea, kind, item in cards.iter_cards(board):
        if item["decision"] != cards.PENDING:
            continue
        source = item.get("source") or {}
        route = " ".join(
            part for part in (str(source.get("http_method") or "").upper(),
                              str(source.get("path") or "")) if part
        ) or str(item.get("route") or "")
        chosen.append(
            f"id={item['id']} | {'сценарий' if kind == 'scenario' else 'страница'} | "
            f"{source.get('slug') or item.get('name') or ''} | {_short(item.get('title'), 100)}"
            + (f" | {route}" if route else "")
            + f"\n    описание: {_short(item.get('description'), 300)}"
            + f"\n    критерии: {_short('; '.join(item.get('acceptance') or []), 300)}"
        )
    shown = "\n".join(chosen[:MAX_DECIDE]) or "(нет карточек)"
    more = len(chosen) - MAX_DECIDE
    tail = f"\n…и ещё {more} карточек: смотри studio_ideas()." if more > 0 else ""
    task = state.workflow.data["task"] or "не указана"
    return (
        "Тебя запустила студия pacage-studio в роли контролёра («Контроль»). Вызови "
        "studio_connect(). Не вызывай studio_workflow и не пиши код. Твоя задача: по КАЖДОЙ "
        "карточке ниже решить, нужна ли она. Принимай (accepted), если сценарий нужен задаче "
        "человека, конкретен, проверяем критериями и не дублирует другую карточку. "
        "Отклоняй (rejected) дубли, лишнее, расплывчатое и то, что вне задачи. Передай решения "
        "вызовом studio_cards_decide(decisions=[{id, decision, reason}]) (можно частями, "
        "reason — одна короткая фраза). Затем заверши работу.\n\n"
        f"Задача человека: {task}\n\nКарточки к решению:\n{shown}{tail}" + runner_mod.ASK_HINT
    )


def _part(unit: dict) -> str:
    return "frontend" if unit["kind"] == "page" else "backend"


def _unit_head(units: list[dict], index: int) -> str:
    unit = units[index]
    return (
        f"Сценарий {index + 1} из {len(units)}: «{unit['title']}» "
        f"({KIND_TITLES.get(unit['kind'], unit['kind'])} {unit['id']}, часть ТЗ — {_part(unit)})"
    )


def _unit_prompt(units: list[dict], index: int) -> str:
    return (
        "Тебя запустила студия pacage-studio на этапе «Реализация». Реализация идёт по "
        f"сценариям, по одному за запрос. Сейчас — {_unit_head(units, index)}. Вызови "
        "studio_connect(), прочитай ТЗ по частям (studio_brief), найди в нём задачу этого "
        "сценария и реализуй ТОЛЬКО её: остальные сценарии не трогай, уже сделанные не ломай. "
        "Запусти тесты проекта и проверь studio_verify. Не вызывай studio_workflow и "
        "studio_report: отчёт студия запросит отдельно. Затем заверши работу."
        + runner_mod.ASK_HINT
    )


def _check_prompt(units: list[dict], index: int, step: str) -> str:
    head = (
        "Тебя запустила студия pacage-studio в роли контролёра («Контроль»). Только что "
        f"реализован: {_unit_head(units, index)}. Вызови studio_connect(). Не вызывай "
        "studio_workflow и studio_report. "
    )
    if step == "verify":
        body = (
            "Проверь реализацию независимо: прочитай задачу сценария и критерии приёмки в ТЗ "
            "(studio_brief), изучи код и тесты, запусти тесты проекта и studio_verify. Код "
            "реализации НЕ правь. Когда закончишь, вызови studio_check_result(ok, remarks): "
            "ok=true, если сценарий реализован по ТЗ и все критерии выполнены; иначе ok=false "
            "и remarks — конкретные замечания списком (что не так и где)."
        )
    else:
        body = (
            "Напиши качественные тесты для этого сценария: прочитай задачу и критерии приёмки "
            "в ТЗ (studio_brief), покрой основной путь, каждый критерий приёмки, ошибки и "
            "граничные случаи. Усиливай уже существующие тесты, а не дублируй их; клади новые "
            "туда, где проект хранит тесты. Запусти тесты проекта: новые должны проходить. "
            "Если тест падает из-за дефекта реализации, код реализации НЕ меняй, а опиши "
            "дефект. В конце вызови studio_check_result(ok, remarks): ok=true, если все тесты "
            "сценария проходят; иначе ok=false и remarks — какие тесты падают и почему."
        )
    return head + body + " Затем заверши работу." + runner_mod.ASK_HINT


def _fix_prompt(units: list[dict], index: int, step: str) -> str:
    unit = units[index]
    remarks = (unit.get("checks") or {}).get(step[4:], {}).get("remarks") or []
    listing = "\n".join(f"- {item}" for item in remarks) or "- (замечания не переданы)"
    source = "проверки" if step == "fix-verify" else "тестов"
    return (
        "Тебя запустила студия pacage-studio на этапе «Реализация». Контроль нашёл замечания "
        f"по итогам {source}: {_unit_head(units, index)}. Исправь их в коде (остальные "
        "сценарии не трогай) и запусти тесты проекта:\n"
        f"{listing}\nНе вызывай studio_workflow и studio_report. Затем заверши работу."
        + runner_mod.ASK_HINT
    )


def _final_prompt(units: list[dict]) -> str:
    lines = []
    for unit in units:
        checks = unit.get("checks") or {}
        marks = []
        for key, title in (("verify", "проверка"), ("tests", "тесты")):
            if key in checks:
                marks.append(f"{title} {'✓' if checks[key]['ok'] else '✗ (замечания остались)'}")
        left = [r for c in checks.values() if not c["ok"] for r in c.get("remarks") or []]
        line = f"- {unit['title']}: " + (", ".join(marks) if marks else "реализовано")
        if left:
            line += "\n    остались замечания: " + "; ".join(left[:5])
        lines.append(line)
    return (
        "Тебя запустила студия pacage-studio на этапе «Реализация». Все сценарии ТЗ "
        f"реализованы ({len(units)}). Вызови studio_connect(). Если ниже есть пожелание "
        "человека, сначала учти его и исправь. Затем отправь человеку итоговый отчёт: "
        "studio_report(summary, ...) — что сделано по каждому сценарию, что проверено и какие "
        "замечания остались (если были страницы фронтенда, отдельно отправь отчёт part="
        "frontend). Итоги контроля:\n" + "\n".join(lines)
        + "\nТолько отчёт: код больше не меняй, если нет пожелания человека. "
        "Затем заверши работу." + runner_mod.ASK_HINT
    )


# ---------------------------------------------------------------------- планировщик


def _signature(pipeline: dict) -> list:
    return [pipeline["phase"], pipeline["iteration"], pipeline["unit"], pipeline["step"],
            pipeline["fixes"], pipeline["final"]]


def job_for(state, stage: str) -> dict | None:
    """Что запускать на этапе ``stage``: ``None`` — обычный запуск без автоматического хода."""
    wf = state.workflow
    pipeline = wf.data["pipeline"]
    sig = _signature(pipeline)
    if stage == "ideas" and ideas_steps(state):
        opts = options(state)
        if pipeline["phase"] == "decide":
            return _job(stage, "decide", "check", _decide_prompt(state),
                        "Контроль: решение по карточкам", sig, fresh=True)
        k, n = pipeline["iteration"], opts["iterations"]
        label = f"Идеи: итерация {k} из {n}" if n > 1 else "Идеи агента"
        return _job(stage, "ideas", "plan", _ideas_prompt(state, k, n), label, sig)
    if stage == "implement" and pipeline["units"] and unit_mode(state):
        units, index, step = pipeline["units"], pipeline["unit"], pipeline["step"]
        if pipeline["final"]:
            return _job(stage, "final", "implement", _final_prompt(units), "Итоговый отчёт", sig)
        prefix = f"Сценарий {index + 1} из {len(units)}"
        first = index == 0 and step == "implement" and pipeline["fixes"] == 0
        if step == "implement":
            return _job(stage, "unit", "implement", _unit_prompt(units, index),
                        f"{prefix}: реализация", sig, begin=first)
        if step in ("verify", "tests"):
            return _job(stage, "check", "check", _check_prompt(units, index, step),
                        f"{prefix}: {STEP_TITLES[step]}", sig, fresh=True)
        if step in ("fix-verify", "fix-tests"):
            return _job(stage, "fix", "implement", _fix_prompt(units, index, step),
                        f"{prefix}: {STEP_TITLES[step]}", sig)
    return None


# ------------------------------------------------------------------------- идеи


def on_ideas_ready(state) -> dict:
    """Агент закончил итерацию идей: студия сама решает, что дальше (после его завершения)."""
    wf = state.workflow
    pipeline = wf.data["pipeline"]
    counts = cards.counts(state.load_ideas())
    if not counts["total"] and pipeline["iteration"] == 1:
        raise MetadataError(
            "invalid_input", "Карточек для валидации нет: сначала пришлите идеи (studio_idea_add)"
        )
    wf.set_pipeline(ready=True)
    return {
        "stage": "ideas", "changed": False, "iteration": pipeline["iteration"],
        "of": options(state)["iterations"], "pending": counts[cards.PENDING],
        "next": "Заверши работу: студия сама запустит следующий шаг.",
    }


def _after_ideas(state, job: dict) -> str:
    wf = state.workflow
    pipeline = wf.data["pipeline"]
    if not pipeline["ready"]:
        return (
            "Агент завершил итерацию, не вызвав studio_ideas_ready: нажмите «Повторить» "
            "или загляните в .studio/runner.log"
        )
    opts = options(state)
    counts = cards.counts(state.load_ideas())
    grew = counts["total"] > pipeline["cards_before"]
    if opts["auto_decide"]:
        if not counts[cards.PENDING]:
            if pipeline["iteration"] < opts["iterations"] and grew:
                return _next_iteration(state, counts)
            return _hand_over(state)
        wf.set_pipeline(ready=False, phase="decide")
        state.runner.request("ideas")
        return ""
    if pipeline["iteration"] < opts["iterations"] and grew:
        return _next_iteration(state, counts)
    wf.set_pipeline(ready=False, phase="")
    wf.advance("review")  # дальше карточки валидирует человек
    return ""


def _next_iteration(state, counts: dict) -> str:
    wf = state.workflow
    wf.set_pipeline(
        ready=False, phase="", iteration=wf.data["pipeline"]["iteration"] + 1,
        cards_before=counts["total"],
    )
    state.runner.request("ideas")
    return ""


def _after_decide(state, job: dict) -> str:
    wf = state.workflow
    pipeline = wf.data["pipeline"]
    counts = cards.counts(state.load_ideas())
    if counts[cards.PENDING]:
        return (
            f"Контроль не решил по {counts[cards.PENDING]} карточкам: нажмите «Повторить» "
            "или решите их сами на странице «Генератор идей»"
        )
    opts = options(state)
    if pipeline["iteration"] < opts["iterations"] and counts["total"] > pipeline["cards_before"]:
        return _next_iteration(state, counts)
    return _hand_over(state)


def _hand_over(state) -> str:
    """Идеи закончились, карточки решены: перенос на карту, выбор задач, старт реализации."""
    from studio.server import chain  # noqa: PLC0415 — chain сам вызывает этот модуль

    wf = state.workflow
    counts = cards.counts(state.load_ideas())
    wf.set_pipeline(ready=False, phase="")
    if not counts["to_transfer"]:
        return (
            "Контроль не принял ни одной новой карточки: переносить на карту нечего. "
            "Вернитесь на «Цель» и начните заново или решите карточки сами"
        )
    with state.lock:
        wf.advance("review")
        wf.advance("confirm")
        result = chain.transfer(state)
        errors = (result.get("transfer") or {}).get("errors") or []
        if wf.data["stage"] != "select":
            names = "; ".join(f"{e['title']}: {e['message']}" for e in errors[:3])
            return f"Перенос на карту с ошибками ({names}): разберите их на странице идей"
        plans, pages = accepted_units(state.load_ideas())
        if not (plans or pages):
            return (
                "Принятые карточки уже есть в коде: реализовывать нечего. "
                "Выберите задачи сами на шаге «Выбор задач»"
            )
        chain.select(state, {"plans": plans, "pages": pages}, state.check_front_links)
    return ""


def accepted_units(board: dict) -> tuple[list[str], list[str]]:
    """Планы и страницы, на которые перенесены принятые карточки (без уже готового в коде)."""
    plans: list[str] = []
    pages: list[str] = []
    for _idea, kind, item in cards.iter_cards(board):
        if item["decision"] != cards.ACCEPTED or item.get("inCode"):
            continue
        if kind == "scenario" and item.get("planSlug"):
            plans.append(str(item["planSlug"]))
        elif kind == "page" and item.get("mapPageId"):
            pages.append(str(item["mapPageId"]))
    return list(dict.fromkeys(plans)), list(dict.fromkeys(pages))


def decide_cards(state, data: dict) -> dict:
    """Решения контролёра по карточкам (studio_cards_decide): только во время его запуска."""
    if state.runner.running_kind() != "decide":
        raise MetadataError(
            "wrong_stage", "Решать карточки может только контролёр, запущенный студией для этого"
        )
    items = data.get("decisions")
    if not isinstance(items, list) or not items:
        raise MetadataError("invalid_input", "decisions: список {id, decision, reason}")
    pairs = []
    for item in items:
        if not isinstance(item, dict) or item.get("decision") not in (
            cards.ACCEPTED, cards.REJECTED
        ):
            raise MetadataError("invalid_input", "decision: accepted или rejected")
        pairs.append((str(item.get("id") or ""), item["decision"], _short(item.get("reason"), 300)))
    missing: list[str] = []

    def apply(board: dict):
        changed = False
        by_id = {item["id"]: item for _i, _k, item in cards.iter_cards(board)}
        for card_id, decision, reason in pairs:
            card = by_id.get(card_id)
            if card is None:
                missing.append(card_id)
                continue
            if card["decision"] != cards.PENDING:
                continue  # решение человека или прежнее не перезаписываем
            card["decision"] = decision
            card["decidedBy"] = "agent"
            if reason:
                card["decisionNote"] = reason
            changed = True
        return changed

    with state.lock:
        state.mutate_ideas(apply)
        counts = cards.counts(state.load_ideas())
        state.workflow.touch()
    return {"cards": counts, "missing": missing}


# -------------------------------------------------------------- реализация по сценариям


def make_units(state, plans: list[str], nodes: list[str], pages: list[str]) -> list[dict]:
    """Сценарии реализации из выбора человека: названия берутся с карты."""
    from studio.server import chain  # noqa: PLC0415

    found = chain.candidates(state)
    titles = {
        ("plan", item["slug"]): item["title"] for item in found["plans"]
    } | {
        ("node", item["id"]): item["label"] for item in found["nodes"]
    } | {
        ("page", item["id"]): item["name"] or item["id"] for item in found["pages"]
    }
    units = []
    for kind, ids in (("plan", plans), ("node", nodes), ("page", pages)):
        for ident in ids:
            units.append({
                "kind": kind, "id": ident, "title": str(titles.get((kind, ident)) or ident),
                "state": "todo", "checks": {}, "remarks": [],
            })
    return units


def start_units(state, units: list[dict]) -> None:
    """Перед входом на «Реализацию»: включить ход по сценариям, если он нужен."""
    if not (units and unit_mode(state)):
        return
    state.workflow.set_pipeline(
        units=units, unit=0, step="implement", fixes=0, final=False, phase="build"
    )


def record_check(state, data: dict) -> dict:
    """Итог проверки или тестов сценария от контролёра (studio_check_result)."""
    if state.runner.running_kind() != "check":
        raise MetadataError(
            "wrong_stage", "Итог проверки принимается только от контролёра, запущенного студией"
        )
    ok = data.get("ok")
    if not isinstance(ok, bool):
        raise MetadataError("invalid_input", "ok: true или false")
    raw = data.get("remarks") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise MetadataError("invalid_input", "remarks: список строк")
    remarks = [_short(item, 500) for item in raw if str(item).strip()][:MAX_REMARKS]
    if not ok and not remarks:
        raise MetadataError("invalid_input", "remarks: при ok=false опишите замечания")
    wf = state.workflow
    with state.lock:
        pipeline = wf.data["pipeline"]
        units, index, step = pipeline["units"], pipeline["unit"], pipeline["step"]
        if step not in ("verify", "tests") or index >= len(units):
            raise MetadataError("wrong_stage", "Сейчас никакая проверка сценария не идёт")
        units[index].setdefault("checks", {})[step] = {"ok": ok, "remarks": remarks}
        wf.set_pipeline(units=units)
    return {"recorded": True, "step": step, "ok": ok}


def _after_unit(state, job: dict) -> str:
    wf = state.workflow
    pipeline = wf.data["pipeline"]
    units, index, step = pipeline["units"], pipeline["unit"], pipeline["step"]
    unit = units[index]
    opts = options(state)
    checks = [name for name in ("verify", "tests") if opts[name]]
    fixes = pipeline["fixes"]
    following: str | None
    if step == "implement":
        following = checks[0] if checks else None
        fixes = 0
    elif step in ("verify", "tests"):
        result = (unit.get("checks") or {}).get(step)
        if result is None:
            return (
                "Контроль завершился, не вернув итог (studio_check_result): нажмите "
                "«Повторить» или загляните в .studio/runner.log"
            )
        if not result["ok"] and fixes < MAX_FIXES:
            wf.set_pipeline(step=f"fix-{step}", fixes=fixes + 1)
            state.runner.request("implement")
            return ""
        position = checks.index(step) if step in checks else len(checks)
        following = checks[position + 1] if position + 1 < len(checks) else None
        fixes = 0
    else:  # fix-verify / fix-tests: заново та же проверка
        following = step[4:]
        (unit.get("checks") or {}).pop(following, None)
    if following:
        wf.set_pipeline(units=units, step=following, fixes=fixes)
        state.runner.request("implement")
        return ""
    left = [r for c in (unit.get("checks") or {}).values() if not c["ok"] for r in c["remarks"]]
    unit["state"] = "issues" if left else "done"
    if index + 1 < len(units):
        wf.set_pipeline(units=units, unit=index + 1, step="implement", fixes=0)
    else:
        wf.set_pipeline(units=units, phase="final", final=True, step="")
    state.runner.request("implement")
    return ""


def _after_final(state, job: dict) -> str:
    return "" if state.workflow.data["stage"] != "implement" else NO_REPORT


# --------------------------------------------------------------------- после запуска


def after_job(state, job: dict) -> str | None:
    """Запуск закончился успешно: продвинуть ход. ``None`` — запуск не наш (обычный этап),
    ``""`` — продвинулись, иначе причина остановки для человека."""
    kind = job.get("kind")
    if kind not in ("ideas", "decide", "unit", "check", "fix", "final"):
        return None
    wf = state.workflow
    if wf.data["stage"] != job["stage"] or job.get("sig") != _signature(wf.data["pipeline"]):
        return None  # человек успел сместить цепочку: этот запуск уже не про неё
    with state.lock:
        if kind == "ideas":
            return _after_ideas(state, job)
        if kind == "decide":
            return _after_decide(state, job)
        if kind == "final":
            return _after_final(state, job)
        return _after_unit(state, job)


__all__ = [
    "MAX_FIXES",
    "accepted_units",
    "after_job",
    "decide_cards",
    "ideas_steps",
    "job_for",
    "make_units",
    "on_ideas_ready",
    "options",
    "record_check",
    "start_units",
    "unit_mode",
]
