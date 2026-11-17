"""Этап 4 «Backend»: план бэкенда, модели из схемы БД (генерирует код), ТЗ из банка, тесты,
реализация, замена моков на реальный API, ворота.

Контракт API, принятый на этапе 3, бэкенд сам не меняет: нужно изменить — запрос на изменение.
"""

from __future__ import annotations

import re
from pathlib import Path

from studio.pipeline.graph import ACCEPTED

from . import prompts, stage3, tz, waves
from .engine import Pipeline
from .errors import PipelineError
from .stage1 import _ids, _items, _s, _step

MODELS_FILE = "models_generated.py"
SQL_TYPES = {
    "int": "int", "text": "str", "bool": "bool", "float": "float", "date": "date",
    "datetime": "datetime", "json": "dict", "uuid": "UUID",
}  # fmt: skip


def entry_ok(pl: Pipeline) -> bool:
    last = pl.keeper.last("frontend")
    return bool(last and last["state"] == "passed")


def _need_entry(pl: Pipeline) -> None:
    if not entry_ok(pl):
        raise PipelineError(
            "stage3_first", "Этап 4 берёт только принятое: сначала пройдите «Приёмку интерфейса»"
        )


# ---------------------------------------------------------------------- 4.1 План бэкенда


def plan_backend(pl: Pipeline) -> dict:
    """4.1 План бэкенда: эндпоинт на каждый вызов контракта."""
    _need_entry(pl)
    todo = [
        c
        for c in waves.records(pl, "call", status=ACCEPTED)
        if not any(e["kind"] == "endpoint" for e in pl.bank.children(c["id"]))
    ]
    if not todo:
        return {"created": [], "skipped": []}
    listing = "\n".join(
        f"{c['id']}: {c['title']} — {(c.get('attrs') or {}).get('method')} {(c.get('attrs') or {}).get('path')}"
        for c in todo
    )
    prompt = (
        f"Конституция:\n{prompts.constitution(pl.bank)}\n\nТаблицы БД:\n{prompts.render(pl.bank, 'table')}\n\n"
        f"Вызовы контракта, для которых нужны эндпоинты:\n{listing}\n\n"
        "Спланируй бэкенд: эндпоинт на каждый вызов (метод и путь — строго по контракту), краткое "
        "описание бизнес-сценария и таблицы, с которыми он работает.\n"
        'Ответ: {"endpoints": [{"call": "c30", "summary": "...", "tables": ["appointment"]}]}'
    )
    data = pl.llm.ask_json("backend_designer", prompts.SYSTEM["backend_designer"], prompt)
    created, skipped = [], []
    valid = {c["id"]: c for c in todo}
    for item in _items(data, "endpoints"):
        ids = [i for i in _ids(pl, item.get("call"), ("call",)) if i in valid]
        if not ids:
            skipped.append(f"«{_s(item.get('summary'), 80)}»: не указан вызов контракта")
            continue
        call = valid[ids[0]]
        a = call.get("attrs") or {}
        created.append(
            pl.bank.add(
                "endpoint",
                f"{a.get('method')} {a.get('path')}",
                parents=[call["id"]],
                body=_s(item.get("summary"), 500),
                attrs={
                    "method": a.get("method"),
                    "path": a.get("path"),
                    "tables": [_s(t, 60) for t in (item.get("tables") or [])][:10],
                },
                author="backend_designer",
            )
        )
    return {"created": created, "skipped": skipped}


# ---------------------------------------------------------------------- 4.2 Модели


def _pascal(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[_\W]+", name) if part) or "Model"


def render_models(tables: list[dict]) -> str:
    """Модели SQLModel из схемы БД — детерминированно, без агента."""
    lines = [
        '"""Модели БД. Сгенерировано студией из схемы БД банка знаний — не править руками."""',
        "",
        "from __future__ import annotations",
        "",
        "from datetime import date, datetime",
        "from uuid import UUID",
        "",
        "from sqlalchemy import JSON, Column",
        "from sqlmodel import Field, SQLModel",
        "",
    ]
    for table in tables:
        name = (table.get("attrs") or {}).get("name") or table["title"]
        lines += [
            "",
            f"class {_pascal(name)}(SQLModel, table=True):",
            f'    __tablename__ = "{name}"',
            "",
        ]
        for col in (table.get("attrs") or {}).get("columns") or []:
            py = SQL_TYPES.get(col["type"], "str")
            nullable = col.get("null") or col["key"] == "pk"
            ann = f"{py} | None" if nullable else py
            if col["key"] == "pk":
                default = "Field(default=None, primary_key=True)"
            elif col["key"] == "fk":
                default = f'Field(foreign_key="{col["ref"]}"' + (
                    ", default=None)" if nullable else ")"
                )
            elif col["type"] == "json":
                default = (
                    "Field(default=None, sa_column=Column(JSON))"
                    if nullable
                    else "Field(sa_column=Column(JSON))"
                )
            else:
                default = "Field(default=None)" if nullable else "Field()"
            lines.append(f"    {col['name']}: {ann} = {default}")
        if not (table.get("attrs") or {}).get("columns"):
            lines.append("    pass")
    return "\n".join(lines) + "\n"


def build_models(pl: Pipeline) -> list[dict]:
    """4.2 Модели из схемы БД: файл пишет код; записи «модель» привязаны к таблицам."""
    _need_entry(pl)
    tables = waves.records(pl, "table", status=ACCEPTED)
    if not tables:
        raise PipelineError("no_tables", "В банке нет принятых таблиц")
    path = pl.project / MODELS_FILE
    path.write_text(render_models(tables), encoding="utf-8")
    out = []
    for table in tables:
        if any(c["kind"] == "model" for c in pl.bank.children(table["id"])):
            continue
        name = (table.get("attrs") or {}).get("name") or table["title"]
        out.append(
            pl.bank.add(
                "model",
                _pascal(name),
                parents=[table["id"]],
                body=f"таблица {name}",
                attrs={"file": MODELS_FILE},
                author="studio",
            )
        )
    return out


# ---------------------------------------------------------------------- 4.3–4.5


def build_tz(pl: Pipeline) -> list[dict]:
    _need_entry(pl)
    if not waves.records(pl, "endpoint"):
        raise PipelineError("no_endpoints", "Сначала спланируйте бэкенд (шаг 4.1)")
    return tz.build(pl, 4)


def write_tests(pl: Pipeline) -> list[dict]:
    _need_entry(pl)
    scenarios = waves.records(pl, "scenario", status=ACCEPTED)
    created = stage3.write_tests_for(pl, 4, "backend", scenarios)
    # критерии ограничений (права, безопасность, объём) не принадлежат сценарию — отдельный блок
    covered = stage3._covered(pl)
    limits = [
        c
        for c in waves.records(pl, "criterion")
        if (c.get("attrs") or {}).get("layer") == "backend"
        and (c.get("attrs") or {}).get("check") != "visual"
        and c["id"] not in covered
        and any(p["kind"] == "constraint" for p in pl.bank.parents(c["id"]))
    ]
    if limits:
        context = "Ограничения проекта:\n" + prompts.constitution(pl.bank)
        created += stage3._write_tests(pl, "backend", limits, context)
    return created


def implement(pl: Pipeline) -> dict:
    _need_entry(pl)
    return stage3.implement_for(pl, 4, "backend")


# ---------------------------------------------------------------------- 4.6 Замена моков


def mocks_in_use(project: Path) -> list[str]:
    """Файлы интерфейса (не тесты и не сами моки), которые всё ещё берут данные из моков."""
    src = Path(project) / "frontend" / "src"
    if not src.is_dir():
        return []
    found = []
    for path in src.rglob("*"):
        if path.suffix not in (".ts", ".js", ".vue") or "mocks" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(r"from\s+['\"][^'\"]*mocks[/'\"]|import\(['\"][^'\"]*mocks", text):
            found.append(str(path.relative_to(project)).replace("\\", "/"))
    return found


def replace_mocks(pl: Pipeline) -> dict:
    """4.6 Замена моков: фронтенд переходит на реальные вызовы по контракту; тесты — снова зелёные."""
    _need_entry(pl)
    calls = waves.records(pl, "call", status=ACCEPTED)
    contract = "\n".join(
        f"- {c['title']}: {(c.get('attrs') or {}).get('method')} {(c.get('attrs') or {}).get('path')}"
        for c in calls
    )
    task = (
        "# Замена моков на реальный API\n\nПерейди во frontend/ с моков на реальные вызовы бэкенда "
        "строго по контракту; моки из рабочего кода убери (сами файлы моков можно оставить для тестов). "
        "Тесты в frontend/tests/pipeline не меняй.\n\nКонтракт:\n" + contract
    )
    result = pl.coder.run("executor", task, pl.project)
    if not result.ok:
        raise PipelineError("coder_failed", result.error or "Исполнитель не справился")
    run = pl.tests.run("frontend")
    stage3.save_result(pl, "frontend", run)
    stage3._checkpoint(pl, "этап 4: замена моков на реальный API")
    return {"mocks": mocks_in_use(pl.project), "frontend_green": stage3.tests_green(pl, "frontend")}


# ---------------------------------------------------------------------- ворота, шаги


def facts(pl: Pipeline) -> dict:
    calls = waves.records(pl, "call", status=ACCEPTED)
    matches = bool(calls) and all(
        any(e["kind"] == "endpoint" for e in pl.bank.children(c["id"])) for c in calls
    )
    done = stage3.results(pl).get("mocks_replaced")
    return {
        "tests_green": stage3.tests_green(pl, "backend"),
        **waves.facts(pl),
        "contract_matches": matches,
        "mocks_removed": True
        if (done and not mocks_in_use(pl.project))
        else (False if done else None),
    }


def steps(pl: Pipeline) -> list[dict]:
    entry = entry_ok(pl)
    has = lambda kind: bool(waves.records(pl, kind))  # noqa: E731
    gate = pl.keeper.last("backend")
    ok = bool(gate and gate["state"] == "passed")
    back = [t for t in waves.records(pl, "test") if (t.get("attrs") or {}).get("layer") == "backend"]
    red = bool(back) and all(t["attrs"].get("red") is not None for t in back)
    green = stage3.tests_green(pl, "backend") is True
    tz_done = tz.built(pl, 4)
    replaced = bool(stage3.results(pl).get("mocks_replaced"))
    return [
        _step("4.1", "plan", "План бэкенда", has("endpoint"), entry, "backend_designer", ""),
        _step(
            "4.2",
            "models",
            "Модели из схемы БД",
            has("model"),
            has("endpoint"),
            None,
            "генерирует код, не агент",
        ),
        _step("4.3", "tz", "ТЗ из банка", tz_done, has("model"), None, "собирает код"),
        _step("4.4", "tests", "Тесты из критериев", bool(back) and red, tz_done, "test_author", ""),
        _step("4.5", "implement", "Реализация", green, bool(back) and red, "executor", ""),
        _step("4.6", "mocks", "Замена моков", replaced, green, "executor", ""),
        _step("gate", "gate_backend", "Приёмка бэкенда", ok, replaced, "evaluator", ""),
    ]


def run_step(pl: Pipeline, key: str):
    if key == "plan":
        return plan_backend(pl)
    if key == "models":
        return build_models(pl)
    if key == "tz":
        return build_tz(pl)
    if key == "tests":
        created = write_tests(pl)
        if created:
            stage3.red_start_for(pl, "backend")
        return created
    if key == "implement":
        return implement(pl)
    if key == "mocks":
        out = replace_mocks(pl)
        data = stage3.results(pl)
        data["mocks_replaced"] = True
        (pl.project / stage3.RESULTS).write_text(
            __import__("json").dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return out
    if key == "gate_backend":
        return pl.run_gate("backend", facts(pl))
    raise PipelineError("unknown_step", f"Шаг {key!r} агентом не выполняется")
