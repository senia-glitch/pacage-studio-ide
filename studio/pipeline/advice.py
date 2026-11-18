"""Что делать, если ворота не пройдены: понятные причины, варианты решения и решения для автопилота.

Замечания ворот — это коды (``orphan``, ``uncovered``…) и критерии оценщика ниже порога. Человеку
нужно не это, а ответ на вопрос «что мне нажать». Здесь для каждого замечания собираются варианты
(``options``): ``key`` говорит интерфейсу, какую кнопку нарисовать, ``recommended`` — какой вариант
лучший. Автопилот использует те же замечания: то, что можно закрыть без человека, закрывает сам.
"""

from __future__ import annotations

from .engine import Pipeline
from .errors import PipelineError
from .gates import GATES

# Шаг, который закрывает пробелы своих ворот (то же делает «Доработать агентом»).
FIX_STEP = {
    "problems": "1.7 «Проблемы»",
    "requirements": "1.8 «Функциональные требования»",
    "scenarios": "2.3 «Сценарии»",
    "schema": "2.5 «Слияние схем»",
    "frontend": "3.2 «План интерфейса»",
    "backend": "4.1 «План бэкенда»",
}
HANGING = ("orphan", "duplicate")


def _opt(key: str, label: str, hint: str = "", recommended: bool = False) -> dict:
    return {"key": key, "label": label, "hint": hint, "recommended": recommended}


def evaluator_failed(run: dict) -> bool:
    """Оценщик не вернул ни одной оценки: это сбой ответа модели, а не плохой результат."""
    failing = run.get("failing") or []
    return bool(failing) and all(f.get("missing") for f in failing)


def short(run: dict | None) -> str:
    """Одна фраза: почему ворота не пройдены (для плашки, строки состояния и журнала автопилота)."""
    if not run:
        return ""
    if run["state"] == "passed":
        return ""
    code, failing = run.get("code") or [], run.get("failing") or []
    parts = []
    if code:
        parts.append(f"проверки кодом: замечаний {len(code)}")
    if evaluator_failed(run):
        parts.append("оценщик не вернул оценки (сбой ответа модели)")
    elif failing:
        parts.append(f"критериев ниже порога: {len(failing)}")
    if run["state"] == "ready":
        return "всё в порядке, ждут вашего решения"
    return "; ".join(parts) or "ждут решения"


def hanging_ids(pl: Pipeline, run: dict) -> list[str]:
    """Висящие и повторяющиеся черновики: их можно отклонить без последствий для принятого."""
    out: list[str] = []
    for item in run.get("code") or []:
        ids = list(item.get("ids") or [])
        if item["code"] == "duplicate":
            ids = ids[1:]  # первая запись остаётся, повтор уходит
        elif item["code"] != "orphan":
            continue
        for rid in ids:
            try:
                card = pl.bank.get(rid)
            except Exception:  # noqa: BLE001 — запись могли уже отклонить
                continue
            if card["status"] == "proposed" and rid not in out:
                out.append(rid)
    return out


def finding_options(pl: Pipeline, item: dict, gate_key: str) -> list[dict]:
    """Варианты решения для одного замечания проверок кодом."""
    code = item["code"]
    step = FIX_STEP.get(gate_key, "соответствующий шаг")
    if code == "orphan":
        return [
            _opt("link", "Внедрить", "привязать запись к родителю: она станет частью цепочки", True),
            _opt("reject", "Отклонить", "убрать запись в архив, если она не нужна"),
        ]
    if code == "duplicate":
        return [_opt("reject", "Отклонить повтор", "оставить одну запись из двух", True)]
    if code in ("uncovered", "broken_chain"):
        return [
            _opt("rework", "Закрыть пробел агентом", f"агент выполнит шаг {step} с этим замечанием", True),
            _opt("open", "Открыть запись", "добавить недостающее вручную в «Банке знаний»"),
        ]
    if code == "unknown_participant":
        return [
            _opt("rework", "Поправить агентом", "агент приведёт участников диаграммы к словарю", True),
            _opt("hint", "Или добавьте имя в словарь", "шаг 2.1 «Роли и словарь» → ↻"),
        ]
    if code == "variant_choice":
        return [_opt("hint", "Выберите один вариант", "блок «Варианты» в карточке этапа 2 — выберите ровно один", True)]
    if code == "stale":
        return [_opt("rework", "Перепроверить агентом", "запись устарела после изменения выше по цепочке", True)]
    if code == "schema":
        return [_opt("rework", "Поправить схему агентом", f"агент выполнит шаг {step}", True)]
    if code in ("not_checked", "failed"):
        return [_opt("hint", "Выполните шаг этапа", "тесты, замена моков или запуск ещё не пройдены: выполните шаг перед воротами", True)]
    if code == "stack":
        return [_opt("rework", "Поправить агентом", "исполнитель исправит код по замечанию стека", True)]
    return [_opt("rework", "Доработать агентом", "", True)]


def criterion_options(item: dict) -> list[dict]:
    if item.get("missing"):
        return [_opt("rerate", "Оценить заново", "оценщик не вернул оценку — попросим ещё раз", True)]
    return [
        _opt("rework", "Доработать агентом", "агент-автор учтёт замечание оценщика", True),
        _opt("open", "Поправить самому", "открыть записи в «Банке знаний»"),
    ]


def for_gate(pl: Pipeline, run: dict | None) -> dict:
    """Всё для экрана: сводка, общие действия, варианты по каждому замечанию и критерию."""
    if not run or run["state"] == "passed":
        return {"summary": "", "lines": [], "actions": [], "findings": [], "criteria": {}}
    gate_key = run["gate"]
    title = GATES[gate_key].title
    code, failing = run.get("code") or [], run.get("failing") or []
    lines = [f"{f['message']}" for f in code]
    if run.get("eval_note"):
        lines.append(f"Оценщик: {run['eval_note']}")
    for f in failing:
        if f.get("missing"):
            lines.append(f"Критерий «{f['title']}»: оценщик не вернул оценку")
        elif "score" in f and "threshold" in f:
            lines.append(f"Критерий «{f['title']}»: {f['score']} из {f['threshold']} — {f['why']}")
        else:
            lines.append(f"Критерий «{f['title']}»: {f['why']}")
    actions = []
    hang = hanging_ids(pl, run)
    if evaluator_failed(run):
        actions.append(_opt("rerate", "Оценить заново", "повторить независимую оценку", True))
        actions.append(_opt("settings", "Сменить модель оценщика", "в «Настройки → Роли агентов»; оценщик — другая модель, чем авторы"))
    elif code or failing:
        actions.append(_opt("rework", "Доработать агентом", "автор поправит черновики и закроет пробелы, ворота пройдут заново", True))
    if hang:
        actions.append(_opt("reject_all", f"Отклонить висящее ({len(hang)})", "убрать черновики без родителя и повторы"))
    if code and not evaluator_failed(run):
        actions.append(_opt("rerate", "Проверить заново", "после правок записей: ворота пройдут ещё раз"))
    if failing and not code and not evaluator_failed(run):
        actions.append(_opt("accept_below", "Принять ниже порога", "записи получат пометку «ниже порога»"))
    if run["state"] == "ready":
        actions.append(_opt("accept", "Принять", "все слои пройдены", True))
    summary = f"Ворота «{title}»: {short(run)}."
    return {
        "summary": summary,
        "lines": lines,
        "actions": actions,
        "findings": [finding_options(pl, f, gate_key) for f in code],
        "criteria": {f["key"]: criterion_options(f) for f in failing},
    }


def auto_fix(pl: Pipeline, run: dict) -> list[str]:
    """Автопилот: закрыть то, что можно без человека — отклонить висящие и повторяющиеся черновики."""
    done = []
    for rid in hanging_ids(pl, run):
        card = pl.bank.get(rid)
        pl.bank.reject(rid, "autopilot", "автопилот: висящая или повторяющаяся запись отклонена")
        done.append(f"отклонена «{card['title']}»")
    return done


def explain(pl: Pipeline, run: dict, rounds: int | None = None) -> str:
    """Текст остановки автопилота: что не прошло и что можно сделать (вместо «круги закончились»)."""
    data = for_gate(pl, run)
    head = f"ворота «{GATES[run['gate']].title}» не пройдены"
    if rounds:
        head += f" после {rounds} кругов"
    reasons = "; ".join(data["lines"][:3]) or short(run)
    more = len(data["lines"]) - 3
    tail = f" (и ещё {more})" if more > 0 else ""
    options = ", ".join(o["label"] for o in data["actions"][:3])
    return f"{head}: {reasons}{tail}. Что можно сделать: {options or 'откройте плашку ворот'}."


def fix(pl: Pipeline, run_id: str, action: str) -> dict:
    """Действие над воротами одним нажатием (``reject_all``); остальные идут через свои маршруты."""
    run = pl.keeper.run(run_id)
    if action == "reject_all":
        done = auto_fix(pl, run)
        if not done:
            raise PipelineError("nothing_to_do", "Висящих и повторяющихся черновиков нет")
        return {"rejected": len(done), "items": done}
    raise PipelineError("bad_action", f"Неизвестное действие: {action!r}")


__all__ = [
    "auto_fix",
    "evaluator_failed",
    "explain",
    "finding_options",
    "fix",
    "for_gate",
    "hanging_ids",
    "short",
]
