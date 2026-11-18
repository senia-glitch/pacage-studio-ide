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

from . import clarify, prompts
from .engine import Pipeline
from .errors import PipelineError
from .gates import GATES
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
    if spec.stage == 1:
        from . import interview

        above = interview.dossier(pl)  # «Идея»: весь банк и весь диалог, а не выборка
    return (
        f"Ворота «{spec.title}» (этап {spec.stage}) не пройдены.\n"
        + "Замечания:\n"
        + "\n".join(lines)
        + f"\n\nЗаписи этих ворот:\n{subject}\n\nПринятое и заданное выше:\n{above}\n\n"
        + "По каждому замечанию дай разбор. cause: \"here\" — дефект в записях этих ворот, "
        '"upstream" — в записи выше (тогда укажи её код в target и правку в change). '
        'action: "improve" — запись можно довести правкой, "reject" — довести нельзя (лишняя, '
        "дублирует другую или противоречит цепочке): назови её в records, её отклонят. "
        'cause: "ask" — не хватает данных, которые знает только человек (автор их выдумал бы): дай '
        "в questions закрытые вопросы с вариантами (text, options, about — код записи).\n"
        + 'Ответ: {"summary": "главная причина в одной фразе", "items": [{"criterion": "<ключ или code>", '
        '"cause": "here", "records": ["c7"], "defect": "что не так", "fix": "что сделать автору", '
        '"action": "improve", "evaluator_ok": true}, {"criterion": "completeness", "cause": "upstream", "target": "c3", '
        '"defect": "...", "fix": "...", "change": {"title": "...", "body": "..."}}, '
        '{"criterion": "clarity", "cause": "ask", "defect": "неясно, кто пользователь", "questions": '
        '[{"text": "...", "options": ["...", "..."], "about": "c7"}]}]}'
    )


def _clean(pl: Pipeline, run: dict, data) -> dict:
    items = []
    for raw in _items(data, "items")[:MAX_ITEMS]:
        defect, fix = _s(raw.get("defect"), 400), _s(raw.get("fix"), 400)
        questions = [q for q in _items(raw, "questions") if _s(q.get("text"), 300)]
        if not defect and not fix and not questions:
            continue
        item = {
            "criterion": _s(raw.get("criterion"), 60),
            "cause": "here",
            "records": [r for r in _codes(raw.get("records")) if _exists(pl, r)],
            "action": "reject" if raw.get("action") == "reject" else "improve",
            "defect": defect,
            "fix": fix,
            "evaluator_ok": raw.get("evaluator_ok") is not False,
        }
        if raw.get("cause") == "ask" and questions:
            item.update(cause="ask", questions=questions)
        target = _s(raw.get("target"), 20)
        change = raw.get("change") if isinstance(raw.get("change"), dict) else {}
        if raw.get("cause") == "upstream" and _exists(pl, target):
            card = pl.bank.get(target)
            if card["status"] == "accepted" and card["kind"] not in GATES[run["gate"]].subject:
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
    if pl.settings.mode != "manual":
        _ask(pl, run, result)
    return result


def _ask(pl: Pipeline, run: dict, result: dict) -> None:
    """Не хватает данных или нужна правка выше по цепочке: студия спрашивает человека, а не требует
    ручной работы. Автопилот отвечает на эти вопросы сам (``clarify.settle``)."""
    gate = run["gate"]
    clarify.drop_pending(pl, gate)
    if GATES[gate].stage == 1:
        # «Идея»: критерии ворот расплывчаты, поэтому замечания обсуждаются с человеком по одному вопросу
        from . import interview

        interview.discuss(pl, run)
        return
    items: list[dict] = []
    for item in result["items"]:
        if item.get("cause") == "ask":
            items += item.get("questions") or []
    if pl.settings.mode == "gates":  # в «На воротах» человек решает, а не правит руками
        for item in result["items"]:
            if item.get("cause") == "upstream":
                what = item["change"].get("title") or item["change"].get("body")
                items.append(
                    {
                        "text": f"Принятая запись {item['target']} мешает: {item['defect']}. Исправить её так: «{what}»?",
                        "options": ["Да, исправить", "Нет, оставить как есть"],
                        "about": item["target"],
                        "upstream": {"target": item["target"], "patch": item["change"]},
                    }
                )
        drop = rejectable(pl, {**run, "audit": result})
        if drop:
            titles = ", ".join(f"«{pl.bank.get(r)['title']}»" for r in drop)
            items.append(
                {
                    "text": f"Записи {titles} довести до порога нельзя. Отклонить?",
                    "options": ["Да, отклонить", "Нет, оставить"],
                    "about": drop[0],
                    "reject": drop,
                }
            )
    made = clarify.create(pl, items, source="audit", batch=run["id"], gate=gate)
    if made:
        pl.keeper.annotate(run["id"], audit={**result, "asked": [q["id"] for q in made]})


def lines(run: dict) -> list[str]:
    """Аудит для подсказки автору: что не так и что исправить по каждому замечанию."""
    audit = run.get("audit") or {}
    out = []
    for item in audit.get("items") or []:
        where = f" (записи {', '.join(item['records'])})" if item.get("records") else ""
        if item["cause"] == "ask":
            out.append(f"- {item['criterion']}: не хватает данных — {item['defect'] or 'нужны ответы человека'}")
        elif item["cause"] == "upstream":
            out.append(f"- {item['criterion']}: причина выше — запись {item['target']}: {item['defect']}")
        elif item.get("action") == "reject":
            out.append(f"- {item['criterion']}{where}: довести нельзя ({item['defect']})")
        else:
            out.append(f"- {item['criterion']}{where}: {item['defect']} → {item['fix']}")
    return out


def rejectable(pl: Pipeline, run: dict) -> list[str]:
    """Записи этих ворот, которые аудит велел отклонить: не принятые и не последние своего вида."""
    spec = GATES[run["gate"]]
    out: list[str] = []
    for item in (run.get("audit") or {}).get("items") or []:
        if item.get("action") != "reject":
            continue
        for rid in item.get("records") or []:
            card = pl.bank.get(rid)
            alive = [c for c in pl.bank.records(card["kind"]) if c["id"] not in (*out, rid)]
            if (
                card["kind"] in spec.subject
                and card["status"] in ("proposed", "stale")
                and alive
                and rid not in out
            ):
                out.append(rid)
    return out


def apply_rejects(pl: Pipeline, run: dict) -> list[str]:
    """Автопилот отклоняет записи, которые аудит признал недоводимыми; остальные режимы — человек."""
    if pl.changes.needs_human():
        return []
    done = []
    for rid in rejectable(pl, run):
        pl.bank.reject(rid, "autopilot", "аудит: довести до порога нельзя")
        done.append(rid)
    return done


def escalate(pl: Pipeline, run: dict) -> list[str]:
    """Причины выше по цепочке: подать запросы на изменение. Автопилот принимает их сам."""
    audit = run.get("audit") or {}
    opened: list[str] = []
    if pl.settings.mode == "gates":
        return opened  # в «На воротах» это вопрос человеку (см. ``_ask``), а не запрос на одобрение
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


__all__ = ["apply_rejects", "escalate", "lines", "make", "rejectable"]
