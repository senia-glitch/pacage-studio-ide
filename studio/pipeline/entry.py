"""Три входа в конвейер: «Новая система», «Развитие» (код есть, банка нет) и «Запрос».

«Развитие»: сканер читает код проекта и кладёт найденное в банк с меткой «из кода»; агент
восстанавливает замысел — проблемы, требования и сценарии — и привязывает найденное к ним.
«Запрос»: мелкая правка идёт быстрым путём (только затронутое перепроверяется своими воротами,
код правит исполнитель по ТЗ затронутых сценариев), остальное — полным путём с этапа-владельца.
"""

from __future__ import annotations

from pathlib import Path

from studio.pipeline.graph import ACCEPTED, STALE

from . import prompts, stage3, tz
from .bank import ProjectBank
from .engine import Pipeline
from .errors import PipelineError
from .kinds import stage_of
from .stage1 import _ids, _items, _s

CODE_MARKERS = ("app", "frontend", "models.py", "core_project", "src")


def detect(project: str | Path) -> str:
    """Какой вход: ``new`` (папка пуста), ``develop`` (код есть, банка нет), ``request`` (банк есть)."""
    root = Path(project)
    if ProjectBank.exists(root):
        return "request"
    has_code = any((root / name).exists() for name in CODE_MARKERS) or any(root.glob("*.py"))
    return "develop" if has_code else "new"


# ---------------------------------------------------------------------- Развитие


def scan_facts(project: Path) -> dict:
    """Факты из кода: эндпоинты, модели БД, страницы. Ничего не запускает и не меняет."""
    facts = {"endpoints": [], "models": [], "pages": []}
    try:
        from studio.scanner import scan

        graph = scan(Path(project))
    except Exception:  # noqa: BLE001 — проект без бэкенда стека тоже годится
        graph = None
    for node in getattr(graph, "nodes", []) or []:
        if node.kind == "endpoint":
            facts["endpoints"].append(
                {
                    "title": node.label,
                    "method": str(node.details.get("http_method", "")).upper(),
                    "path": node.details.get("url_path") or node.details.get("path") or "",
                    "file": node.file_path,
                }
            )
        elif node.kind == "model":
            facts["models"].append(
                {
                    "title": node.details.get("tablename") or node.label,
                    "file": node.file_path,
                    "fields": [
                        f.get("name", f) if isinstance(f, dict) else f
                        for f in node.details.get("fields", [])
                    ],
                }
            )
    try:
        from studio.frontend.graph import build_front_graph

        for page in build_front_graph(project).get("pages", []):
            facts["pages"].append({"title": page.get("name", ""), "file": page.get("folder", "")})
    except Exception:  # noqa: BLE001
        pass
    return facts


def scan_into_bank(pl: Pipeline) -> dict:
    """Найденное в коде — в банк с меткой «из кода». Повторный запуск дубли не создаёт."""
    facts = scan_facts(pl.project)
    made = {"endpoint": 0, "table": 0, "page": 0}
    for item in facts["endpoints"]:
        title = item["title"] or f"{item['method']} {item['path']}"
        if title and not pl.bank.find("endpoint", title):
            pl.bank.add_from_code(
                "endpoint",
                title,
                attrs={"method": item["method"], "path": item["path"], "file": item["file"]},
            )
            made["endpoint"] += 1
    for item in facts["models"]:
        if item["title"] and not pl.bank.find("table", item["title"]):
            cols = [
                {"name": str(f), "type": "text", "key": "", "ref": "", "null": True}
                for f in item["fields"]
                if isinstance(f, str)
            ]
            pl.bank.add_from_code(
                "table",
                item["title"],
                attrs={"name": item["title"], "columns": cols, "file": item["file"]},
            )
            made["table"] += 1
    for item in facts["pages"]:
        if item["title"] and not pl.bank.find("page", item["title"]):
            pl.bank.add_from_code("page", item["title"], attrs={"file": item["file"]})
            made["page"] += 1
    return made


def undescribed(pl: Pipeline) -> list[dict]:
    return [
        c
        for c in pl.bank.records()
        if (c.get("attrs") or {}).get("from_code") and not c["attrs"].get("described")
    ]


def reconstruct(pl: Pipeline) -> dict:
    """Восстановление замысла: агент предлагает проблемы, требования и сценарии по найденному коду."""
    todo = undescribed(pl)
    if not todo:
        return {"created": [], "skipped": []}
    listing = "\n".join(f"{c['id']} [{c['kind']}] {c['title']}" for c in todo)
    prompt = (
        f"В проекте уже есть код. Найдено:\n{listing}\n\n"
        "Восстанови замысел: сгруппируй найденное в функции системы. Для каждой — какую проблему "
        "она решает, требование («Система должна…»), сценарий и коды страниц, таблиц и "
        "эндпоинтов, которые к ней относятся. Не выдумывай того, чего нет в списке.\n"
        'Ответ: {"features": [{"problem": "...", "requirement": "Система должна ...", '
        '"scenario": "...", "pages": ["c3"], "tables": ["c4"], "endpoints": ["c5"]}]}'
    )
    data = pl.llm.ask_json("analyst", prompts.SYSTEM["analyst"], prompt)
    areas = pl.bank.records("area")
    area = (
        areas[0]
        if areas
        else pl.bank.add("area", pl.project.name or "Существующая система", author="analyst")
    )
    boundary = pl.bank.find("boundary", "Существующая система: что уже реализовано") or pl.bank.add(
        "boundary",
        "Существующая система: что уже реализовано",
        parents=[area["id"]],
        author="analyst",
    )
    created, skipped = [], []
    valid = {c["id"] for c in todo}
    for item in _items(data, "features"):
        problem, req, scenario = (
            _s(item.get(k), 200) for k in ("problem", "requirement", "scenario")
        )
        pages = [i for i in _ids(pl, item.get("pages"), ("page",)) if i in valid]
        tables = [i for i in _ids(pl, item.get("tables"), ("table",)) if i in valid]
        endpoints = [i for i in _ids(pl, item.get("endpoints"), ("endpoint",)) if i in valid]
        if not (problem and req and scenario) or not (pages or tables or endpoints):
            skipped.append(
                f"«{scenario or req or problem}»: нет проблемы, требования, сценария или найденного кода"
            )
            continue
        prob = pl.bank.find("problem", problem) or pl.bank.add(
            "problem", problem, parents=[boundary["id"]], author="analyst"
        )
        requirement = pl.bank.find("requirement", req) or pl.bank.add(
            "requirement", req, parents=[prob["id"]], author="analyst"
        )
        scen = pl.bank.add("scenario", scenario, parents=[requirement["id"]], author="analyst")
        created.append(scen)
        for page in pages:
            pl.bank.link(page, scen["id"], "analyst")
        for table in tables:
            pl.bank.link(table, scen["id"], "analyst")
        for endpoint in endpoints:
            card = pl.bank.get(endpoint)
            parent = pages[0] if pages else scen["id"]
            call = pl.bank.add(
                "call",
                card["title"],
                parents=[parent],
                attrs={"method": card["attrs"].get("method"), "path": card["attrs"].get("path")},
                author="analyst",
            )
            pl.bank.link(endpoint, call["id"], "analyst")
    return {"created": created, "skipped": skipped}


# ---------------------------------------------------------------------- Запрос


def submit(
    pl: Pipeline, text: str, *, patches: dict | None = None, new: dict | None = None
) -> dict:
    """Подать запрос и определить путь: мелкая правка — быстрый, остальное — полный с этапа-владельца."""
    request = pl.changes.open(text, "human", patches=patches, new=new)
    request["route"] = (
        "quick" if request["size"] == "small" and request["type"] == "edit" else "full"
    )
    request["start_stage"] = request["owner_stage"]
    pl.changes._save()
    return request


def affected_scenarios(pl: Pipeline, ids: list[str]) -> list[dict]:
    found: dict[str, dict] = {}
    for rid in ids:
        card = pl.bank.get(rid)
        cards = [card, *pl.bank.ancestors(rid)]
        for c in cards:
            if c["kind"] == "scenario" and c["status"] != "rejected":
                found[c["id"]] = c
    return list(found.values())


def apply_accepted(pl: Pipeline, request_id: str) -> list[dict]:
    """После принятия запроса: перепроверить затронутое своими воротами (и обновить код, если нужно)."""
    request = pl.changes.get(request_id)
    if request["status"] != "accepted":
        raise PipelineError("not_accepted", "Запрос ещё не принят")
    stale = [r for r in request.get("stale", []) if pl.bank.get(r)["status"] == STALE]
    stages = sorted({stage_of(pl.bank.get(r)["kind"]) for r in stale})
    log: list[dict] = []
    for stage in stages:
        if stage == 1:
            for key in ("problems", "requirements"):
                run = pl.run_gate(key)
                log.append({"gate": key, "state": run["state"], "human": run.get("human")})
        elif stage == 2:
            for key in ("scenarios", "schema"):
                run = pl.run_gate(key)
                log.append({"gate": key, "state": run["state"], "human": run.get("human")})
        elif stage in (3, 4):
            scenarios = affected_scenarios(pl, stale)
            layer = "frontend" if stage == 3 else "backend"
            if scenarios:
                tz.build(pl, stage, scenarios)
                out = stage3.implement_for(pl, stage, layer, scenarios)
                log.append({"stage": stage, "implement": out.get("green")})
            from . import stage4

            run = pl.run_gate(layer, stage3.facts(pl) if stage == 3 else stage4.facts(pl))
            log.append({"gate": layer, "state": run["state"], "human": run.get("human")})
    return log


__all__ = ["ACCEPTED", "apply_accepted", "detect", "reconstruct", "scan_into_bank", "submit"]
