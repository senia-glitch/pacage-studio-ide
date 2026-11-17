"""Ход конвейера: шаги всех этапов, «что дальше», доработка по замечаниям ворот и автопилот.

Координатор не пишет в банк сам: он выбирает шаг, зовёт роль, запускает ворота и по режиму участия
решает, нужен ли человек. Принятое этапом k читает этап k+1: этап открывается, когда последние
ворота предыдущего этапа пройдены.
"""

from __future__ import annotations

from . import prompts, stage1, stage2
from .engine import Pipeline
from .errors import PipelineError
from .gates import GATES, STAGE_GATES, gate

STAGES = {1: stage1, 2: stage2}

# Ворота → (роль-автор, шаг, который закрывает пробелы).
REWORK = {
    "problems": ("analyst", stage1.find_problems),
    "requirements": ("analyst", stage1.write_requirements),
    "scenarios": ("scenarist", stage2.write_scenarios),
    "schema": ("db_designer", stage2.merge_schemas),
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


def next_step(pl: Pipeline, skip: set[str] | frozenset[str] = frozenset()) -> dict | None:
    """Первый шаг, доступный сейчас (``stage`` — этап). ``skip`` — шаги, которые уже пробовали."""
    for stage, items in all_steps(pl).items():
        for item in items:
            if item["state"] == "available" and item["key"] not in skip:
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


def rework(pl: Pipeline, gate_key: str, run: dict) -> dict:
    """Доработка: роль-автор правит черновики по замечаниям, затем закрываются пробелы покрытия."""
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


def autopilot(pl: Pipeline, limit: int = 40) -> list[dict]:
    """Идёт по шагам, пока не потребуется человек. Журнал — что сделано и почему остановились."""
    if pl.settings.mode != "autopilot":
        raise PipelineError("not_autopilot", "Автопилот включается в Настройки → Режим участия")
    log: list[dict] = []
    tried: set[str] = (
        set()
    )  # шаг без результата (например, конкурентов не нашли) не гоняем по кругу
    for _ in range(limit):
        item = next_step(pl, tried)
        if item is None:
            log.append({"stop": "все шаги пройдены"})
            break
        key, stage = item["key"], item["stage"]
        if key == "area":
            log.append({"step": key, "stop": "нужна идея"})
            break
        if key == "questions":
            if not pl.bank.records("question"):
                stage1.propose_questions(pl)
            stage1.auto_answer(pl)
            log.append({"step": key})
            continue
        if key == "variants":
            stage2.propose_variants(pl)
            stage2.auto_choose(pl)
            log.append({"step": key})
            continue
        if not key.startswith("gate_"):
            tried.add(key)
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
                if result.get("stuck") or gate_key not in REWORK:
                    entry["stop"] = "ворота вернули на доработку, круги закончились"
                    log.append(entry)
                    break
                entry["rework"] = rework(pl, gate_key, result)
            elif result.get("human"):
                entry["stop"] = "нужно ваше решение"
                log.append(entry)
                break
        log.append(entry)
    return log


__all__ = ["GATES", "autopilot", "next_step", "rework", "run_step", "all_steps", "stage_open"]
