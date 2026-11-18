"""Аудит ворот: почему порог не пройден и что именно исправить.

Оценщик говорит «критерий ниже порога». Автору этого мало: чтобы доработка помогла, а не переписала
слова, нужен критический разбор причины. Аудит делает независимая модель (та же роль, что и оценщик,
то есть не автор): по каждому критерию она находит корневой дефект — в записях этого этапа или в
принятом выше, — называет записи и формулирует конкретную правку. Недоверие у аудита двустороннее: он
отмечает и случаи, когда замечание оценщика не подтверждается записями.

Результат ложится в прогон ворот (``run["audit"]``). Автор получает его вместе с замечаниями, человек —
на экране ворот. Если причина выше по цепочке, аудит предлагает запрос на изменение принятой записи:
в автопилоте его принимает агент, в остальных режимах он ждёт решения человека.
"""

from __future__ import annotations

from . import prompts
from .engine import Pipeline
from .errors import PipelineError
from .gates import GATES
from .kinds import stage_of
from .stage1 import _items, _s

SYSTEM = (
    "Ты — независимый аудитор качества. Ты не автор записей и не оценщик, а критик: твоя задача — "
    "найти корневую причину, по которой критерий не набрал порог, и сказать автору, что именно "
    "исправить. Не повторяй замечание оценщика своими словами: назови записи и конкретную правку "
    "(что переписать, чего не хватает, что лишнее). Проверяй и самого оценщика: если по записям его "
    "замечание не подтверждается, скажи об этом честно. Если причина не в записях этого этапа, а в "
    "принятом выше, укажи эту запись и предложи правку к ней. Отвечай только JSON."
)
MAX_ITEMS = 12


def _prompt(pl: Pipeline, run: dict) -> str:
    spec = GATES[run["gate"]]
    lines = []
    for item in run.get("failing") or []:
        mark = f"{item['score']} из {item.get('threshold', 8)}" if "score" in item else "без оценки"
        rating = (run.get("ratings") or {}).get(item["key"]) or {}
        evidence = f"; оценщик сослался на: {rating['evidence']}" if rating.get("evidence") else ""
        lines.append(f"- {item['key']} «{item['title']}»: {mark}, {item['why']}{evidence}")
    for found in run.get("code") or []:
        lines.append(f"- проверка кодом ({found['code']}): {found['message']}")
    subject = prompts.render(pl.bank, *spec.subject)
    above = prompts.render(pl.bank, "area", "boundary", "constraint", "problem", "requirement")
    return (
        f"Ворота «{spec.title}» (этап {spec.stage}) не пройдены.\n"
        + "Замечания:\n"
        + "\n".join(lines)
        + f"\n\nЗаписи этих ворот:\n{subject}\n\nПринятое и заданное выше:\n{above}\n\n"
        + "По каждому замечанию дай разбор. cause: \"here\" — дефект в записях этих ворот, "
        '"upstream" — в записи выше (тогда укажи её код в target и правку в change).\n'
        + 'Ответ: {"summary": "главная причина в одной фразе", "items": [{"criterion": "<ключ или code>", '
        '"cause": "here", "records": ["c7"], "defect": "что не так", "fix": "что сделать автору", '
        '"evaluator_ok": true}, {"criterion": "completeness", "cause": "upstream", "target": "c3", '
        '"defect": "...", "fix": "...", "change": {"title": "...", "body": "..."}}]}'
    )


def _clean(pl: Pipeline, run: dict, data) -> dict:
    items = []
    stage = GATES[run["gate"]].stage
    for raw in _items(data, "items")[:MAX_ITEMS]:
        defect, fix = _s(raw.get("defect"), 400), _s(raw.get("fix"), 400)
        if not defect and not fix:
            continue
        item = {
            "criterion": _s(raw.get("criterion"), 60),
            "cause": "here",
            "records": [r for r in _codes(raw.get("records")) if _exists(pl, r)],
            "defect": defect,
            "fix": fix,
            "evaluator_ok": raw.get("evaluator_ok") is not False,
        }
        target = _s(raw.get("target"), 20)
        change = raw.get("change") if isinstance(raw.get("change"), dict) else {}
        if raw.get("cause") == "upstream" and _exists(pl, target):
            card = pl.bank.get(target)
            if card["status"] == "accepted" and stage_of(card["kind"]) < stage:
                title, body = _s(change.get("title"), 200), _s(change.get("body"), 800)
                if title or body:
                    item.update(cause="upstream", target=target, change={"title": title, "body": body})
        items.append(item)
    summary = _s(data.get("summary") if isinstance(data, dict) else "", 300)
    return {"summary": summary, "items": items}


def _codes(value) -> list[str]:
    return [_s(v, 20) for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _exists(pl: Pipeline, record_id: str) -> bool:
    try:
        pl.bank.get(record_id)
    except Exception:  # noqa: BLE001 — модель могла назвать несуществующий код
        return False
    return True


def make(pl: Pipeline, run: dict) -> dict | None:
    """Провести аудит возвращённых ворот и записать его в прогон. Сбой модели не мешает воротам."""
    if run["state"] != "returned" or not (run.get("failing") or run.get("code")):
        return None
    try:
        data = pl.llm.ask_json("evaluator", SYSTEM, _prompt(pl, run))
    except PipelineError:
        return None
    result = _clean(pl, run, data)
    if not result["items"]:
        return None
    pl.keeper.annotate(run["id"], audit=result)
    return result


def lines(run: dict) -> list[str]:
    """Аудит для подсказки автору: что не так и что исправить по каждому замечанию."""
    audit = run.get("audit") or {}
    out = []
    for item in audit.get("items") or []:
        where = f" (записи {', '.join(item['records'])})" if item.get("records") else ""
        if item["cause"] == "upstream":
            out.append(f"- {item['criterion']}: причина выше — запись {item['target']}: {item['defect']}")
        else:
            out.append(f"- {item['criterion']}{where}: {item['defect']} → {item['fix']}")
    return out


def escalate(pl: Pipeline, run: dict) -> list[str]:
    """Причины выше по цепочке: подать запросы на изменение. Автопилот принимает их сам."""
    audit = run.get("audit") or {}
    opened: list[str] = []
    busy = {t for r in pl.changes.open_requests() for t in r.get("targets") or []}
    for item in audit.get("items") or []:
        target = item.get("target")
        if item.get("cause") != "upstream" or not target or target in busy:
            continue
        patch = {k: v for k, v in (item.get("change") or {}).items() if v}
        if not patch:
            continue
        text = f"Аудит ворот «{GATES[run['gate']].title}»: {item['defect']}"
        try:
            request = pl.changes.open(text, "audit", patches={target: patch})
            if not pl.changes.needs_human():
                pl.changes.decide(request["id"], "accept", "autopilot", "причина по аудиту ворот")
        except PipelineError:
            continue
        busy.add(target)
        opened.append(request["id"])
    return opened


__all__ = ["escalate", "lines", "make"]
