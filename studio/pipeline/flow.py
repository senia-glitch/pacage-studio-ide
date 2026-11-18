"""Ход конвейера: шаги всех этапов, «что дальше», доработка по замечаниям ворот и автопилот.

Координатор не пишет в банк сам: он выбирает шаг, зовёт роль, запускает ворота и по режиму участия
решает, нужен ли человек. Принятое этапом k читает этап k+1: этап открывается, когда последние
ворота предыдущего этапа пройдены.
"""

from __future__ import annotations

from . import advice, prompts, stage1, stage2, stage3, stage4, stage5, waves
from .engine import Pipeline
from .errors import PipelineError
from .gates import GATES, STAGE_GATES, gate

STAGES = {1: stage1, 2: stage2, 3: stage3, 4: stage4, 5: stage5}

# Ворота → (роль-автор, шаг, который закрывает пробелы).
REWORK = {
    "problems": ("analyst", stage1.find_problems),
    "requirements": ("analyst", stage1.write_requirements),
    "scenarios": ("scenarist", stage2.write_scenarios),
    "schema": ("db_designer", stage2.merge_schemas),
    "frontend": ("ui_designer", stage3.plan_interface),
    "backend": ("backend_designer", stage4.plan_backend),
}


def stage_open(pl: Pipeline, stage: int) -> bool:
    """Этап открыт, если ворота предыдущего этапа пройдены (первый этап открыт всегда)."""
    if stage == 1:
        return True
    last = pl.keeper.last(STAGE_GATES[stage - 1][-1])
    return bool(last and last["state"] == "passed")


def all_steps(pl: Pipeline) -> dict[int, list[dict]]:
    out = {}
    for stage, module in STAGES.items():
        items = module.steps(pl)
        if not stage_open(pl, stage):
            items = [{**i, "state": "blocked"} if i["state"] == "available" else i for i in items]
        out[stage] = items
    return out


def next_step(pl: Pipeline, skip: set | frozenset = frozenset()) -> dict | None:
    """Первый шаг, доступный сейчас (``stage`` — этап). ``skip`` — шаги, которые уже пробовали."""
    for stage, items in all_steps(pl).items():
        for item in items:
            if item["state"] == "available" and (stage, item["key"]) not in skip:
                return {**item, "stage": stage}
    return None


def run_step(pl: Pipeline, stage: int, key: str):
    if stage not in STAGES:
        raise PipelineError("unknown_stage", f"Этап {stage} ещё не подключён")
    return STAGES[stage].run_step(pl, key)


# ---------------------------------------------------------------------- доработка


def feedback_text(run: dict) -> str:
    lines = [f"- {f['message']}" for f in run.get("code") or []]
    for item in run.get("failing") or []:
        mark = f"{item.get('score', '—')} из {item.get('threshold', 8)}" if "score" in item else ""
        lines.append(f"- {item['title']}: {item['why']} {mark}".strip())
    return "\n".join(lines) or "- доработайте по замечаниям человека"


def gate_facts(pl: Pipeline, gate_key: str) -> dict | None:
    """Внешние факты ворот (тесты, моки, запуск): их собирает этап, а не оценщик."""
    sources = {"frontend": stage3.facts, "backend": stage4.facts, "final": stage5.facts}
    source = sources.get(gate_key)
    return source(pl) if source else None


def rework(pl: Pipeline, gate_key: str, run: dict) -> dict:
    """Доработка: роль-автор правит черновики по замечаниям, затем закрываются пробелы покрытия."""
    if gate_key not in REWORK:
        raise PipelineError(
            "no_rework",
            "У итоговой приёмки нет доработки: внесите правку запросом на изменение "
            "и пройдите итоговую приёмку заново",
        )
    spec = gate(gate_key)
    role, fill = REWORK[gate_key]
    feedback = feedback_text(run)
    drafts = [c for kind in spec.subject for c in pl.bank.records(kind, status="proposed")]
    changed = 0
    if drafts:
        prompt = (
            "Замечания ворот к твоей работе:\n"
            + feedback
            + "\n\nЧерновики:\n"
            + "\n".join(prompts.line(c) for c in drafts)
            + "\n\nИсправь то, что можно исправить правкой текста, и отклони лишнее. Пробелы "
            "(чего-то не хватает) закроют отдельным шагом.\n"
            'Ответ: {"revise": [{"id": "c1", "title": "...", "body": "..."}], '
            '"reject": [{"id": "c2"}]}'
        )
        data = pl.llm.ask_json(role, prompts.SYSTEM[role], prompt)
        live = {c["id"] for c in drafts}
        for item in stage1._items(data, "revise"):
            cid = stage1._s(item.get("id"), 20)
            if cid in live and (item.get("title") or item.get("body")):
                title = stage1._s(item.get("title"), 200) or None
                body = stage1._s(item.get("body"), 800) or None
                pl.bank.revise(
                    cid, role, title=title, body=body, note="доработка по замечаниям ворот"
                )
                changed += 1
        for item in stage1._items(data, "reject"):
            cid = stage1._s(item.get("id"), 20)
            if cid in live:
                pl.bank.reject(cid, role, "отклонено при доработке")
                changed += 1
    pl.llm.feedback = feedback
    try:
        filled = fill(pl)
    finally:
        pl.llm.feedback = ""
    return {"changed": changed, "filled": filled}


# ---------------------------------------------------------------------- автопилот


def _accept_below(pl: Pipeline, run: dict) -> list[str]:
    """Круги доработки кончились, проверки кодом чистые, а оценщик строже порога: автопилот
    принимает ниже порога с пометкой на записях (человек видит её и может вернуться). Если
    оценщик не дал оценок вообще, принимать нечего: он не оценивал."""
    failing = run.get("failing") or []
    if (
        run.get("code")
        or not failing
        or advice.evaluator_failed(run)
        or not pl.settings.autopilot_accept_below
        or run["state"] != "returned"
    ):
        return []
    note = "автопилот: круги доработки закончились, принято ниже порога"
    pl.keeper.decide(run["id"], "accept_below", "autopilot", note)
    return [f["title"] for f in failing]


def autopilot(pl: Pipeline, limit: int = 80, stop_after: int | None = None) -> list[dict]:
    """Идёт по шагам, пока не потребуется человек. Журнал — что сделано и почему остановились."""
    if pl.settings.mode != "autopilot":
        raise PipelineError("not_autopilot", "Автопилот включается в Настройки → Режим участия")
    log: list[dict] = []
    tried: set[str] = (
        set()
    )  # шаг без результата (например, конкурентов не нашли) не гоняем по кругу
    wave = waves.current(pl)
    for _ in range(limit):
        if waves.current(pl) != wave:  # началась новая волна: её шаги — снова с первого
            wave = waves.current(pl)
            tried = {t for t in tried if t[0] < 3}  # этапы 1–2 заново не гоняем
        item = next_step(pl, tried)
        if item is None:
            log.append({"stop": "все шаги пройдены"})
            break
        key, stage = item["key"], item["stage"]
        if stop_after is not None and stage > stop_after:
            log.append({"stop": f"этап {stop_after} пройден"})
            break
        if key == "area":
            log.append({"step": key, "stop": "нужна идея"})
            break
        if key == "questions":
            if not pl.bank.records("question"):
                stage1.propose_questions(pl)
            stage1.auto_answer(pl)
            log.append({"step": key})
            continue
        if key == "style":
            stage3.build_style(pl, "light")  # в автопилоте — стандартная тема без вопросов
            log.append({"step": key})
            continue
        if key == "variants":
            stage2.propose_variants(pl)
            stage2.auto_choose(pl)
            log.append({"step": key})
            continue
        if not key.startswith("gate_"):
            tried.add((stage, key))
        try:
            result = run_step(pl, stage, key)
        except PipelineError as error:
            log.append({"step": key, "stop": str(error)})
            break
        entry: dict = {"step": key}
        if key.startswith("gate_"):
            entry["gate"] = result["state"]
            if result["state"] == "returned":
                gate_key = key[5:]
                fixed = advice.auto_fix(pl, result)  # висящее и повторы отклоняются без человека
                if fixed:
                    entry["fixed"] = fixed
                    log.append(entry)
                    continue  # ворота пройдут заново уже без них
                if result.get("stuck") or gate_key not in REWORK:
                    taken = _accept_below(pl, result)
                    if taken:
                        entry["accepted_below"] = taken
                        log.append(entry)
                        continue
                    entry["stop"] = advice.explain(pl, result, pl.settings.max_rounds)
                    log.append(entry)
                    break
                entry["rework"] = rework(pl, gate_key, result)
            elif result.get("human"):
                entry["stop"] = "нужно ваше решение: " + (
                    "итоговая приёмка остаётся за вами"
                    if GATES[key[5:]].final
                    else advice.short(result) or "ворота ждут решения"
                )
                log.append(entry)
                break
        log.append(entry)
    return log


__all__ = ["GATES", "autopilot", "next_step", "rework", "run_step", "all_steps", "stage_open"]
