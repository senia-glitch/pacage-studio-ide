"""«ТЗ из банка»: срез банка для исполнителя и автора тестов. Собирает код, а не агент.

В ТЗ входит «конституция» (принятые границы, ограничения, словарь и стиль), сценарий с диаграммой,
его требования и критерии, страницы и вызовы (контракт) или эндпоинты и таблицы. ТЗ помнит версии
записей, из которых собрано: запись стала «устарело» или изменилась — ТЗ устарело, и это видно.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from studio.pipeline.graph import ACCEPTED

from . import stack, waves
from .engine import Pipeline
from .errors import PipelineError

DIR = Path(".studio") / "tz"
FRONT_RULES = (
    stack.FRONTEND_GUIDE
    + "\nТесты в frontend/tests/pipeline не меняй. Данные берёшь из моков по контракту."
)
BACK_RULES = (
    stack.BACKEND_GUIDE
    + "\nТесты в tests/pipeline не меняй. Фронтенд не трогай. Документация стека лежит в docs/ "
    "проекта (fastbase.md, core-package.md, event-infra.md, full-stack.md): читай её при сомнениях."
)


def _versions(cards: list[dict]) -> str:
    """Отпечаток версий записей. Принятие на воротах его не меняет, а правка или «устарело» — меняет."""
    parts = []
    for c in cards:
        mark = ":устарело" if c["status"] == "stale" else ""
        parts.append(f"{c['id']}:{(c.get('attrs') or {}).get('version', 1)}{mark}")
    return hashlib.sha256(";".join(parts).encode("utf-8")).hexdigest()[:16]


def _scenario_cards(pl: Pipeline, scenario: dict, stage: int) -> dict:
    bank = pl.bank
    reqs = [p for p in bank.parents(scenario["id"]) if p["kind"] == "requirement"]
    criteria = list(bank.children(scenario["id"]))
    for req in reqs:
        criteria += [c for c in bank.children(req["id"]) if c["kind"] == "criterion"]
    criteria = [c for c in criteria if c["kind"] == "criterion"]
    pages = [c for c in bank.children(scenario["id"]) if c["kind"] == "page"]
    calls = [c for page in pages for c in bank.children(page["id"]) if c["kind"] == "call"]
    constitution = bank.constitution()
    for style in bank.records("style"):  # стиль выбран человеком до ворот — в ТЗ он уже нужен
        if style["id"] not in {c["id"] for c in constitution}:
            constitution.append(style)
    out = {
        "constitution": constitution,
        "requirements": reqs,
        "criteria": list({c["id"]: c for c in criteria}.values()),
        "pages": pages,
        "calls": calls,
        "tables": [],
    }
    if stage == 4:
        out["endpoints"] = [
            e for call in calls for e in bank.children(call["id"]) if e["kind"] == "endpoint"
        ]
        out["tables"] = [t for t in bank.children(scenario["id"]) if t["kind"] == "table"]
    return out


def render(pl: Pipeline, scenario: dict, stage: int) -> tuple[str, list[dict]]:
    parts = _scenario_cards(pl, scenario, stage)
    lines = [f"# ТЗ: {scenario['title']} (этап {stage})", ""]
    lines += ["## Правила", "", FRONT_RULES if stage == 3 else BACK_RULES, ""]
    lines += ["## Конституция проекта", ""]
    for c in parts["constitution"]:
        lines.append(f"- [{c['kind']}] {c['title']}" + (f" — {c['body']}" if c.get("body") else ""))
    lines += ["", "## Сценарий", "", scenario["body"] or scenario["title"], ""]
    diagram = (scenario.get("attrs") or {}).get("diagram") or {}
    for number, step in enumerate(diagram.get("steps") or [], 1):
        lines.append(
            f"{number}. {step['from']} → {step['to']}: {step['text']} [{step.get('tag') or '-'}]"
        )
    lines += ["", "## Требования и критерии приёмки", ""]
    for req in parts["requirements"]:
        lines.append(f"- {req['title']}")
    for crit in parts["criteria"]:
        how = (
            "проверяется глазами"
            if (crit.get("attrs") or {}).get("check") == "visual"
            else "проверяется тестом"
        )
        lines.append(f"  - {crit['body'] or crit['title']} ({how})")
    if stage == 3:
        lines += ["", "## Страницы и контракт API (моки)", ""]
        for page in parts["pages"]:
            lines.append(
                f"- Страница «{page['title']}» {(page.get('attrs') or {}).get('route', '')}: {page['body']}"
            )
        for call in parts["calls"]:
            a = call.get("attrs") or {}
            lines.append(f"- Вызов «{call['title']}»: {a.get('method', '')} {a.get('path', '')}")
            lines.append(f"  запрос: {json.dumps(a.get('request'), ensure_ascii=False)}")
            lines.append(f"  ответ: {json.dumps(a.get('response'), ensure_ascii=False)}")
            lines.append(f"  мок: {json.dumps(a.get('mock'), ensure_ascii=False)}")
        for style in [c for c in parts["constitution"] if c["kind"] == "style"]:
            lines += [
                "",
                f"## Стиль и каркас: {style['title']}",
                "",
                json.dumps(style.get("attrs"), ensure_ascii=False),
            ]
    else:
        lines += ["", "## Эндпоинты и таблицы", ""]
        for ep in parts["endpoints"]:
            a = ep.get("attrs") or {}
            lines.append(
                f"- {a.get('method', '')} {a.get('path', '')} — {ep['title']}: {ep['body']}"
            )
        for table in parts["tables"]:
            cols = ", ".join(
                f"{c['name']}:{c['type']}" for c in (table.get("attrs") or {}).get("columns") or []
            )
            lines.append(f"- таблица {table['title']}({cols})")
    tests = [
        t
        for t in pl.bank.records("test")
        if any(p["id"] in {c["id"] for c in parts["criteria"]} for p in pl.bank.parents(t["id"]))
    ]
    if tests:
        lines += ["", "## Тесты (только читать, не менять)", ""]
        lines += [f"- {(t.get('attrs') or {}).get('file', t['title'])}" for t in tests]
    cards = [scenario, *parts["requirements"], *parts["criteria"], *parts["pages"], *parts["calls"],
             *parts.get("endpoints", []), *parts.get("tables", []), *parts["constitution"]]  # fmt: skip
    return "\n".join(lines) + "\n", cards


def build(pl: Pipeline, stage: int, scenarios: list[dict] | None = None) -> list[dict]:
    """Собрать ТЗ по принятым сценариям этапа и запомнить версии записей."""
    if stage not in (3, 4):
        raise PipelineError("bad_stage", "ТЗ собираются для этапов 3 и 4")
    items = (
        scenarios
        if scenarios is not None
        else waves.records(pl, "scenario", status=ACCEPTED)
    )
    out_dir = pl.project / DIR / f"stage{stage}"
    out_dir.mkdir(parents=True, exist_ok=True)
    index = _index(pl)
    made = []
    for scenario in items:
        text, cards = render(pl, scenario, stage)
        path = out_dir / f"{scenario['id']}.md"
        path.write_text(text, encoding="utf-8")
        digest = _versions(cards)
        index[f"{stage}:{scenario['id']}"] = digest
        made.append(
            {"scenario": scenario["id"], "path": str(path.relative_to(pl.project)), "hash": digest}
        )
    (pl.project / DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return made


def built(pl: Pipeline, stage: int) -> bool:
    """ТЗ собрано для каждого принятого сценария (в волнах — текущей волны) и не устарело."""
    scenarios = waves.records(pl, "scenario", status=ACCEPTED)
    folder = pl.project / DIR / f"stage{stage}"
    return (
        bool(scenarios)
        and all((folder / f"{s['id']}.md").is_file() for s in scenarios)
        and not stale(pl, stage)
    )


def _index(pl: Pipeline) -> dict:
    try:
        return json.loads((pl.project / DIR / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def stale(pl: Pipeline, stage: int) -> list[str]:
    """Сценарии, чьё ТЗ собрано из версий, которые с тех пор изменились."""
    index = _index(pl)
    out = []
    for scenario in pl.bank.records("scenario", status=ACCEPTED):
        key = f"{stage}:{scenario['id']}"
        if key in index:
            _, cards = render(pl, scenario, stage)
            if _versions(cards) != index[key]:
                out.append(scenario["id"])
    return out


def read(pl: Pipeline, stage: int, scenario_id: str) -> str:
    path = pl.project / DIR / f"stage{stage}" / f"{scenario_id}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        raise PipelineError("no_tz", "ТЗ ещё не собрано: выполните шаг «ТЗ из банка»") from None
