"""Отчёт по бэкенд-проекту: markdown и те же сведения структурно (JSON).

Отчёт описывает то, что есть в проекте, а не задачи на будущее (это ТЗ):
сводка, структура «домен → метод → эндпоинт → сценарий → io → модель»,
покрытие описаниями и тестами, планы, модели и диагностика. Разделы выбирает
пользователь. Модуль только читает граф; код проекта и метаданные не меняются.
"""

from __future__ import annotations

from pathlib import Path

from studio._atomic import atomic_write
from studio.metamodel import Graph, Node
from studio.sections import pick_sections

REPORT_SECTIONS = ("summary", "structure", "coverage", "plans", "models", "diagnostics")

SECTION_TITLES = {
    "summary": "Сводка",
    "structure": "Структура",
    "coverage": "Покрытие описаниями и тестами",
    "plans": "Планы сценариев",
    "models": "Модели и таблицы",
    "diagnostics": "Диагностика",
}

REPORT_FILE = ".studio/report.md"
NO_DOMAIN = "Без домена"
# Виды узлов, у которых хранятся описание и критерии.
DESCRIBED_KINDS = ("endpoint", "scenario", "io", "model")
KIND_RU = {"endpoint": "эндпоинт", "scenario": "сценарий", "io": "io", "model": "модель"}
STATUS_RU = {"done": "выполнен", "partial": "частично", "not_done": "не выполнен"}


def _endpoint_label(node: Node) -> str:
    details = node.details or {}
    method = str(details.get("http_method", "")).upper()
    path = details.get("url_path") or details.get("path") or "/"
    return f"{method} {path}".strip()


def _where(node: Node) -> str:
    if not node.file_path:
        return "—"
    return f"{node.file_path}:{node.line}" if node.line > 0 else node.file_path


def _domains(node: Node) -> list[str]:
    parts = [part.strip() for part in str(node.domain or "").split(",") if part.strip()]
    return parts or [NO_DOMAIN]


def _summary(graph: Graph) -> dict:
    counts: dict[str, dict] = {}
    for kind in (*DESCRIBED_KINDS, "test"):
        nodes = [n for n in graph.nodes if n.kind == kind]
        counts[kind] = {
            "fact": sum(1 for n in nodes if not n.planned),
            "planned": sum(1 for n in nodes if n.planned),
        }
    domains = {d for n in graph.nodes if n.kind == "endpoint" for d in _domains(n)}
    return {
        "counts": counts,
        "domains": sorted(domains),
        "plans": len(graph.plans),
        "generated_at": graph.generated_at,
        "fingerprint": graph.fingerprint[:12],
    }


def _structure(graph: Graph) -> list[dict]:
    """Домен → эндпоинты; у каждого сценарии, io (с таблицами) и тесты."""
    nodes = graph.by_id()
    table_targets: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.kind == "table" and edge.to_node in nodes:
            table_targets.setdefault(edge.from_node, []).append(nodes[edge.to_node].label)
    scenarios_of: dict[str, list[str]] = {}
    for scenario_id, path in graph.scenario_paths.items():
        for endpoint_id in path.endpoints or ([path.endpoint] if path.endpoint else []):
            scenarios_of.setdefault(endpoint_id, []).append(scenario_id)
    by_domain: dict[str, list[dict]] = {}
    for node in sorted(
        (n for n in graph.nodes if n.kind == "endpoint"),
        key=lambda n: (_endpoint_label(n), n.id),
    ):
        scenarios = []
        for scenario_id in sorted(scenarios_of.get(node.id, [])):
            scenario = nodes.get(scenario_id)
            if scenario is None:
                continue
            path = graph.scenario_paths.get(scenario_id)
            called = path.called_scenarios if path else []
            io_items = []
            for io_id in path.io_calls if path else []:
                io_node = nodes.get(io_id)
                if io_node is not None:
                    tables = table_targets.get(io_id, [])
                    io_items.append({"id": io_id, "label": io_node.label, "tables": tables})
            scenarios.append(
                {
                    "id": scenario_id,
                    "label": scenario.label,
                    "planned": scenario.planned,
                    "io": io_items,
                    "calls": [nodes[c].label for c in called if c in nodes],
                }
            )
        tests = sorted(
            nodes[e.from_node].label
            for e in graph.edges
            if e.kind == "tests" and e.to_node == node.id and e.from_node in nodes
        )
        item = {
            "id": node.id,
            "label": _endpoint_label(node),
            "function": (node.details or {}).get("function_name") or node.label,
            "planned": node.planned,
            "where": _where(node),
            "scenarios": scenarios,
            "tests": tests,
        }
        for domain in _domains(node):
            by_domain.setdefault(domain, []).append(item)
    return [{"domain": d, "endpoints": by_domain[d]} for d in sorted(by_domain, key=_domain_key)]


def _domain_key(name: str) -> tuple[int, str]:
    return (name == NO_DOMAIN, name.lower())


def _coverage(graph: Graph) -> dict:
    tested: dict[str, int] = {}
    for edge in graph.edges:
        if edge.kind == "tests":
            tested[edge.to_node] = tested.get(edge.to_node, 0) + 1
    rows: dict[str, dict] = {}
    missing: list[dict] = []
    for kind in DESCRIBED_KINDS:
        facts = [n for n in graph.nodes if n.kind == kind and not n.planned]
        described = [n for n in facts if str(n.description or "").strip()]
        with_criteria = [n for n in facts if n.acceptance]
        row = {
            "total": len(facts),
            "described": len(described),
            "acceptance": len(with_criteria),
        }
        if kind in ("endpoint", "scenario"):
            row["tested"] = sum(1 for n in facts if tested.get(n.id, 0) > 0)
        rows[kind] = row
        missing += [
            {"id": n.id, "kind": kind, "label": n.label, "where": _where(n)}
            for n in sorted(facts, key=lambda n: (n.file_path, n.line, n.id))
            if not str(n.description or "").strip()
        ]
    return {"kinds": rows, "without_description": missing}


def _plans(graph: Graph) -> list[dict]:
    return [
        {
            "slug": plan.slug,
            "title": plan.title or plan.slug,
            "status": plan.status,
            "domain": plan.domain,
            "route": f"{plan.http_method.upper()} {plan.path}".strip(),
            "layers": [
                {"layer": layer.layer, "status": layer.status, "names": layer.actual_names}
                for layer in plan.layers
            ],
        }
        for plan in sorted(graph.plans, key=lambda p: p.slug)
    ]


def _models(graph: Graph) -> list[dict]:
    nodes = graph.by_id()
    users: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.kind != "table" or edge.from_node not in nodes:
            continue
        scenario_id = str((nodes[edge.from_node].details or {}).get("scenario_id") or "")
        scenario = nodes.get(scenario_id)
        if scenario is not None:
            users.setdefault(edge.to_node, set()).add(scenario.label)
    out = []
    for node in sorted((n for n in graph.nodes if n.kind == "model"), key=lambda n: n.label):
        details = node.details or {}
        out.append(
            {
                "id": node.id,
                "label": node.label,
                "table": details.get("tablename") or details.get("table") or "",
                "fields": len(details.get("fields") or []),
                "planned": node.planned,
                "where": _where(node),
                "used_by": sorted(users.get(node.id, [])),
            }
        )
    return out


def _diagnostics(graph: Graph) -> dict:
    items = [
        {
            "level": diag.level,
            "code": diag.code,
            "message": diag.message,
            "where": f"{diag.file}:{diag.line}" if diag.file else "",
        }
        for diag in graph.diagnostics
        if diag.level in ("error", "warning")
    ]
    yellow = [
        {
            "id": node.id,
            "label": node.label,
            "notes": [str(note.get("message") or note.get("code") or "") for note in node.notes],
        }
        for node in sorted(graph.nodes, key=lambda n: n.id)
        if node.notes
    ]
    return {"items": items, "yellow": yellow}


def _pct(part: int, total: int) -> str:
    return f"{round(100 * part / total)}%" if total else "—"


def _render_summary(data: dict) -> list[str]:
    lines = []
    for kind, label in (
        ("endpoint", "эндпоинтов"),
        ("scenario", "сценариев"),
        ("io", "io-вызовов"),
        ("model", "моделей"),
        ("test", "тестов"),
    ):
        item = data["counts"][kind]
        planned = f" (плановых {item['planned']})" if item["planned"] else ""
        lines.append(f"- {label}: {item['fact']}{planned}")
    lines.append(f"- доменов: {len(data['domains'])}; планов: {data['plans']}")
    if data["generated_at"]:
        lines.append(f"- скан: {data['generated_at']} · отпечаток кода {data['fingerprint']}")
    return lines


def _render_structure(domains: list[dict]) -> list[str]:
    if not domains:
        return ["(эндпоинтов нет)"]
    lines = []
    for block in domains:
        lines.append(f"#### Домен: {block['domain']}")
        for endpoint in block["endpoints"]:
            mark = "[ПЛАН] " if endpoint["planned"] else ""
            head = f"`{endpoint['label']}` — {endpoint['function']} · {endpoint['where']}"
            lines.append(f"- {mark}{head}")
            for scenario in endpoint["scenarios"]:
                planned = "[ПЛАН] " if scenario["planned"] else ""
                lines.append(f"  - {planned}сценарий {scenario['label']}")
                for io_item in scenario["io"]:
                    tables = f" → {', '.join(io_item['tables'])}" if io_item["tables"] else ""
                    lines.append(f"    - io {io_item['label']}{tables}")
                if scenario["calls"]:
                    lines.append(f"    - вызывает: {', '.join(scenario['calls'])}")
            if endpoint["tests"]:
                lines.append(f"  - тесты: {', '.join(endpoint['tests'])}")
    return lines


def _render_coverage(data: dict) -> list[str]:
    lines = ["| вид | всего | с описанием | с критериями | с тестом |", "|---|---|---|---|---|"]
    for kind, row in data["kinds"].items():
        tested = (
            f"{row['tested']} ({_pct(row['tested'], row['total'])})" if "tested" in row else "—"
        )
        lines.append(
            f"| {KIND_RU[kind]} | {row['total']} "
            f"| {row['described']} ({_pct(row['described'], row['total'])}) "
            f"| {row['acceptance']} ({_pct(row['acceptance'], row['total'])}) | {tested} |"
        )
    lines.append("")
    lines.append("Без описания:")
    missing = data["without_description"]
    if not missing:
        lines.append("(все узлы описаны)")
    for item in missing:
        lines.append(f"- {KIND_RU[item['kind']]} `{item['label']}` — {item['where']}")
    return lines


def _render_plans(plans: list[dict]) -> list[str]:
    if not plans:
        return ["(планов нет)"]
    lines = []
    for plan in plans:
        route = f" · {plan['route']}" if plan["route"] else ""
        status = STATUS_RU.get(plan["status"], plan["status"])
        lines.append(f"- `{plan['slug']}` — {plan['title']}: {status}{route}")
        layers = ", ".join(
            f"{layer['layer']}: {'есть' if layer['status'] == 'done' else 'нет'}"
            for layer in plan["layers"]
        )
        if layers:
            lines.append(f"  - слои: {layers}")
    return lines


def _render_models(models: list[dict]) -> list[str]:
    if not models:
        return ["(моделей нет)"]
    lines = ["| модель | таблица | полей | используют сценарии |", "|---|---|---|---|"]
    for model in models:
        used = ", ".join(model["used_by"]) or "—"
        name = f"{_mark_text(model)}{model['label']}"
        lines.append(f"| {name} | {model['table'] or '—'} | {model['fields']} | {used} |")
    return lines


def _mark_text(model: dict) -> str:
    return "[ПЛАН] " if model["planned"] else ""


def _render_diagnostics(data: dict) -> list[str]:
    if not data["items"] and not data["yellow"]:
        return ["(нет)"]
    lines = []
    for item in data["items"]:
        where = f" `{item['where']}`" if item["where"] else ""
        lines.append(f"- **{item['code']}**{where} — {item['message']}")
    if data["yellow"]:
        lines.append("")
        lines.append("Жёлтые пометки:")
        for item in data["yellow"]:
            lines.append(f"- `{item['label']}` — {'; '.join(n for n in item['notes'] if n)}")
    return lines


def build_report(graph: Graph, sections=None) -> dict:
    """Отчёт по графу: {"markdown", "data"}; разделы всегда в порядке REPORT_SECTIONS.

    sections: подмножество REPORT_SECTIONS (None — все; пустой список — ошибка ввода).
    """
    picked = pick_sections(sections, REPORT_SECTIONS, "отчёта")
    if not picked:
        raise ValueError("Выберите хотя бы один раздел отчёта")
    root_name = Path(graph.project_root).name or str(graph.project_root)
    builders = {
        "summary": (_summary, _render_summary),
        "structure": (_structure, _render_structure),
        "coverage": (_coverage, _render_coverage),
        "plans": (_plans, _render_plans),
        "models": (_models, _render_models),
        "diagnostics": (_diagnostics, _render_diagnostics),
    }
    lines = [f"# Отчёт по проекту — {root_name}"]
    data: dict = {"root": root_name, "sections": [k for k in REPORT_SECTIONS if k in picked]}
    for key in REPORT_SECTIONS:
        if key not in picked:
            continue
        collect, render = builders[key]
        value = collect(graph)
        data[key] = value
        lines.append("")
        lines.append(f"## {SECTION_TITLES[key]}")
        lines.append("")
        lines.extend(render(value))
    return {"markdown": "\n".join(lines) + "\n", "data": data}


def save_report(project_root: Path, markdown: str) -> str:
    """Записать отчёт в `.studio/report.md`; вернуть относительный путь."""
    target = Path(project_root) / REPORT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    if not atomic_write(target, markdown):
        raise OSError(f"Не удалось записать {REPORT_FILE}")
    return REPORT_FILE


__all__ = [
    "REPORT_FILE",
    "REPORT_SECTIONS",
    "SECTION_TITLES",
    "build_report",
    "save_report",
]
