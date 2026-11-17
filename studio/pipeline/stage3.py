"""Этап 3 «Frontend на моках»: стиль и каркас, план интерфейса, моки и контракт, ТЗ из банка,
тесты из критериев (до реализации, другой агент), реализация исполнителем, ворота.

Порядок защиты от «работает, но не то»: тесты пишет автор тестов, который кода не видит; до
реализации они обязаны падать («красный старт»); исполнитель тесты не правит — после его запуска
хэши проверяются, а изменённый тест возвращается в прежний вид.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from studio.pipeline.graph import ACCEPTED

from . import prompts, tz
from .coder import file_hashes, safe_test_path
from .engine import Pipeline
from .errors import PipelineError
from .stage1 import _ids, _items, _s, _step, _title

THEMES = {"light": "Светлая", "dark": "Тёмная", "business": "Деловая"}
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
RESULTS = Path(".studio") / "tz" / "results.json"
TESTS_COPY = Path(".studio") / "tz" / "tests"
MAX_FIX_ROUNDS = 2


def entry_ok(pl: Pipeline) -> bool:
    last = pl.keeper.last("schema")
    return bool(last and last["state"] == "passed")


def _need_entry(pl: Pipeline) -> None:
    if not entry_ok(pl):
        raise PipelineError(
            "stage2_first", "Этап 3 берёт только принятое: сначала пройдите «Приёмку схемы БД»"
        )


# ---------------------------------------------------------------------- результаты тестов


def results(pl: Pipeline) -> dict:
    try:
        return json.loads((pl.project / RESULTS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_result(pl: Pipeline, layer: str, run) -> None:
    data = results(pl)
    data[layer] = {
        "ran": run.ran,
        "passed": run.passed,
        "failed": run.failed,
        "output": run.output[-1500:],
    }
    path = pl.project / RESULTS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def tests_green(pl: Pipeline, layer: str):
    item = results(pl).get(layer)
    if not item or not item.get("ran"):
        return None
    return bool(item.get("passed"))


# ---------------------------------------------------------------------- 3.1 Стиль и каркас


def build_style(pl: Pipeline, theme: str | None = None) -> dict:
    """3.1 Стиль и каркас: один раз. Тему можно задать готовой (без агента) или попросить дизайнера."""
    _need_entry(pl)
    if pl.bank.records("style"):
        raise PipelineError("style_exists", "Стиль уже задан: меняйте его запросом на изменение")
    attrs = {"theme": theme if theme in THEMES else "light", "navigation": [], "notes": ""}
    if theme in THEMES:
        title = f"Стиль: {THEMES[theme]}"
    else:
        scenarios = prompts.render(pl.bank, "scenario", status=(ACCEPTED,))
        prompt = (
            f"Границы, ограничения, словарь:\n{prompts.constitution(pl.bank)}\n\nСценарии:\n{scenarios}\n\n"
            "Предложи стиль и каркас интерфейса: тему (light, dark или business), меню по ролям, "
            "общий каркас страниц.\n"
            'Ответ: {"theme": "light", "navigation": [{"role": "Пациент", "items": ["Запись"]}], "notes": "..."}'
        )
        data = pl.llm.ask_json("ui_designer", prompts.SYSTEM["ui_designer"], prompt)
        if isinstance(data, dict):
            chosen = _s(data.get("theme"), 12).lower()
            attrs["theme"] = chosen if chosen in THEMES else "light"
            attrs["navigation"] = [
                i for i in (data.get("navigation") or []) if isinstance(i, dict)
            ][:12]
            attrs["notes"] = _s(data.get("notes"), 600)
        title = f"Стиль: {THEMES[attrs['theme']]}"
    return pl.bank.add("style", title, attrs=attrs, author="ui_designer")


# ---------------------------------------------------------------------- 3.2 План интерфейса


def _scenarios(pl: Pipeline) -> list[dict]:
    return pl.bank.records("scenario", status=ACCEPTED)


def plan_interface(pl: Pipeline) -> dict:
    """3.2 План интерфейса: страницы по сценариям; вызовы — из шагов с меткой call."""
    _need_entry(pl)
    todo = [
        s for s in _scenarios(pl) if not any(c["kind"] == "page" for c in pl.bank.children(s["id"]))
    ]
    if not todo:
        return {"created": [], "skipped": []}
    blocks = []
    for s in todo:
        steps = "\n".join(
            f"   {st['from']} → {st['to']}: {st['text']} [{st.get('tag') or '-'}]"
            for st in (s.get("attrs") or {}).get("diagram", {}).get("steps", [])
        )
        blocks.append(f"{s['id']}: {s['title']}\n{steps}")
    prompt = (
        f"Конституция:\n{prompts.constitution(pl.bank)}\n\nСценарии (метки: screen — экран, call — "
        "вызов системы):\n" + "\n".join(blocks) + "\n\n"
        "Спроектируй страницы интерфейса: каждая — для одного или нескольких сценариев, с блоками "
        "и вызовами API (из шагов с меткой call). Каждый сценарий должен получить хотя бы одну "
        "страницу.\n"
        'Ответ: {"pages": [{"title": "Запись", "route": "/appointment", "scenarios": ["c20"], '
        '"description": "...", "calls": [{"title": "создать запись"}]}]}'
    )
    data = pl.llm.ask_json("ui_designer", prompts.SYSTEM["ui_designer"], prompt)
    created, skipped = [], []
    valid = {s["id"] for s in todo}
    for item in _items(data, "pages"):
        title = _s(item.get("title"), 120)
        parents = [i for i in _ids(pl, item.get("scenarios"), ("scenario",)) if i in valid]
        if not title or pl.bank.find("page", title):
            continue
        if not parents:
            skipped.append(f"«{title}»: не указан принятый сценарий")
            continue
        page = pl.bank.add(
            "page",
            title,
            parents=parents,
            body=_s(item.get("description"), 500),
            attrs={"route": _s(item.get("route"), 80)},
            author="ui_designer",
        )
        created.append(page)
        for call in _items(item, "calls"):
            ctitle = _s(call.get("title"), 120)
            if not ctitle:
                continue
            existing = pl.bank.find("call", ctitle)
            if existing:
                pl.bank.link(existing["id"], page["id"], "ui_designer")
            else:
                pl.bank.add("call", ctitle, parents=[page["id"]], author="ui_designer")
    return {"created": created, "skipped": skipped}


# ---------------------------------------------------------------------- 3.3 Моки и контракт


def write_contract(pl: Pipeline) -> list[dict]:
    """3.3 Моки и контракт: форма запроса, ответа и мок каждого вызова (версия контракта 1)."""
    _need_entry(pl)
    todo = [c for c in pl.bank.records("call") if not (c.get("attrs") or {}).get("method")]
    if not todo:
        return []
    listing = "\n".join(f"{c['id']}: {c['title']}" for c in todo)
    tables = prompts.render(pl.bank, "table")
    prompt = (
        f"Таблицы БД:\n{tables}\n\nВызовы без контракта:\n{listing}\n\n"
        "Для каждого вызова опиши контракт API: метод, путь, форму запроса и ответа (JSON) и мок — "
        "пример ответа, на котором интерфейс работает до появления бэкенда.\n"
        'Ответ: {"calls": [{"id": "c30", "method": "POST", "path": "/api/appointments", '
        '"request": {"doctor_id": 1}, "response": {"id": 1}, "mock": {"id": 1}}]}'
    )
    data = pl.llm.ask_json("ui_designer", prompts.SYSTEM["ui_designer"], prompt)
    done = []
    valid = {c["id"] for c in todo}
    for item in _items(data, "calls"):
        cid = _s(item.get("id"), 20)
        method = _s(item.get("method"), 8).upper()
        path = _s(item.get("path"), 160)
        if cid not in valid or method not in METHODS or not path.startswith("/"):
            continue
        pl.bank.mark(
            cid,
            "ui_designer",
            method=method,
            path=path,
            request=item.get("request") or {},
            response=item.get("response") or {},
            mock=item.get("mock") or item.get("response") or {},
            contract_version=1,
        )
        done.append(pl.bank.get(cid))
    return done


# ---------------------------------------------------------------------- 3.4 ТЗ из банка


def build_tz(pl: Pipeline) -> list[dict]:
    _need_entry(pl)
    if not pl.bank.records("page"):
        raise PipelineError("no_pages", "Сначала спланируйте интерфейс (шаг 3.2)")
    return tz.build(pl, 3)


# ---------------------------------------------------------------------- 3.5 Тесты


def _criteria(pl: Pipeline, scenario: dict, layer: str) -> list[dict]:
    out = {}
    cards = list(pl.bank.children(scenario["id"]))
    for req in pl.bank.parents(scenario["id"]):
        if req["kind"] == "requirement":
            cards += pl.bank.children(req["id"])
    for c in cards:
        a = c.get("attrs") or {}
        if (
            c["kind"] == "criterion"
            and a.get("check") != "visual"
            and (a.get("layer") or "frontend") == layer
        ):
            out[c["id"]] = c
    return list(out.values())


def _covered(pl: Pipeline) -> set[str]:
    return {p["id"] for t in pl.bank.records("test") for p in pl.bank.parents(t["id"])}


def write_tests_for(pl: Pipeline, stage: int, layer: str, scenarios: list[dict]) -> list[dict]:
    """Автор тестов пишет файлы тестов по критериям сценариев; кода он не видит (только ТЗ)."""
    created = []
    for scenario in scenarios:
        criteria = [c for c in _criteria(pl, scenario, layer) if c["id"] not in _covered(pl)]
        if criteria:
            created += _write_tests(pl, layer, criteria, tz.read(pl, stage, scenario["id"]))
    return created


def _write_tests(pl: Pipeline, layer: str, criteria: list[dict], context: str) -> list[dict]:
    created = []
    listing = "\n".join(f"{c['id']}: {c['body'] or c['title']}" for c in criteria)
    kind = "Vitest (TypeScript)" if layer == "frontend" else "pytest (Python)"
    base = {"frontend": "frontend/tests/pipeline/", "e2e": "tests/pipeline/e2e/"}.get(
        layer, "tests/pipeline/"
    )
    ext = ".test.ts" if layer == "frontend" else ".py"
    prompt = (
        f"ТЗ:\n{context}\n\nКритерии, для которых нужны тесты:\n{listing}\n\n"
        f"Напиши тесты ({kind}) по одному на критерий. Кода реализации ты не видишь и не "
        f"выдумываешь: опирайся только на ТЗ и контракт. Файлы — в {base}. Тест должен падать, "
        "пока функции нет.\n"
        'Ответ: {"tests": [{"criterion": "c40", "title": "...", "file": "'
        + base
        + "x"
        + ext
        + '", "code": "..."}]}'
    )
    data = pl.llm.ask_json("test_author", prompts.SYSTEM["test_author"], prompt)
    valid = {c["id"] for c in criteria}
    for item in _items(data, "tests"):
        cid = _s(item.get("criterion"), 20)
        code = str(item.get("code") or "")
        if cid not in valid or not code.strip():
            continue
        try:
            path = safe_test_path(pl.project, _s(item.get("file"), 200), layer)
        except PipelineError:
            continue  # тест вне своего каталога не пишем: через «тест» код не меняют
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        rel = str(path.relative_to(pl.project)).replace("\\", "/")
        copy = pl.project / TESTS_COPY / rel
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(code, encoding="utf-8")  # эталон: по нему вернём изменённый тест
        created.append(
            pl.bank.add(
                "test",
                _s(item.get("title"), 200) or rel,
                parents=[cid],
                attrs={
                    "file": rel,
                    "layer": layer,
                    "hash": hashlib.sha256(code.encode("utf-8")).hexdigest(),
                    "red": None,
                },
                author="test_author",
            )
        )
    return created


def write_tests(pl: Pipeline) -> list[dict]:
    _need_entry(pl)
    if not tz.stale(pl, 3) and not (pl.project / tz.DIR / "stage3").is_dir():
        raise PipelineError("no_tz", "Сначала соберите ТЗ (шаг 3.4)")
    return write_tests_for(pl, 3, "frontend", _scenarios(pl))


def red_start_for(pl: Pipeline, layer: str) -> dict:
    """Красный старт: до реализации тесты обязаны падать, иначе они пустые."""
    tests = [t for t in pl.bank.records("test") if (t.get("attrs") or {}).get("layer") == layer]
    if not tests:
        raise PipelineError("no_tests", "Сначала напишите тесты")
    run = pl.tests.run(layer)
    save_result(pl, layer, run)
    if not run.ran:
        return {"checked": False, "note": run.output}
    if run.passed:
        for t in tests:
            pl.bank.mark(t["id"], "studio", red=False)
        raise PipelineError(
            "tests_empty",
            "Тесты прошли без реализации — значит, они пустые. Автор тестов должен переписать их",
        )
    failed = set(run.failed)
    for t in tests:
        red = (not failed) or (t["attrs"].get("file") in failed)
        pl.bank.mark(t["id"], "studio", red=bool(red))
    return {
        "checked": True,
        "red": len([t for t in tests if pl.bank.get(t["id"])["attrs"].get("red")]),
    }


def red_start(pl: Pipeline) -> dict:
    _need_entry(pl)
    return red_start_for(pl, "frontend")


# ---------------------------------------------------------------------- 3.6 Реализация


def implement_for(
    pl: Pipeline, stage: int, layer: str, scenarios: list[dict] | None = None
) -> dict:
    """Исполнитель пишет код по ТЗ сценария; тесты не трогает; до двух кругов исправлений."""
    scenarios = scenarios or _scenarios(pl)
    if not scenarios:
        raise PipelineError("no_scenarios", "Нет принятых сценариев")
    tests = [t for t in pl.bank.records("test") if (t.get("attrs") or {}).get("layer") == layer]
    files = [t["attrs"]["file"] for t in tests if t.get("attrs", {}).get("file")]
    before = file_hashes(pl.project, files)
    log, summary = [], ""
    for scenario in scenarios:
        task = tz.read(pl, stage, scenario["id"])
        for _round in range(MAX_FIX_ROUNDS + 1):
            result = pl.coder.run("executor", task, pl.project)
            if not result.ok:
                raise PipelineError("coder_failed", result.error or "Исполнитель не справился")
            summary = result.summary
            _restore_tests(pl, files, before)
            run = pl.tests.run(layer)
            save_result(pl, layer, run)
            if not run.ran or run.passed:
                break
            task = (
                task
                + "\n\n## Тесты ещё падают, исправь код (тесты не менять)\n"
                + run.output[-2000:]
            )
        log.append({"scenario": scenario["id"], "green": bool(run.passed) if run.ran else None})
    _checkpoint(pl, f"этап {stage}: реализация по ТЗ")
    return {"scenarios": log, "summary": summary, "green": tests_green(pl, layer)}


def _restore_tests(pl: Pipeline, files: list[str], before: dict[str, str]) -> list[str]:
    """Тесты читает только исполнитель, но не меняет: изменённый тест возвращаем в прежний вид."""
    restored = []
    now = file_hashes(pl.project, files)
    for name, digest in before.items():
        if now.get(name) != digest:
            source = pl.project / TESTS_COPY / name
            if source.is_file():
                (pl.project / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
                restored.append(name)
    return restored


def _checkpoint(pl: Pipeline, message: str) -> None:
    from studio.server import gitops

    try:
        if gitops.is_repo(pl.project):
            gitops.checkpoint(pl.project, message)
    except Exception:  # noqa: BLE001 — точка отката не должна ронять шаг
        return


def implement(pl: Pipeline) -> dict:
    _need_entry(pl)
    return implement_for(pl, 3, "frontend")


# ---------------------------------------------------------------------- ворота, шаги


def facts(pl: Pipeline) -> dict:
    return {"tests_green": tests_green(pl, "frontend")}


def steps(pl: Pipeline) -> list[dict]:
    entry = entry_ok(pl)
    has = lambda kind: bool(pl.bank.records(kind))  # noqa: E731
    gate = pl.keeper.last("frontend")
    ok = bool(gate and gate["state"] == "passed")
    tests = pl.bank.records("test")
    front = [t for t in tests if (t.get("attrs") or {}).get("layer") == "frontend"]
    red = bool(front) and all(t["attrs"].get("red") is not None for t in front)
    contract = bool(pl.bank.records("call")) and all(
        (c.get("attrs") or {}).get("method") for c in pl.bank.records("call")
    )
    green = tests_green(pl, "frontend") is True
    return [
        _step(
            "3.1",
            "style",
            "Стиль и каркас",
            has("style"),
            entry,
            "ui_designer",
            "выберите тему или поручите дизайнеру",
        ),
        _step(
            "3.2", "plan", "План интерфейса", has("page"), entry and has("style"), "ui_designer", ""
        ),
        _step("3.3", "contract", "Моки и контракт", contract, has("page"), "ui_designer", ""),
        _step(
            "3.4",
            "tz",
            "ТЗ из банка",
            (pl.project / tz.DIR / "stage3").is_dir() and not tz.stale(pl, 3),
            contract,
            None,
            "собирает код",
        ),
        _step(
            "3.5",
            "tests",
            "Тесты из критериев",
            bool(front) and red,
            (pl.project / tz.DIR / "stage3").is_dir(),
            "test_author",
            "",
        ),
        _step("3.6", "implement", "Реализация", green, bool(front) and red, "executor", ""),
        _step(
            "gate",
            "gate_frontend",
            "Приёмка интерфейса",
            ok,
            green and bool(front),
            "evaluator",
            "",
        ),
    ]


def run_step(pl: Pipeline, key: str):
    if key == "style":
        return build_style(pl, None)
    if key == "plan":
        return plan_interface(pl)
    if key == "contract":
        return write_contract(pl)
    if key == "tz":
        return build_tz(pl)
    if key == "tests":
        created = write_tests(pl)
        if created:
            red_start(pl)
        return created
    if key == "implement":
        return implement(pl)
    if key == "gate_frontend":
        return pl.run_gate("frontend", facts(pl))
    raise PipelineError("unknown_step", f"Шаг {key!r} агентом не выполняется")


__all__ = ["THEMES", "build_style", "plan_interface", "steps", "run_step", "_title"]
