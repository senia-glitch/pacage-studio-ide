"""CLI студии: dev, scan, list, brief, verify, doctor, install-full, help."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

from studio import __version__, store
from studio import log as slog
from studio._constants import FASTBASE_FULL_SPEC
from studio.agent import (
    BriefError,
    BriefNotFound,
    BriefOptions,
    build_brief,
    load_brief,
    render_report,
    save_brief,
    save_brief_sidecars,
    verify_brief,
)
from studio.agent.report import build_report, save_report
from studio.env import read_env
from studio.frontend.brief import report_frontend
from studio.scanner import scan
from studio.server import PortInUse, serve
from studio.source import load_sources, module_files, tests_files

PROJECT_MARKERS = (".env.fastbase", "app", "models.py")


class CliError(Exception):
    def __init__(self, message: str, code: int = 1):
        self.code = code
        super().__init__(message)


def _find_root(explicit: str | None) -> tuple[Path, bool]:
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not root.is_dir():
            raise CliError(f"Проект не найден: {root}", 2)
        return root, True
    current = Path.cwd().resolve()
    for candidate in [current, *current.parents][:7]:
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate, False
    return current, False


def _print_root(root: Path, explicit: bool) -> None:
    suffix = " (--project)" if explicit else ""
    print(f"Проект: {root}{suffix}")


def _count(graph, kind: str) -> int:
    return sum(1 for n in graph.nodes if n.kind == kind)


def _graph_counts(graph) -> dict:
    return {
        "endpoints": _count(graph, "endpoint"),
        "scenarios": _count(graph, "scenario"),
        "io": _count(graph, "io"),
        "models": _count(graph, "model"),
        "tests": _count(graph, "test"),
        "nested": _count(graph, "nested"),
        "edges": len(graph.edges),
        "nodes": len(graph.nodes),
    }


def _diagnostics_payload(graph) -> list[dict]:
    return [diagnostic.model_dump() for diagnostic in graph.diagnostics]


def _endpoint_label(graph, endpoint_id: str, nodes: dict | None = None) -> str:
    node = (nodes if nodes is not None else graph.by_id()).get(endpoint_id)
    if node is None:
        return endpoint_id
    details = node.details
    method = str(details.get("http_method", "")).upper()
    path = details.get("url_path") or details.get("path") or "/"
    return f"{method} {path}".strip()


def cmd_scan(args) -> int:
    root, explicit = _find_root(args.project)
    slog.start_run(root, "scan")
    graph = scan(root)
    path = store.write_graph(root, graph)
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "root": root.as_posix(),
                    "counts": _graph_counts(graph),
                    "diagnostics": _diagnostics_payload(graph),
                    "fingerprint": graph.fingerprint,
                    "generated_at": graph.generated_at,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    _print_root(root, explicit)
    counts = _graph_counts(graph)
    print(
        f"Эндпоинтов {counts['endpoints']} · сценариев {counts['scenarios']} · "
        f"io {counts['io']} · моделей {counts['models']} · тестов {counts['tests']} · "
        f"связей {counts['edges']}"
    )
    errors = [d for d in graph.diagnostics if d.level == "error"]
    warnings = [d for d in graph.diagnostics if d.level == "warning"]
    if errors or warnings:
        print(f"Диагностика: {len(errors)} ошибок · {len(warnings)} предупреждений")
        for diagnostic in graph.diagnostics:
            where = f" {diagnostic.file}:{diagnostic.line}" if diagnostic.file else ""
            print(f"  {diagnostic.code}{where} — {diagnostic.message}")
    print(f"graph: {path}")
    return 0


def cmd_list(args) -> int:
    root, explicit = _find_root(args.project)
    slog.start_run(root, "list")
    graph = store.read_graph(root)
    if graph is None:
        graph = scan(root)
        store.write_graph(root, graph)
    nodes = graph.by_id()
    groups: dict[str | None, list[str]] = {}
    for scenario_id in graph.scenarios:
        path = graph.scenario_paths.get(scenario_id)
        endpoints = path.endpoints if path is not None else []
        if not endpoints:
            groups.setdefault(None, []).append(scenario_id)
            continue
        for endpoint_id in endpoints:
            groups.setdefault(endpoint_id, []).append(scenario_id)
    if getattr(args, "json", False):
        payload = {
            "root": root.as_posix(),
            "groups": [
                {
                    "endpoint": endpoint_id,
                    "label": _endpoint_label(graph, endpoint_id, nodes)
                    if endpoint_id
                    else "",
                    "scenarios": [
                        {
                            "id": scenario_id,
                            "label": nodes[scenario_id].label
                            if scenario_id in nodes
                            else scenario_id,
                            "io": list(
                                graph.scenario_paths[scenario_id].io_calls
                            )
                            if scenario_id in graph.scenario_paths
                            else [],
                            "calls": list(
                                graph.scenario_paths[scenario_id].called_scenarios
                            )
                            if scenario_id in graph.scenario_paths
                            else [],
                        }
                        for scenario_id in groups[endpoint_id]
                    ],
                }
                for endpoint_id in sorted(groups, key=lambda value: (value is None, value or ""))
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    _print_root(root, explicit)
    order = [endpoint_id for endpoint_id in graph.endpoints if endpoint_id in groups]
    if None in groups:
        order.append(None)
    for endpoint_id in order:
        header = (
            f"endpoint {_endpoint_label(graph, endpoint_id, nodes)}"
            if endpoint_id
            else "(без endpoint)"
        )
        print(header)
        for scenario_id in groups[endpoint_id]:
            node = nodes.get(scenario_id)
            print(f"  scenario {node.label if node else scenario_id}")
            path = graph.scenario_paths.get(scenario_id)
            if path is None:
                continue
            for io_id in path.io_calls:
                io_node = nodes.get(io_id)
                if io_node is None:
                    continue
                targets = [
                    nodes[edge.to_node].label
                    for edge in graph.edges
                    if edge.kind == "table"
                    and edge.from_node == io_id
                    and edge.to_node in nodes
                ]
                suffix = f" -> {', '.join(targets)}" if targets else ""
                print(f"    io: {io_node.label}{suffix}")
            if path.called_scenarios:
                names = [
                    nodes[called_id].label if called_id in nodes else called_id
                    for called_id in path.called_scenarios
                ]
                print(f"    calls: {', '.join(names)}")
    if not graph.scenarios:
        print("Сценариев нет — проверьте FAT_CORE_DISCOVER или выполните studio doctor.")
    return 0


def _brief_options(args) -> BriefOptions:
    raw_only = getattr(args, "only", None)
    raw_plans = getattr(args, "plan", None)
    raw_sections = getattr(args, "section", None)
    return BriefOptions(
        map_mode=str(getattr(args, "map", "") or ""),
        only=tuple(str(item) for item in raw_only) if raw_only else None,
        plans=tuple(str(item) for item in raw_plans) if raw_plans else None,
        scenario=str(getattr(args, "scenario", "") or ""),
        include_empty=bool(getattr(args, "include_empty", False)),
        sections=tuple(str(item) for item in raw_sections) if raw_sections else None,
    )


def cmd_brief(args) -> int:
    root, explicit = _find_root(args.project)
    slog.start_run(root, "brief")
    graph = scan(root)
    try:
        sources = load_sources(read_env(root))
        brief = build_brief(graph, sources, _brief_options(args))
    except BriefError as exc:
        raise CliError(exc.message, 2 if exc.code == "plan_not_found" else 1) from exc

    if getattr(args, "json", False):
        try:
            save_brief_sidecars(root, brief)
        except BriefError as exc:
            raise CliError(exc.message) from exc
        print(
            json.dumps(
                {
                    "root": root.as_posix(),
                    "markdown": brief.markdown,
                    "brief_id": brief.brief_id,
                    "fingerprint": brief.fingerprint,
                    "stats": brief.stats,
                    "warnings": brief.warnings,
                    "tasks": [
                        {
                            "number": item["number"],
                            "kind": item.get("kind") or item.get("type", ""),
                            "anchor": item.get("anchor")
                            or item.get("slug")
                            or item.get("title", ""),
                            "target_file": item.get("target_file", ""),
                        }
                        for item in brief.tasks
                    ],
                    "unready": brief.unready,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if getattr(args, "stdout", False):
        try:
            save_brief_sidecars(root, brief)
        except BriefError as exc:
            raise CliError(exc.message) from exc
        print(brief.markdown, end="")
        return 0
    target = Path(args.out) if getattr(args, "out", None) else None
    try:
        path = save_brief(root, brief, target)
    except BriefError as exc:
        raise CliError(exc.message) from exc
    try:
        shown = path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        shown = path.as_posix()
    _print_root(root, explicit)
    print(f"ТЗ: {shown}")
    print(
        f"Задач: {brief.stats['tasks']} · не готово: {brief.stats['unready']} · "
        f"{brief.stats['chars']} символов (~{brief.stats['approx_tokens']} токенов)"
    )
    print(
        "ТЗ содержит фрагменты вашего кода и описания — перед отправкой "
        "во внешний AI-сервис это решает пользователь."
    )
    for warning in brief.warnings:
        print(f"  ! {warning}")
    if brief.tasks:
        print(
            "Прочитай и выполни ТЗ из файла "
            f"{shown}, следуй его правилам"
        )
    else:
        print("Нет узлов к реализации")
    for item in brief.unready:
        print(
            f"  не готово: {item['kind']} {item['anchor']} — {item['file']}: "
            + ", ".join(item["missing"])
        )
    return 0


def cmd_report(args) -> int:
    """Отчёт по проекту: бэкенд (по графу) или фронтенд (по frontend/)."""
    root, _explicit = _find_root(args.project)
    slog.start_run(root, "report")
    sections = list(args.section) or None
    try:
        if args.part == "frontend":
            result = report_frontend(root, sections=sections)
        else:
            result = build_report(scan(root), sections=sections)
            result["path"] = save_report(root, result["markdown"])
    except ValueError as exc:
        raise CliError(str(exc), 2) from exc
    if args.json:
        print(
            json.dumps(
                {
                    "root": root.as_posix(),
                    "part": args.part,
                    "path": result["path"],
                    "markdown": result["markdown"],
                    "data": result["data"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.stdout:
        print(result["markdown"], end="")
    else:
        print(f"Отчёт сохранён: {result['path']}")
    return 0


def _cmd_verify_frontend(args, root, explicit, graph) -> int:
    from studio.frontend.brief import load_front_brief
    from studio.frontend.graph import build_front_graph
    from studio.frontend.links import check_links
    from studio.frontend.verify import render_front_report, verify_front_brief

    try:
        brief = load_front_brief(root, getattr(args, "brief", None))
    except FileNotFoundError as exc:
        raise CliError(str(exc), 2) from exc
    front = build_front_graph(root)
    links = check_links(front, graph, read_env(root).api_prefix)
    report = verify_front_brief(front, brief, links)
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return int(report["code"])
    _print_root(root, explicit)
    print(render_front_report(report))
    return int(report["code"])


def cmd_verify(args) -> int:
    root, explicit = _find_root(args.project)
    slog.start_run(root, "verify")
    graph = scan(root)
    if getattr(args, "part", "backend") == "frontend":
        return _cmd_verify_frontend(args, root, explicit, graph)
    try:
        data = load_brief(root, getattr(args, "brief", None))
    except BriefNotFound as exc:
        raise CliError(exc.message, 2) from exc
    report = verify_brief(graph, data)
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return int(report["code"])
    _print_root(root, explicit)
    print(render_report(report))
    return int(report["code"])


def _source_stats(root, graph, env) -> list[dict]:
    routes_files = len(module_files(root, env.routes_package))
    core_files = len(module_files(root, env.core_discover))
    model_paths = [path for path in env.models_paths if path.is_file()]
    tests_count_files = len(tests_files(root))
    return [
        {
            "name": "fastbase",
            "ok": routes_files > 0,
            "target": env.routes_package,
            "files": routes_files,
            "units": sum(1 for n in graph.nodes if n.kind == "endpoint"),
            "unit": "эндпоинта",
            "hint": "FAT_ROUTES_PACKAGE в .env.fastbase",
        },
        {
            "name": "core-package",
            "ok": core_files > 0,
            "target": env.core_discover,
            "files": core_files,
            "units": sum(1 for n in graph.nodes if n.kind == "scenario"),
            "unit": "сценарий",
            "hint": "FAT_CORE_DISCOVER в .env.fastbase",
        },
        {
            "name": "event-infra",
            "ok": bool(model_paths),
            "target": env.models_spec
            or ", ".join(path.name for path in model_paths)
            or "models.py",
            "files": len(model_paths),
            "units": sum(1 for n in graph.nodes if n.kind == "model"),
            "unit": "моделей",
            "hint": "создайте models.py или задайте STUDIO_MODELS",
        },
        {
            "name": "tests",
            "ok": tests_count_files > 0,
            "target": "tests/",
            "files": tests_count_files,
            "units": sum(1 for n in graph.nodes if n.kind == "test"),
            "unit": "тестов",
            "hint": "создайте tests/ или не указывайте тесты",
        },
    ]


def cmd_doctor(args) -> int:
    root, explicit = _find_root(args.project)
    slog.start_run(root, "doctor")
    graph = scan(root)
    from studio.env import read_env

    env = read_env(root)
    stats = _source_stats(root, graph, env)
    errors = [d for d in graph.diagnostics if d.level == "error"]
    warnings = [d for d in graph.diagnostics if d.level == "warning"]
    strict = bool(getattr(args, "strict", False))
    code = 1 if errors or (strict and warnings) else 0
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "version": __version__,
                    "root": root.as_posix(),
                    "sources": stats,
                    "env": {
                        "file": env.env_file.is_file(),
                        "routes_package": env.routes_package,
                        "core_discover": env.core_discover,
                        "api_prefix": env.api_prefix,
                        "models": env.models_spec
                        or ", ".join(path.name for path in env.models_paths),
                    },
                    "graph": _graph_counts(graph),
                    "brief": {
                        "tasks": sum(
                            1
                            for node in graph.nodes
                            if node.planned and (node.description or "").strip()
                        ),
                        "unready": sum(
                            1
                            for node in graph.nodes
                            if node.planned and not (node.description or "").strip()
                        ),
                    },
                    "diagnostics": _diagnostics_payload(graph),
                    "ok": code == 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return code
    print(f"pacage-studio {__version__} · проект: {root}")
    print("Источники:")
    for item in stats:
        mark = "✓" if item["ok"] else "✗"
        if item["ok"]:
            print(
                f"  {mark} {item['name']:<13} {item['target']:<22} "
                f"{item['files']} файлов · {item['units']} {item['unit']}"
            )
        else:
            print(
                f"  {mark} {item['name']:<13} {item['target']:<22} "
                f"не найден → {item['hint']}"
            )
    env_state = "найден" if env.env_file.is_file() else "не найден"
    print(f"Окружение: .env.fastbase {env_state} (FAT_API_PREFIX={env.api_prefix})")
    incomplete = sum(1 for node in graph.nodes if not node.complete)
    print(
        f"Граф: узлов {len(graph.nodes)} · связей {len(graph.edges)} · "
        f"неполных {incomplete}"
    )
    planned = [node for node in graph.nodes if node.planned]
    ready = sum(1 for node in planned if (node.description or "").strip())
    print(f"К реализации: {ready} · не готово: {len(planned) - ready}")
    print(f"Диагностика: {len(errors)} ошибок · {len(warnings)} предупреждений")
    for diagnostic in graph.diagnostics:
        where = f" {diagnostic.file}:{diagnostic.line}" if diagnostic.file else ""
        print(f"  {diagnostic.code}{where} — {diagnostic.message}")
    print("Итог: есть ошибки" if errors else ("есть предупреждения" if warnings else "порядок"))
    if strict and warnings and not errors:
        print("Режим --strict: предупреждения считаются ошибкой")
    return code


def cmd_install_full(args) -> int:
    root, explicit = _find_root(args.project)
    slog.start_run(root, "install-full")
    _print_root(root, explicit)
    command = [sys.executable, "-m", "pip", "install", "--upgrade", FASTBASE_FULL_SPEC]
    print("studio install-full: " + " ".join(command))
    try:
        result = subprocess.run(command)
    except OSError as exc:
        raise CliError(f"Не удалось запустить pip: {exc}") from exc
    if result.returncode != 0:
        raise CliError(f"pip завершился с кодом {result.returncode}")
    return 0


def cmd_dev(args) -> int:
    root, explicit = _find_root(args.project)
    slog.start_run(root, f"dev {args.host}:{args.port or 'auto'}")
    _print_root(root, explicit)
    try:
        serve(
            root,
            host=args.host,
            port=args.port,
            no_open=bool(args.no_open),
            poll=args.poll,
            verbose=bool(args.verbose),
        )
    except PortInUse as exc:
        raise CliError(str(exc)) from exc
    return 0


def cmd_skill(args) -> int:
    """Руководство для AI-агента: печать или установка в проект (скилл, AGENTS.md)."""
    from studio import __version__
    from studio.agent import guide

    if args.target == "stdout":
        print(guide.load_guide(), end="")
        return 0
    root, explicit = _find_root(args.project)
    _print_root(root, explicit)
    try:
        written = guide.install(root, args.target, __version__)
    except OSError as exc:
        raise CliError(str(exc)) from exc
    for path in written:
        print(f"записано: {path.relative_to(root).as_posix()}")
    print("Агент прочитает это руководство сам; студия отдаёт то же по GET /api/agent/guide.")
    return 0


def cmd_mcp(_args) -> int:
    """MCP-сервер (stdio) для AI-агента. В stdout — только протокол, поэтому без печати."""
    try:
        from studio.mcp_server import create_server

        server = create_server()
    except ImportError as exc:
        raise CliError(str(exc)) from exc
    server.run()
    return 0


def cmd_help(_args) -> int:
    print("studio — карта пути HTTP-запроса")
    print()
    print("  HTTP → endpoint (fastbase) → scenario (core) → io → model (event-infra)")
    print()
    print("Команды:")
    print("  dev   [--project .] [--host H] [--port N] [--no-open] [--poll S] [--verbose]")
    print("                                       автоскан и интерфейс")
    print("  scan  [--project .] [--json]       код → .studio/graph.json")
    print("  list  [--project .] [--json]       сценарии в терминале")
    print("  brief [--out PATH | --stdout] [--plan SLUG ...] [--only A ...]")
    print("        [--scenario NAME] [--map full|related|compact|none] [--include-empty]")
    print("        [--section map|unready|diagnostics|anchors ...] [--json]")
    print("                                       ТЗ для AI-агента")
    print("  verify [BRIEF] [--json]            проверить выполнение ТЗ")
    print("  report [--part backend|frontend] [--section S ...] [--stdout | --json]")
    print("                                       отчёт по проекту")
    print("  skill [--target stdout|claude|agents|all] [--project .]")
    print("                                       руководство для AI-агента: печать или")
    print("                                       установка (скилл Claude Code, AGENTS.md)")
    print("  mcp                                MCP-сервер для AI-агента (stdio);")
    print('                                       нужен pip install "pacage-studio[mcp]"')
    print("  doctor [--project .] [--json] [--strict]  диагностика одной командой")
    print("  install-full [--project .]         установить/обновить fastbase[full]")
    print("  help                               этот экран")
    print("  --version                          версия пакета")
    print()
    print("Без команды studio запускает dev.")
    return 0


def _add_project(sub) -> None:
    sub.add_argument("--project", default=argparse.SUPPRESS, help="корень проекта")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="studio",
        description="pacage-studio — карта пути HTTP-запроса",
    )
    parser.add_argument("--project", default=None, help="корень проекта (поиск вверх)")
    sub = parser.add_subparsers(dest="cmd")

    dev_p = sub.add_parser("dev", help="автоскан и интерфейс")
    _add_project(dev_p)
    dev_p.add_argument("--host", default="127.0.0.1")
    dev_p.add_argument("--port", type=int, default=None)
    dev_p.add_argument("--no-open", action="store_true")
    dev_p.add_argument("--poll", type=float, default=None)
    dev_p.add_argument("--verbose", action="store_true")
    dev_p.set_defaults(f=cmd_dev)

    scan_p = sub.add_parser("scan", help="код → .studio/graph.json")
    _add_project(scan_p)
    scan_p.add_argument("--json", action="store_true")
    scan_p.set_defaults(f=cmd_scan)

    list_p = sub.add_parser("list", help="список сценариев в терминале")
    _add_project(list_p)
    list_p.add_argument("--json", action="store_true")
    list_p.set_defaults(f=cmd_list)

    brief_p = sub.add_parser("brief", help="ТЗ для AI-агента")
    _add_project(brief_p)
    brief_out = brief_p.add_mutually_exclusive_group()
    brief_out.add_argument("--out", default=None, help="куда записать markdown")
    brief_out.add_argument("--stdout", action="store_true", help="печатать ТЗ в stdout")
    brief_p.add_argument("--plan", action="append", default=[], metavar="SLUG")
    brief_p.add_argument("--only", action="append", default=[], metavar="ANCHOR")
    brief_p.add_argument("--scenario", default="", metavar="NAME")
    brief_p.add_argument("--map", choices=["full", "related", "compact", "none"], default="")
    brief_p.add_argument("--include-empty", action="store_true")
    brief_p.add_argument(
        "--section", action="append", default=[], metavar="NAME",
        help="необязательный раздел ТЗ: map, unready, diagnostics, anchors "
        "(можно повторять); без флага — все",
    )
    brief_p.add_argument("--json", action="store_true")
    brief_p.set_defaults(f=cmd_brief)

    report_p = sub.add_parser("report", help="отчёт по проекту")
    _add_project(report_p)
    report_p.add_argument("--part", choices=["backend", "frontend"], default="backend")
    report_p.add_argument(
        "--section", action="append", default=[], metavar="NAME",
        help="раздел отчёта (можно повторять); без флага — все",
    )
    report_out = report_p.add_mutually_exclusive_group()
    report_out.add_argument("--stdout", action="store_true", help="печатать отчёт в stdout")
    report_out.add_argument("--json", action="store_true")
    report_p.set_defaults(f=cmd_report)

    verify_p = sub.add_parser("verify", help="проверить выполнение ТЗ")
    _add_project(verify_p)
    verify_p.add_argument("brief", nargs="?", default=None, help="файл, id или last")
    verify_p.add_argument("--part", choices=["backend", "frontend"], default="backend")
    verify_p.add_argument("--json", action="store_true")
    verify_p.set_defaults(f=cmd_verify)

    skill_p = sub.add_parser("skill", help="руководство для AI-агента: печать или установка")
    _add_project(skill_p)
    skill_p.add_argument(
        "--target",
        choices=["stdout", "claude", "agents", "all"],
        default="stdout",
        help="stdout — напечатать; claude — .claude/skills; agents — блок в AGENTS.md",
    )
    skill_p.set_defaults(f=cmd_skill)

    mcp_p = sub.add_parser("mcp", help="MCP-сервер для AI-агента (stdio)")
    mcp_p.set_defaults(f=cmd_mcp)

    doctor_p = sub.add_parser("doctor", help="диагностика проекта")
    _add_project(doctor_p)
    doctor_p.add_argument("--json", action="store_true")
    doctor_p.add_argument("--strict", action="store_true")
    doctor_p.set_defaults(f=cmd_doctor)

    install_p = sub.add_parser("install-full", help="установить/обновить fastbase[full]")
    _add_project(install_p)
    install_p.set_defaults(f=cmd_install_full)

    help_p = sub.add_parser("help", help="показать все команды")
    help_p.set_defaults(f=cmd_help)
    return parser


def _safe_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        isatty = getattr(stream, "isatty", lambda: False)()
        if reconf is None or isatty:
            continue
        with contextlib.suppress(OSError, ValueError):
            reconf(encoding="utf-8", errors="replace")


def main(argv=None) -> int:
    _safe_stdio()
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    if "--version" in argv or "-V" in argv:
        print(__version__)
        return 0
    if not argv:
        argv = ["dev"]
    if (
        argv[0].startswith("-")
        and argv[0] not in {"--project", "--version", "-V", "-h", "--help"}
    ):
        argv = ["dev", *argv]
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if not hasattr(args, "f"):
            args = parser.parse_args(["dev", *argv])
        return args.f(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    except CliError as exc:
        print(str(exc))
        return exc.code
    except KeyboardInterrupt:
        print("Остановлено")
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI не печатает трейсбек
        if os.environ.get("STUDIO_DEBUG") == "1":
            raise
        print(f"Ошибка: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
