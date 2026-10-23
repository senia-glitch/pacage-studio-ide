"""CLI студии: dev, scan, list, export, doctor, install-full, help."""

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
from studio._atomic import atomic_write
from studio._constants import FASTBASE_FULL_SPEC
from studio.exporter import EXPORT_FILENAME, build_document
from studio.scanner import scan
from studio.server import PortInUse, serve
from studio.source import module_files, tests_files

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
        "edges": len(graph.edges),
        "nodes": len(graph.nodes),
    }


def _diagnostics_payload(graph) -> list[dict]:
    return [diagnostic.model_dump() for diagnostic in graph.diagnostics]


def _endpoint_label(graph, endpoint_id: str) -> str:
    node = graph.by_id().get(endpoint_id)
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
                    "label": _endpoint_label(graph, endpoint_id) if endpoint_id else "",
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
            f"endpoint {_endpoint_label(graph, endpoint_id)}"
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


def cmd_export(args) -> int:
    root, explicit = _find_root(args.project)
    slog.start_run(root, "export")
    graph = scan(root)
    content = build_document(graph)
    target = Path(args.out) if getattr(args, "out", None) else root / EXPORT_FILENAME
    if not target.is_absolute():
        target = Path.cwd() / target
    target.parent.mkdir(parents=True, exist_ok=True)
    if not atomic_write(target, content):
        raise CliError(f"Не удалось записать {target}")
    _print_root(root, explicit)
    print(f"export: {target}")
    return 0


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


def cmd_help(args) -> int:
    print("studio — карта пути HTTP-запроса")
    print()
    print("  HTTP → endpoint (fastbase) → scenario (core) → io → model (event-infra)")
    print()
    print("Команды:")
    print("  dev   [--project .] [--host H] [--port N] [--no-open] [--poll S] [--verbose]")
    print("                                       автоскан и интерфейс")
    print("  scan  [--project .] [--json]       код → .studio/graph.json")
    print("  list  [--project .] [--json]       сценарии в терминале")
    print("  export [--project .] [--out PATH]  метаданные → STUDIO_METADATA.md")
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

    export_p = sub.add_parser("export", help="метаданные → STUDIO_METADATA.md")
    _add_project(export_p)
    export_p.add_argument("--out", default=None)
    export_p.set_defaults(f=cmd_export)

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
