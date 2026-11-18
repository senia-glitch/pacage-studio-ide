"""Ход конвейера: шаги всех этапов, «что дальше», доработка по замечаниям ворот и автопилот.

Координатор не пишет в банк сам: он выбирает шаг, зовёт роль, запускает ворота и по режиму участия
решает, нужен ли человек. Принятое этапом k читает этап k+1: этап открывается, когда последние
ворота предыдущего этапа пройдены.
"""

from __future__ import annotations

from . import (
    advice,
    audit,
    clarify,
    debt,
    integrate,
    review,
    stage1,
    stage2,
    stage3,
    stage4,
    stage5,
    waves,
)
from .engine import Pipeline
from .errors import PipelineError
from .gates import GATES, STAGE_GATES, gate

SPECIAL = ("questions", "style", "variants")  # шаги, которые автопилот делает в обход run_step
LIMIT = 600  # предел шагов за один запуск (защита от зацикливания; доработка и так ограничена)
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
    asked = pl.settings.mode == "gates" and bool(clarify.pending(pl))
    for stage, module in STAGES.items():
        items = module.steps(pl)
        if not stage_open(pl, stage):
            items = [{**i, "state": "blocked"} if i["state"] == "available" else i for i in items]
        elif asked:  # «На воротах»: есть вопросы к человеку — до ответов дальше не идём
            items = [
                {**i, "state": "blocked", "hint": "ответьте на вопросы студии"}
                if i["state"] == "available"
                else i
                for i in items
            ]
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
    before = clarify.last_id(pl)
    result = STAGES[stage].run_step(pl, key)
    if key.startswith("gate_") or key == "questions":
        return result
    result = _repair(pl, stage, key, result)
    made = clarify.after_step(pl, stage, key, before)  # критик и сомнения авторов → вопросы
    review.after_step(pl, stage, key)  # «Идея»: после каждого шага — описание банка, в любом режиме
    if made and isinstance(result, dict):
        result["questions"] = [q["id"] for q in made]
    return result


def _repair(pl: Pipeline, stage: int, key: str, result):
    """Записи, потерявшие родителя при создании, студия не бросает до ворот: один раз переспрашивает
    автора со списком допустимых кодов (шаг сам берёт только то, чего ещё нет)."""
    if not isinstance(result, dict) or not result.get("skipped"):
        return result
    pl.llm.feedback = (
        "Прошлая попытка потеряла записи, потому что не указан или неверен код родителя:\n"
        + "\n".join(f"- {line}" for line in result["skipped"])
        + "\nУкажи точный код родителя из списка выше для каждой записи."
    )
    try:
        again = STAGES[stage].run_step(pl, key)
    except PipelineError:
        return result  # повтор не получился: останется то, что было, ворота и внедрение закроют
    finally:
        pl.llm.feedback = ""
    if isinstance(again, dict):
        result["created"] = [*result.get("created", []), *again.get("created", [])]
        result["skipped"] = again.get("skipped", [])
    return result


# ---------------------------------------------------------------------- доработка


def feedback_text(run: dict) -> str:
    lines = [f"- {f['message']}" for f in run.get("code") or []]
    for item in run.get("failing") or []:
        mark = f"{item.get('score', '—')} из {item.get('threshold', 8)}" if "score" in item else ""
        lines.append(f"- {item['title']}: {item['why']} {mark}".strip())
    reasons = audit.lines(run)
    if reasons:
        lines.append("Разбор причин независимым аудитором (что именно исправить):")
        lines += reasons
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
    answers = clarify.answers_text(pl, run.get("id", ""))  # ответы человека по этим воротам
    feedback = feedback_text(run) + answers
    drafts = [c for kind in spec.subject for c in pl.bank.records(kind, status="proposed")]
    changed = clarify.revise_drafts(pl, role, drafts, feedback)
    pl.llm.feedback = feedback
    try:
        filled = fill(pl)
    finally:
        pl.llm.feedback = ""
    # записи, у которых пропал родитель, не оставляем: агент встраивает их в цепочку или отклоняет
    integrated = integrate.run(pl) if integrate.orphans(pl) else {}
    upstream = audit.escalate(pl, run)  # причина выше по цепочке: запрос на изменение принятого
    dropped = audit.apply_rejects(pl, run)  # недоводимое отклоняет автопилот, остальные — человек
    clarify.mark_applied(pl, run.get("id", ""))  # ответы человека учтены этой доработкой
    return {
        "changed": changed,
        "filled": filled,
        "integrated": integrated,
        "upstream": upstream,
        "dropped": dropped,
    }


def apply_answers(pl: Pipeline, batch: str):
    """Ответили на все вопросы пачки: студия сама меняет систему. Вопросы аудита ворот — доработка с
    ответами и ворота заново, вопросы шага — правка записей, к которым они относились."""
    questions = clarify.batch_questions(pl, batch)
    gate_key = next((q["attrs"]["gate"] for q in questions if q["attrs"].get("gate")), "")
    if not gate_key:
        changed = clarify.refine(pl, batch)
        clarify.mark_applied(pl, batch)
        return {"refined": changed}
    run = pl.keeper.run(batch)
    decided = clarify.apply_decisions(pl, batch)  # правка принятого выше и отклонения — по ответам
    if gate_key in REWORK:
        rework(pl, gate_key, run)
    clarify.mark_applied(pl, batch)
    again = pl.run_gate(gate_key, gate_facts(pl, gate_key))
    return {**again, "decided": decided}


def answer_for_me(pl: Pipeline, ids: list[str]):
    """«Решить за меня»: студия отвечает на вопросы по своим правилам и применяет ответы."""
    done = clarify.decide(pl, ids)
    batches = list(dict.fromkeys(q["attrs"].get("batch") for q in done if q["attrs"].get("batch")))
    out: dict = {"answered": len(done)}
    for batch in batches:
        if clarify.batch_ready(pl, batch):
            out["applied"] = apply_answers(pl, batch)
    return out


# ---------------------------------------------------------------------- автопилот


def _special(pl: Pipeline, key: str) -> None:
    """Шаги, которые автопилот делает сам, без вопросов человеку."""
    if key == "questions":
        if not pl.bank.records("question"):
            stage1.propose_questions(pl)
        stage1.auto_answer(pl)
    elif key == "style":
        stage3.build_style(pl, "light")  # в автопилоте — стандартная тема без вопросов
    elif key == "variants":
        stage2.propose_variants(pl)
        stage2.auto_choose(pl)


def autopilot(pl: Pipeline, limit: int = LIMIT, stop_after: int | None = None) -> list[dict]:
    """Идёт по шагам, пока не потребуется человек. Журнал — что сделано и почему остановились."""
    if pl.settings.mode != "autopilot":
        raise PipelineError("not_autopilot", "Автопилот включается в Настройки → Режим участия")
    log: list[dict] = []
    tried: set[str] = (
        set()
    )  # шаг без результата (например, конкурентов не нашли) не гоняем по кругу
    wave = waves.current(pl)
    repeats: dict[str, int] = {}
    for _ in range(limit):
        if waves.current(pl) != wave:  # началась новая волна: её шаги — снова с первого
            wave = waves.current(pl)
            tried = {t for t in tried if t[0] < 3}  # этапы 1–2 заново не гоняем
        reopened = debt.reopen(pl)  # долг «ниже порога» — на улучшение, с ранних ворот
        if reopened:
            log.append({"debt": reopened})
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
        if key in SPECIAL:
            repeats[key] = repeats.get(key, 0) + 1
            if repeats[key] > 3:  # шаг выполняется, а доступным остаётся: человек должен вмешаться
                stop = f"шаг «{key}» не продвигается: проверьте его вручную"
                log.append({"step": key, "stop": stop})
                break
        if key in SPECIAL:
            try:
                _special(pl, key)
            except PipelineError as error:
                log.append({"step": key, "stop": str(error)})
                break
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
                if any(f["code"] == "orphan" for f in result.get("code") or []):
                    try:
                        done = integrate.run(pl)  # висящее: агент внедряет по цепочке или отклоняет
                    except PipelineError as error:
                        entry["stop"] = f"не удалось внедрить висящие записи: {error}"
                        log.append(entry)
                        break
                    entry["integrated"] = done
                    log.append(entry)
                    continue
                fixed = advice.auto_fix(pl, result, ("duplicate",))  # повторы уходят без человека
                if fixed:
                    entry["fixed"] = fixed
                    log.append(entry)
                    continue  # ворота пройдут заново уже без них
                if result.get("stuck") or gate_key not in REWORK:
                    # ниже порога автопилот не принимает: оценка перестала расти — зовём человека
                    entry["stop"] = advice.explain(pl, result)
                    log.append(entry)
                    break
                clarify.settle(pl, result["id"])  # вопросы аудита: студия отвечает сама
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
    else:
        log.append({"stop": f"достигнут предел в {limit} шагов: запустите автопилот ещё раз"})
    return log


__all__ = ["GATES", "autopilot", "next_step", "rework", "run_step", "all_steps", "stage_open"]
