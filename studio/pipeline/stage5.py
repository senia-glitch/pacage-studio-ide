"""Этап 5 «Общий анализ и правки»: запуск с демо-данными, сквозные тесты, итоговая приёмка, итог.

Этап сам ничего не правит: замечания ворот становятся записями «замечание» и запросами на
изменение, правит этап-владелец. Итоговая приёмка в автопилоте остаётся за человеком.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from studio.pipeline.graph import ACCEPTED

from . import prompts, stage3
from .engine import Pipeline
from .errors import PipelineError
from .stage1 import _items, _s, _step

DEMO = Path(".studio") / "demo" / "seed.json"
DOCS = "Документация.md"
REPORTS = Path(".studio") / "reports"


class Launcher:
    """Запуск приложения проекта. Настоящий — через серверы студии; тесты подкладывают своего."""

    def start(self, project: Path) -> dict:
        from studio.server.devservers import DevServers

        servers = DevServers(project)
        servers.start("all")
        return {"running": True, "note": "серверы проекта запущены кнопкой студии"}


def entry_ok(pl: Pipeline) -> bool:
    last = pl.keeper.last("backend")
    return bool(last and last["state"] == "passed")


def _need_entry(pl: Pipeline) -> None:
    if not entry_ok(pl):
        raise PipelineError(
            "stage4_first", "Этап 5 берёт только принятое: сначала пройдите «Приёмку бэкенда»"
        )


# ---------------------------------------------------------------------- 5.1 Запуск


def launch(pl: Pipeline) -> dict:
    """5.1 Запуск: демо-данные по сценариям (помощник) и запуск приложения одной кнопкой."""
    _need_entry(pl)
    tables = pl.bank.records("table", status=ACCEPTED)
    listing = "\n".join(
        f"{(t.get('attrs') or {}).get('name')}: "
        + ", ".join(c["name"] for c in (t.get("attrs") or {}).get("columns") or [])
        for t in tables
    )
    prompt = (
        f"Таблицы БД:\n{listing}\n\nСценарии:\n{prompts.render(pl.bank, 'scenario', status=(ACCEPTED,))}\n\n"
        "Придумай небольшие правдоподобные демо-данные (2–4 строки на таблицу), на которых можно "
        "пройти сценарии. Внешние ключи должны ссылаться на существующие строки.\n"
        'Ответ: {"rows": {"doctor": [{"id": 1, "name": "Иванов"}]}}'
    )
    data = pl.llm.ask_json("assistant", prompts.SYSTEM["scenarist"], prompt)
    rows = data.get("rows") if isinstance(data, dict) else None
    known = {(t.get("attrs") or {}).get("name") for t in tables}
    rows = {
        name: [r for r in items if isinstance(r, dict)][:20]
        for name, items in (rows or {}).items()
        if name in known and isinstance(items, list)
    }
    path = pl.project / DEMO
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    info = pl.launcher.start(pl.project)
    data = stage3.results(pl)
    data["launch"] = {"at": time.time(), **info}
    (pl.project / stage3.RESULTS).write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return {"demo_tables": sorted(rows), **info}


# ---------------------------------------------------------------------- 5.2 Сквозные тесты


def write_e2e(pl: Pipeline) -> list[dict]:
    """Сквозные тесты по критериям сценариев на реальном API (пишет автор тестов)."""
    _need_entry(pl)
    covered = {p["id"] for t in pl.bank.records("test") if (t.get("attrs") or {}).get("layer") == "e2e"
               for p in pl.bank.parents(t["id"])}  # fmt: skip
    created = []
    for scenario in pl.bank.records("scenario", status=ACCEPTED):
        criteria = [
            c
            for c in pl.bank.children(scenario["id"])
            if c["kind"] == "criterion"
            and (c.get("attrs") or {}).get("check") != "visual"
            and c["id"] not in covered
        ]
        if criteria:
            context = (
                f"Сценарий «{scenario['title']}»: {scenario['body']}\nПриложение запущено локально."
            )
            created += stage3._write_tests(pl, "e2e", criteria, context)
    return created


def run_e2e(pl: Pipeline) -> dict:
    _need_entry(pl)
    if not [t for t in pl.bank.records("test") if (t.get("attrs") or {}).get("layer") == "e2e"]:
        raise PipelineError("no_e2e", "Сначала напишите сквозные тесты")
    run = pl.tests.run("e2e")
    stage3.save_result(pl, "e2e", run)
    return {"ran": run.ran, "passed": run.passed, "failed": run.failed}


# ---------------------------------------------------------------------- итоговые ворота


def facts(pl: Pipeline) -> dict:
    results = stage3.results(pl)
    layers = [stage3.tests_green(pl, layer) for layer in ("frontend", "backend")]
    green = None if any(x is None for x in layers) else all(layers)
    e2e = stage3.tests_green(pl, "e2e")
    perf = [
        c
        for c in pl.bank.records("constraint")
        if (c.get("attrs") or {}).get("topic") == "Объёмы и скорость"
    ]
    return {
        "e2e_green": e2e,
        "tests_green": green,
        "load_ok": e2e if perf else True,  # нагрузку отдельно не гоняем: считаем по сквозным тестам
        "launched": bool(results.get("launch")),
    }


# ---------------------------------------------------------------------- 5.3 Итог итерации


def render_docs(pl: Pipeline) -> str:
    bank = pl.bank
    out = ["# Документация", ""]
    areas = bank.records("area")
    if areas:
        out += [areas[0]["body"] or areas[0]["title"], ""]

    def section(
        title, kind, fmt=lambda c: f"- {c['title']}" + (f" — {c['body']}" if c["body"] else "")
    ):
        items = [c for c in bank.records(kind) if c["status"] == ACCEPTED]
        if items:
            out.extend([f"## {title}", ""] + [fmt(c) for c in items] + [""])

    section("Границы системы", "boundary")
    section("Ограничения", "constraint")
    section("Проблемы", "problem")
    section("Функциональные требования", "requirement")
    section(
        "Словарь", "term", lambda c: f"- **{c['title']}** ({c['attrs'].get('type')}) — {c['body']}"
    )
    for scenario in [c for c in bank.records("scenario") if c["status"] == ACCEPTED]:
        out += [f"## Сценарий: {scenario['title']}", "", scenario["body"], ""]
        for n, step in enumerate((scenario["attrs"].get("diagram") or {}).get("steps") or [], 1):
            out.append(f"{n}. {step['from']} → {step['to']}: {step['text']}")
        out.append("")
        for crit in [c for c in bank.children(scenario["id"]) if c["kind"] == "criterion"]:
            out.append(f"- Критерий: {crit['body'] or crit['title']}")
        out.append("")
    tables = [c for c in bank.records("table") if c["status"] == ACCEPTED]
    if tables:
        out += ["## Схема БД", ""]
        for t in tables:
            cols = ", ".join(f"{c['name']}:{c['type']}" for c in t["attrs"].get("columns") or [])
            out.append(f"- **{t['title']}**({cols})")
        out.append("")
    section("Страницы", "page")
    section(
        "Контракт API",
        "call",
        lambda c: f"- {c['title']}: {c['attrs'].get('method')} {c['attrs'].get('path')}",
    )
    section("Эндпоинты", "endpoint")
    return "\n".join(out).rstrip() + "\n"


def finish(pl: Pipeline) -> dict:
    """5.3 Итог итерации: Документация.md, отчёт, номер итерации и точка отката."""
    last = pl.keeper.last("final")
    if not last or last["state"] != "passed":
        raise PipelineError("final_first", "Итог собирается после итоговой приёмки")
    number = len(pl.bank.records("release")) + 1
    docs = pl.project / DOCS
    docs.write_text(render_docs(pl), encoding="utf-8")
    report = pl.project / REPORTS / f"итерация-{number}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Итерация {number}", "", "## Ворота", ""]
    for run in pl.keeper.runs:
        if run["state"] == "passed":
            below = "; принято ниже порога" if run.get("below") else ""
            lines.append(
                f"- {run['gate']}: принято (круг {run['round']}, решил: {run.get('by')}){below}"
            )
    marked = [c for c in pl.bank.records() if (c.get("attrs") or {}).get("below_threshold")]
    if marked:
        lines += ["", "## Принято ниже порога", ""] + [f"- {c['title']}" for c in marked]
    lines += ["", "## Запросы на изменение", ""]
    lines += [f"- {r['id']} {r['status']}: {r['text']}" for r in pl.changes.items] or ["- не было"]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    release = pl.bank.add(
        "release", f"Итерация {number}", body=f"Документация: {DOCS}; отчёт: {report.name}",
        attrs={"iteration": number, "docs": DOCS, "report": str(report.relative_to(pl.project))},
        author="studio", status=ACCEPTED,
    )  # fmt: skip
    stage3._checkpoint(pl, f"итерация {number}: итоговая приёмка пройдена")
    return {"iteration": number, "docs": DOCS, "release": release["id"]}


def raise_findings(pl: Pipeline, run: dict) -> list[dict]:
    """Замечания итоговых ворот — записями «замечание»: правит этап-владелец через запрос."""
    out = []
    for item in run.get("code") or []:
        out.append(pl.bank.add("finding", item["message"][:200], author="studio"))
    for item in run.get("failing") or []:
        out.append(pl.bank.add("finding", f"{item['title']}: {item['why']}", author="evaluator"))
    return out


# ---------------------------------------------------------------------- шаги


def steps(pl: Pipeline) -> list[dict]:
    entry = entry_ok(pl)
    results = stage3.results(pl)
    gate = pl.keeper.last("final")
    ok = bool(gate and gate["state"] == "passed")
    e2e = [t for t in pl.bank.records("test") if (t.get("attrs") or {}).get("layer") == "e2e"]
    e2e_ran = bool((results.get("e2e") or {}).get("ran"))  # красные сквозные тесты — дело ворот
    launched = bool(results.get("launch"))
    return [
        _step(
            "5.1",
            "launch",
            "Запуск",
            launched,
            entry,
            "assistant",
            "демо-данные и запуск одной кнопкой",
        ),
        _step(
            "5.2", "e2e", "Сквозные сценарии", bool(e2e) and e2e_ran, launched, "test_author", ""
        ),
        _step(
            "gate",
            "gate_final",
            "Итоговая приёмка",
            ok,
            launched and bool(e2e) and e2e_ran,
            "evaluator",
            "",
        ),
        _step(
            "5.3",
            "finish",
            "Итог итерации",
            bool(pl.bank.records("release")) and ok,
            ok,
            None,
            "собирает код",
        ),
    ]


def run_step(pl: Pipeline, key: str):
    if key == "launch":
        return launch(pl)
    if key == "e2e":
        created = write_e2e(pl)
        run = run_e2e(pl)
        return {"created": created, **run}
    if key == "gate_final":
        run = pl.run_gate("final", facts(pl))
        if run["state"] == "returned":
            raise_findings(pl, run)
        return run
    if key == "finish":
        return finish(pl)
    raise PipelineError("unknown_step", f"Шаг {key!r} агентом не выполняется")


__all__ = ["Launcher", "steps", "run_step", "_items", "_s"]
