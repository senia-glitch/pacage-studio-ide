"""Этап 2 «Сценарии и схема БД»: словарь, варианты, сценарии, схемы БД по сценариям, слияние.

Вход этапа — только принятое из банка (идея и требования, прошедшие «Приёмку требований»).
Каждый шаг диаграммы сценария размечен: экран, вызов или данные — из меток рождаются план
интерфейса, контракт API и таблицы. Участники диаграмм — только имена из словаря.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from studio.pipeline.graph import ACCEPTED

from . import coverage as cov
from . import prompts
from .diagrams import normalize_columns, normalize_diagram, schema_problems, snake
from .engine import Pipeline
from .errors import PipelineError
from .stage1 import _ids, _items, _s, _title

TERM_TYPES = ("role", "component", "entity")


def _entry_ok(pl: Pipeline) -> bool:
    last = pl.keeper.last("requirements")
    return bool(last and last["state"] == "passed")


def _need_entry(pl: Pipeline) -> None:
    if not _entry_ok(pl):
        raise PipelineError(
            "stage1_first", "Этап 2 берёт только принятое: сначала пройдите «Приёмку требований»"
        )


def _requirements(pl: Pipeline) -> list[dict]:
    return pl.bank.records("requirement", status=ACCEPTED)


# ---------------------------------------------------------------------- 2.1 Роли и словарь


def build_dictionary(pl: Pipeline) -> list[dict]:
    """2.1 Роли и словарь: единый список имён (роли, компоненты, сущности) с источником."""
    from . import interview

    _need_entry(pl)
    reqs = _requirements(pl)
    prompt = (
        f"{interview.dossier(pl, design=True)}\n\n"
        "Составь единый словарь имён: роли (люди), компоненты (системы, в том числе внешние), "
        "сущности (термины предметной области). Только то, что есть в требованиях и диалоге с "
        "человеком: ролей, систем и понятий, о которых там не говорится, не добавляй. У каждого — тип, "
        "определение в одну фразу и коды записей, откуда оно взято. Уже есть в словаре — не повторяй.\n"
        'Ответ: {"terms": [{"name": "Пациент", "type": "role", "text": "...", "from": ["c5"]}]}'
    )
    data = pl.llm.ask_json("scenarist", prompts.SYSTEM["scenarist"], prompt)
    created = []
    for item in _items(data, "terms"):
        name, kind = _s(item.get("name"), 80), _s(item.get("type"), 12).lower()
        if not name or kind not in TERM_TYPES or pl.bank.find("term", name):
            continue
        parents = _ids(pl, item.get("from"), ("domain", "requirement", "boundary")) or (
            [reqs[0]["id"]] if reqs else []
        )
        if not parents:
            continue
        created.append(
            pl.bank.add(
                "term",
                name,
                parents=parents,
                body=_s(item.get("text"), 400),
                attrs={"type": kind},
                author="scenarist",
            )
        )
    return created


# ---------------------------------------------------------------------- 2.2 Варианты


def propose_variants(pl: Pipeline) -> list[dict]:
    """2.2 Варианты: только для требований с флагом «есть несколько способов»."""
    _need_entry(pl)
    todo = [
        r
        for r in _requirements(pl)
        if (r.get("attrs") or {}).get("options")
        and not any(c["kind"] == "variant" for c in pl.bank.children(r["id"], live=False))
    ]
    if not todo:
        return []
    from . import interview

    listing = "\n".join(f"{r['id']}: {r['title']}" for r in todo)
    prompt = (
        f"{interview.dossier(pl, design=True)}\n\n"
        f"Для каждого требования предложи 2–3 способа реализации с плюсами и минусами:\n{listing}\n"
        'Ответ: {"variants": [{"requirement": "c9", "title": "SMS", "body": "плюсы и минусы"}]}'
    )
    data = pl.llm.ask_json("scenarist", prompts.SYSTEM["scenarist"], prompt)
    valid = {r["id"] for r in todo}
    created = []
    for item in _items(data, "variants"):
        req, title = _s(item.get("requirement"), 20), _s(item.get("title"), 120)
        if req in valid and title and not pl.bank.find("variant", f"{req}: {title}"):
            created.append(
                pl.bank.add(
                    "variant",
                    title,
                    parents=[req],
                    body=_s(item.get("body"), 600),
                    attrs={"key": f"{req}: {title}"},
                    author="scenarist",
                )
            )
    return created


def choose_variant(pl: Pipeline, variant_id: str, by: str = "human") -> dict:
    """Выбрать вариант: остальные у этого требования уходят в архив (остаются в банке)."""
    card = pl.bank.get(variant_id)
    if card["kind"] != "variant":
        raise PipelineError("not_variant", "Выбрать можно только вариант решения")
    for parent in pl.bank.parents(variant_id):
        for sibling in pl.bank.children(parent["id"]):
            if sibling["kind"] == "variant" and sibling["id"] != variant_id:
                pl.bank.reject(sibling["id"], by, "выбран другой вариант")
    pl.bank.mark(variant_id, by, chosen=True, chosen_by=by)
    return pl.bank.get(variant_id)


def unchosen(pl: Pipeline) -> list[dict]:
    """Требования, у которых варианты предложены, а выбор не сделан."""
    out = []
    for req in pl.bank.records("requirement"):
        variants = [c for c in pl.bank.children(req["id"]) if c["kind"] == "variant"]
        if variants and not any((v.get("attrs") or {}).get("chosen") for v in variants):
            out.append(req)
    return out


def auto_choose(pl: Pipeline) -> list[dict]:
    """Автопилот: координатор сам выбирает вариант и объясняет выбор."""
    todo = unchosen(pl)
    if not todo:
        return []
    lines = []
    for req in todo:
        variants = [c for c in pl.bank.children(req["id"]) if c["kind"] == "variant"]
        lines.append(
            f"{req['id']}: {req['title']}\n"
            + "\n".join(f"  {v['id']}: {v['title']} — {v['body']}" for v in variants)
        )
    prompt = (
        f"Границы и ограничения:\n{prompts.constitution(pl.bank)}\n\nВыбери по одному варианту:\n"
        + "\n".join(lines)
        + '\nОтвет: {"choices": [{"requirement": "c9", "variant": "c12", "why": "..."}]}'
    )
    data = pl.llm.ask_json("coordinator", prompts.SYSTEM["coordinator"], prompt)
    done = []
    for item in _items(data, "choices"):
        req = _s(item.get("requirement"), 20)
        ids = _ids(pl, item.get("variant"), ("variant",))
        if not ids or req not in {r["id"] for r in todo}:
            continue
        if ids[0] not in {p["id"] for p in pl.bank.children(req)}:
            continue
        chosen = choose_variant(pl, ids[0], "coordinator")
        pl.bank.mark(chosen["id"], "coordinator", why=_s(item.get("why"), 300))
        done.append(pl.bank.get(chosen["id"]))
    return done


# ---------------------------------------------------------------------- 2.3 Сценарии


SCENARIO_GROUP = 3  # требований в одном вызове: группы пишутся параллельно


def _needs_scenarios(pl: Pipeline) -> list[dict]:
    """Требования без сценария или с критерием, который не проверяет ни один сценарий."""
    covered: set[str] = set()
    for scenario in pl.bank.records("scenario"):
        covered |= cov.scenario_covers(pl.bank, scenario)
    out = []
    for req in _requirements(pl):
        has = any(c["kind"] == "scenario" for c in pl.bank.children(req["id"]))
        open_criteria = [
            c
            for c in pl.bank.children(req["id"])
            if c["kind"] == "criterion" and not cov.visual(c) and c["id"] not in covered
        ]
        if not has or open_criteria:
            out.append(req)
    return out


def _requirement_block(pl: Pipeline, req: dict) -> str:
    text = f"{req['id']}: {req['title']}"
    if req.get("body") and req["body"] != req["title"]:
        text += f"\n    {req['body'][:400]}"
    for crit in pl.bank.children(req["id"]):
        if crit["kind"] == "criterion":
            text += f"\n    критерий {crit['id']}: {crit['title']}"
    return text


def write_scenarios(pl: Pipeline, workers: int = 3) -> dict:
    """2.3 Сценарии: строго из требований и их критериев; недостающие дописываются и при доработке.

    Требования идут группами и пишутся параллельно: быстрее, и каждый вызов видит весь банк (досье)."""
    from . import interview

    _need_entry(pl)
    if unchosen(pl):
        raise PipelineError(
            "choose_variants", "Сначала выберите вариант решения для каждого требования"
        )
    if not pl.bank.records("term"):
        raise PipelineError("no_dictionary", "Сначала составьте словарь (шаг 2.1)")
    todo = _needs_scenarios(pl)
    if not todo and pl.llm.feedback:
        todo = _requirements(pl)  # доработка по замечаниям ворот: автор сам видит, каких сценариев не хватает
    if not todo:
        return {"created": [], "skipped": []}
    chosen = "\n".join(
        f"{v['id']} (для {p['id']}): {v['title']}"
        for v in pl.bank.records("variant")
        if (v.get("attrs") or {}).get("chosen")
        for p in pl.bank.parents(v["id"])
    )
    context = interview.dossier(pl, design=True)
    groups = [todo[i : i + SCENARIO_GROUP] for i in range(0, len(todo), SCENARIO_GROUP)]

    def prompt_for(group: list[dict]) -> str:
        listing = "\n".join(_requirement_block(pl, r) for r in group)
        return (
            f"{context}\n\nВыбранные варианты:\n{chosen or '(нет)'}\n\n"
            f"Требования, которые нужно покрыть сценариями:\n{listing}\n\n"
            "Опиши сценарии строго из этих требований и их критериев: ничего, что из них и из диалога "
            "не следует (регистрация, оплата, роли, которых нет), не добавляй; уже написанные "
            "сценарии не повторяй, а дополни недостающими. Одна цель на сценарий: успешный ход — "
            "отдельный сценарий, каждый отказ или ошибка из критериев — отдельный сценарий со своим "
            "предусловием. Участники — ТОЛЬКО имена из словаря. У каждого шага диаграммы метка tag: "
            "screen (экран), call (вызов системы), data (данные). Каждое требование и каждый его "
            "критерий (код указан) должен войти хотя бы в один сценарий: коды критериев, которые "
            "сценарий проверяет, перечисли в covers. Если сценарий не работает с данными — "
            "no_data=true.\n"
            'Ответ: {"scenarios": [{"title": "...", "requirements": ["c9"], "covers": ["c10"], '
            '"variant": "c12", "no_data": false, "diagram": {"participants": ["Пациент", "Система"], '
            '"steps": [{"from": "Пациент", "to": "Система", "text": "выбирает время", "tag": "screen"}]}, '
            '"criteria": [{"text": "Когда ..., система ...", "check": "auto"}]}]}'
        )

    def one(group):
        try:
            return group, pl.llm.ask_json("scenarist", prompts.SYSTEM["scenarist"], prompt_for(group)), None
        except PipelineError as error:
            return group, None, error

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(one, groups))
    if all(error is not None for _, _, error in results):
        raise results[0][2]
    created, skipped = [], []
    for group, data, error in results:
        if error is not None:
            skipped.append(f"группа требований {', '.join(r['id'] for r in group)}: {error.message}")
            continue
        _apply_scenarios(pl, data, {r["id"] for r in group}, created, skipped)
    return {"created": created, "skipped": skipped}


def _apply_scenarios(pl: Pipeline, data, valid: set[str], created: list, skipped: list) -> None:
    for item in _items(data, "scenarios"):
        title = _s(item.get("title"), 200)
        if not title or pl.bank.find("scenario", title):
            continue
        reqs = [r for r in _ids(pl, item.get("requirements"), ("requirement",)) if r in valid]
        if not reqs:
            skipped.append(f"«{title}»: не указано принятое требование")
            continue
        variants = _ids(pl, item.get("variant"), ("variant",))
        diagram = normalize_diagram(item.get("diagram"))
        own = {
            c["id"]
            for r in reqs
            for c in pl.bank.children(r)
            if c["kind"] == "criterion"
        }
        covers = [c for c in _ids(pl, item.get("covers"), ("criterion",)) if c in own]
        scenario = pl.bank.add(
            "scenario",
            title,
            parents=reqs + variants,
            body=_s(item.get("goal"), 500),
            attrs={
                "diagram": diagram,
                "no_data": bool(item.get("no_data")),
                # без отметки сценарий считается покрывающим все критерии своих требований
                **({"covers": covers} if covers else {}),
            },
            author="scenarist",
        )
        created.append(scenario)
        for crit in _items(item, "criteria"):
            text = _s(crit.get("text"), 400)
            if text:
                check = "visual" if _s(crit.get("check"), 10) == "visual" else "auto"
                pl.bank.add(
                    "criterion",
                    _title(text),
                    parents=[scenario["id"]],
                    body=text,
                    attrs={"check": check, "layer": "frontend"},
                    author="scenarist",
                )


# ---------------------------------------------------------------------- 2.4 Схема БД сценария


def _scenario_prompt(pl: Pipeline, scenario: dict) -> str:
    diagram = (scenario.get("attrs") or {}).get("diagram") or {}
    steps = "\n".join(
        f"  {s['from']} → {s['to']}: {s['text']} [{s.get('tag') or '-'}]"
        for s in diagram.get("steps") or []
    )
    return (
        f"Словарь:\n{prompts.render(pl.bank, 'term')}\n\n"
        f"Сценарий {scenario['id']}: {scenario['title']}\n{scenario['body']}\n{steps}\n\n"
        "Спроектируй таблицы, нужные ЭТОМУ сценарию (шаги с меткой data). Имена таблиц и колонок "
        "— snake_case латиницей, у каждой таблицы первичный ключ (key: pk), внешние ключи с "
        "ref «таблица.колонка», типы: int, text, bool, float, date, datetime, json, uuid.\n"
        'Ответ: {"tables": [{"name": "appointment", "description": "...", "columns": '
        '[{"name": "id", "type": "int", "key": "pk"}, {"name": "doctor_id", "type": "int", '
        '"key": "fk", "ref": "doctor.id"}]}]}'
    )


def design_schemas(pl: Pipeline, workers: int = 3) -> dict:
    """2.4 Схема БД на каждый сценарий — параллельно, черновик лежит в самом сценарии."""
    _need_entry(pl)
    todo = [
        s
        for s in pl.bank.records("scenario")
        if not (s.get("attrs") or {}).get("no_data")
        and not (s.get("attrs") or {}).get("schema_draft")
    ]

    def one(scenario):
        data = pl.llm.ask_json(
            "db_designer", prompts.SYSTEM["db_designer"], _scenario_prompt(pl, scenario)
        )
        return scenario["id"], data

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(one, todo))
    done, bad = [], []
    for sid, data in results:
        tables = _tables(data)
        if not tables:
            bad.append(sid)
            continue
        pl.bank.mark(sid, "db_designer", schema_draft=tables)
        done.append(sid)
    return {"designed": done, "failed": bad}


def _tables(data) -> list[dict]:
    out = []
    for item in _items(data, "tables"):
        name = snake(item.get("name"))
        cols = normalize_columns(item.get("columns"))
        if name and cols:
            out.append(
                {"name": name, "description": _s(item.get("description"), 300), "columns": cols}
            )
    return out


# ---------------------------------------------------------------------- 2.5 Слияние


def _col(column: dict) -> str:
    return column["name"] + (":" + column["key"] if column["key"] else "")


def _merge_answer(pl: Pipeline, prompt: str) -> list[dict]:
    data = pl.llm.ask_json("db_designer", prompts.SYSTEM["db_designer"], prompt)
    merged = []
    for item in _items(data, "tables"):
        name, cols = snake(item.get("name")), normalize_columns(item.get("columns"))
        sources = _ids(pl, item.get("from"), ("scenario",))
        if name and cols:
            merged.append(
                {
                    "name": name,
                    "description": _s(item.get("description"), 300),
                    "columns": cols,
                    "from": sources,
                }
            )
    return merged


def merge_schemas(pl: Pipeline) -> list[dict]:
    """2.5 Слияние схем в одну общую: таблица происходит из тех сценариев, что её требовали."""
    _need_entry(pl)
    drafts = [s for s in pl.bank.records("scenario") if (s.get("attrs") or {}).get("schema_draft")]
    if not drafts:
        raise PipelineError("no_drafts", "Сначала спроектируйте схемы по сценариям (шаг 2.4)")
    blocks = []
    for s in drafts:
        tables = "; ".join(
            f"{t['name']}({', '.join(_col(c) for c in t['columns'])})"
            for t in s["attrs"]["schema_draft"]
        )
        blocks.append(f"{s['id']} {s['title']}: {tables}")
    prompt = (
        "Слей схемы сценариев в ОДНУ общую схему: одинаковые таблицы объедини, конфликты имён "
        "разреши, внешние ключи должны вести в существующие таблицы, у каждой таблицы — "
        "первичный ключ. Для каждой итоговой таблицы укажи from — коды сценариев, которым она "
        "нужна.\n\n" + "\n".join(blocks) + "\n"
        'Ответ: {"tables": [{"name": "doctor", "description": "...", "columns": [{"name": "id", '
        '"type": "int", "key": "pk"}], "from": ["c20", "c21"]}]}'
    )
    merged = _merge_answer(pl, prompt)
    problems = schema_problems(merged)
    if problems:  # один автоповтор: студия сама исправляет, прежде чем звать человека
        merged = _merge_answer(
            pl,
            prompt
            + "\n\nПрошлая попытка не годится: "
            + "; ".join(problems)
            + ". Исправь именно это и верни схему целиком.",
        )
        problems = schema_problems(merged)
    if problems:
        raise PipelineError(
            "bad_schema",
            "Общая схема не годится: " + "; ".join(problems) + ". Варианты: повторить шаг «Слияние "
            "схем» или поправить схемы сценариев (шаг 2.4, ↻).",
        )
    # прежние черновые таблицы (если слияние повторяют) уходят в архив
    for old in pl.bank.records("table", status="proposed"):
        pl.bank.reject(old["id"], "db_designer", "заменена новым слиянием")
    created = []
    for table in merged:
        parents = table["from"] or [d["id"] for d in drafts]
        created.append(
            pl.bank.add(
                "table",
                table["name"],
                parents=parents,
                body=table["description"],
                attrs={"name": table["name"], "columns": table["columns"]},
                author="db_designer",
            )
        )
    return created


def er(pl: Pipeline) -> dict:
    """Данные для ER-диаграммы: таблицы с колонками и связи по внешним ключам."""
    tables = pl.bank.records("table")
    names = {(t.get("attrs") or {}).get("name") or t["title"]: t for t in tables}
    links = []
    for table in tables:
        attrs = table.get("attrs") or {}
        for col in attrs.get("columns") or []:
            if col["key"] == "fk":
                target = col["ref"].partition(".")[0]
                if target in names:
                    links.append({"from": attrs.get("name"), "to": target, "column": col["name"]})
    return {
        "tables": [
            {
                "id": t["id"],
                "name": (t.get("attrs") or {}).get("name") or t["title"],
                "status": t["status"],
                "columns": (t.get("attrs") or {}).get("columns") or [],
            }
            for t in tables
        ],
        "links": links,
    }


# ---------------------------------------------------------------------- шаги и ход


def steps(pl: Pipeline) -> list[dict]:
    from .stage1 import _step

    has = lambda kind: bool(pl.bank.records(kind))  # noqa: E731
    entry = _entry_ok(pl)
    scen_gate, schema_gate = pl.keeper.last("scenarios"), pl.keeper.last("schema")
    scen_ok = bool(scen_gate and scen_gate["state"] == "passed")
    schema_ok = bool(schema_gate and schema_gate["state"] == "passed")
    has_draft = any((s.get("attrs") or {}).get("schema_draft") for s in pl.bank.records("scenario"))
    variants_needed = any(
        (r.get("attrs") or {}).get("options")
        for r in pl.bank.records("requirement", status=ACCEPTED)
    )
    return [
        _step("2.1", "dictionary", "Роли и словарь", has("term"), entry, "scenarist", ""),
        _step(
            "2.2",
            "variants",
            "Варианты",
            has("variant") or not variants_needed,
            entry and has("term"),
            "scenarist",
            "выберите вариант" if unchosen(pl) else "",
        ),
        _step(
            "2.3",
            "scenarios",
            "Сценарии",
            has("scenario"),
            entry and has("term") and not unchosen(pl),
            "scenarist",
            "",
        ),
        _step(
            "gate", "gate_scenarios", "Приёмка сценариев", scen_ok, has("scenario"), "evaluator", ""
        ),
        _step(
            "2.4",
            "schemas",
            "Схема БД сценария",
            has_draft or (scen_ok and not _needs_schema(pl)),
            scen_ok,
            "db_designer",
            "",
        ),
        _step(
            "2.5",
            "merge",
            "Слияние схем",
            has("table") or (scen_ok and not _needs_schema(pl)),
            scen_ok and has_draft,
            "db_designer",
            "",
        ),
        _step(
            "gate",
            "gate_schema",
            "Приёмка схемы БД",
            schema_ok,
            scen_ok and (has("table") or not _needs_schema(pl)),
            "evaluator",
            "",
        ),
    ]


def _needs_schema(pl: Pipeline) -> bool:
    return any(not (s.get("attrs") or {}).get("no_data") for s in pl.bank.records("scenario"))


def run_step(pl: Pipeline, key: str):
    table = {
        "dictionary": build_dictionary,
        "variants": propose_variants,
        "scenarios": write_scenarios,
        "schemas": design_schemas,
        "merge": merge_schemas,
        "gate_scenarios": lambda p: p.run_gate("scenarios"),
        "gate_schema": lambda p: p.run_gate("schema"),
    }
    if key not in table:
        raise PipelineError("unknown_step", f"Шаг {key!r} агентом не выполняется")
    return table[key](pl)
