"""CLI студии: scan / dev / list / scaffold / export / reset / help.

Скан пишет `.studio/graph.json` (схема v4), scaffold создаёт файлы-скелеты
с маркером. Существующий код проекта не изменяется.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from studio import log as slog
from studio import store
from studio.exporter import to_json, to_markdown
from studio.scaffold import apply_scaffold, collect_model_appends, plan_scaffold
from studio.scanner import build_scenario_paths, scan
from studio.server import serve


def _root(args) -> Path:
    return Path(getattr(args, "project", None) or ".").resolve()


def _count(graph, kind: str) -> int:
    return sum(1 for n in graph.nodes if n.kind == kind)


def _origin_tag(node) -> str:
    if node is None:
        return ""
    if node.origin == "user":
        return " [план]"
    if node.origin == "stale":
        return " [stale]"
    return ""


def _confirm(question: str) -> bool:
    """y/да → True; неинтерактивный ввод (EOF) → False без исключения."""
    try:
        answer = input(question).strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes", "д", "да")


def cmd_scan(args) -> int:
    root = _root(args)
    slog.start_run(root, "scan")
    slog.step(f"проект: {root}")
    graph = scan(root)
    slog.step(
        f"итог: endpoints={len(graph.endpoints)} scenarios={len(graph.scenarios)} "
        f"io={_count(graph, 'io')} models={_count(graph, 'model')} "
        f"helpers={_count(graph, 'helper')} tests={_count(graph, 'test')} связей={len(graph.edges)}"
    )
    path = store.write_graph(root, graph)
    print(
        f"scan: endpoints {len(graph.endpoints)}, scenarios {len(graph.scenarios)}, "
        f"io {_count(graph, 'io')}, models {_count(graph, 'model')}, "
        f"tests {_count(graph, 'test')}"
    )
    print(f"graph: {path}")
    return 0


def _endpoint_label(graph, endpoint_id: str) -> str:
    nodes = graph.by_id()
    node = nodes.get(endpoint_id)
    if node is None:
        return endpoint_id
    details = node.details
    method = str(details.get("http_method", "")).upper()
    path = details.get("path") or "/"
    return f"{method} {path}".strip()


def cmd_list(args) -> int:
    root = _root(args)
    graph = store.read_graph(root)
    if graph is None:
        print("no graph: run studio scan first")
        return 2
    slog.start_run(root, "list")
    nodes = graph.by_id()
    groups: dict[str | None, list[str]] = {}
    for scenario_id in graph.scenarios:
        path = graph.scenario_paths.get(scenario_id)
        key = path.endpoint if path and path.endpoint else None
        groups.setdefault(key, []).append(scenario_id)

    order = [ep for ep in graph.endpoints if ep in groups]
    if None in groups:
        order.append(None)

    slog.step(f"сценариев: {len(graph.scenarios)}")
    for endpoint_id in order:
        header = f"endpoint {_endpoint_label(graph, endpoint_id)}" if endpoint_id else "(без endpoint)"
        print(header)
        for scenario_id in groups[endpoint_id]:
            node = nodes.get(scenario_id)
            print(f"  scenario {node.label if node else scenario_id}{_origin_tag(node)}")
            path = graph.scenario_paths.get(scenario_id)
            if path is None:
                continue
            for io_id in path.io_calls:
                io_node = nodes.get(io_id)
                if io_node is None:
                    continue
                target = next(
                    (e.to_node for e in graph.edges if e.kind == "table" and e.from_node == io_id),
                    None,
                )
                target_node = nodes.get(target) if target else None
                suffix = f" -> {target_node.label}" if target_node else ""
                print(f"    io: {io_node.label}{suffix}{_origin_tag(io_node)}")
            if path.called_scenarios:
                names = []
                for called_id in path.called_scenarios:
                    called_node = nodes.get(called_id)
                    names.append(called_node.label if called_node else called_id)
                print(f"    calls: {', '.join(names)}")
    if not graph.scenarios:
        print("no scenarios: run studio scan first")
    return 0


def cmd_scaffold(args) -> int:
    root = _root(args)
    graph = store.read_graph(root)
    if graph is None:
        print("no graph: run studio scan first")
        return 2
    slog.start_run(root, "scaffold")
    slog.step(f"проект: {root}")

    ops = plan_scaffold(graph, root)
    models = collect_model_appends(graph)

    if args.dry_run:
        print("dry-run: операции без записи")
        for op in ops:
            print(f"  {op.action}: {op.path} ({op.node_id}){f' — {op.reason}' if op.reason else ''}")
        for node_id, class_name, tablename in models:
            print(f"  append_model: {class_name} -> models.py (table: {tablename})")
        return 0

    if not ops and not models:
        print("scaffold: нет операций")
        return 0

    created = sum(1 for op in ops if op.action == "create")
    updated = sum(1 for op in ops if op.action == "update_docstring")
    skipped = sum(1 for op in ops if op.action.startswith("skip"))
    print(f"Будут созданы файлы ({created}):")
    for op in ops:
        if op.action == "create":
            print(f"  {op.path}")
    print(f"Будут обновлены существующие скелеты ({updated}):")
    for op in ops:
        if op.action == "update_docstring":
            print(f"  {op.path}")
    print(f"Будут дописаны в models.py ({len(models)}):")
    for _, class_name, _ in models:
        print(f"  {class_name}")
    print(f"Пропущены ({skipped}):")
    for op in ops:
        if op.action.startswith("skip"):
            print(f"  {op.path} — {op.reason}")

    if not args.yes and not _confirm("Создать файлы? [y/N] "):
        print("отмена")
        return 1

    apply_scaffold(ops, graph, root)
    build_scenario_paths(graph)
    store.write_graph(root, graph)
    slog.step(f"scaffold: создано={created} обновлено={updated} пропущено={skipped}")
    print(f"scaffold: создано={created}, обновлено={updated}, пропущено={skipped}")
    return 0


def cmd_export(args) -> int:
    root = _root(args)
    graph = store.read_graph(root)
    if graph is None:
        print("no graph: run studio scan first")
        return 2
    slog.start_run(root, "export")
    if args.format == "json":
        content = to_json(graph)
    else:
        content = to_markdown(graph)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        print(f"export: {args.format} -> {out_path}")
    else:
        print(content, end="")
    return 0


def cmd_reset(args) -> int:
    root = _root(args)
    plan_path = store.studio_dir(root) / "plan.json"
    if not plan_path.is_file():
        print("reset: plan.json отсутствует")
        return 0
    if not args.yes and not _confirm("Удалить .studio/plan.json? [y/N] "):
        print("отмена")
        return 1
    plan_path.unlink()
    print("reset: plan.json удалён")
    return 0


def cmd_dev(args) -> int:
    root = _root(args)
    slog.start_run(root, f"dev {args.host}:{args.port}")
    slog.step(f"проект: {root}")
    if store.read_graph(root) is None:
        graph = scan(root)
        store.write_graph(root, graph)
        slog.step(f"граф построен: узлов={len(graph.nodes)} связей={len(graph.edges)}")
        print(f"scan: endpoints {len(graph.endpoints)}, scenarios {len(graph.scenarios)}")
    serve(root, host=args.host, port=args.port)
    return 0


def cmd_help(args) -> int:
    print("studio — карта проекта для AI-разработки")
    print()
    print("Путь одного запроса:")
    print("  HTTP → endpoint (fastbase) → scenario (core) → _db.<method> (io) → model (event-infra)")
    print()
    print("Команды:")
    print("  scan      [--project .]                            код → .studio/graph.json")
    print("  dev       [--project .] [--host 127.0.0.1] [--port 8777]")
    print("                                          поднять интерфейс (граф сканируется сам)")
    print("  list      [--project .]                            список сценариев с endpoint'ами и io")
    print("  scaffold  [--project .] [--dry-run] [--yes]        создать скелеты для узлов плана")
    print("  export    [--project .] [--format md|json] [--out P] экспорт графа")
    print("  reset     [--project .] [--yes]                     удалить .studio/plan.json")
    print("  help                                     этот экран")
    print()
    print("Подробности: studio <команда> --help")
    return 0


def _add_project(sub) -> None:
    sub.add_argument("--project", default=argparse.SUPPRESS, help="корень проекта (по умолчанию cwd)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="studio",
        description="pacage-studio — карта проекта для AI-разработки (схема графа v4)",
    )
    parser.add_argument("--project", default=".", help="корень проекта (по умолчанию cwd)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan", help="код → .studio/graph.json")
    _add_project(scan_p)
    scan_p.set_defaults(f=cmd_scan)

    dev_p = sub.add_parser("dev", help="поднять интерфейс")
    _add_project(dev_p)
    dev_p.add_argument("--host", default="127.0.0.1")
    dev_p.add_argument("--port", type=int, default=8777)
    dev_p.set_defaults(f=cmd_dev)

    list_p = sub.add_parser("list", help="список сценариев в терминале")
    _add_project(list_p)
    list_p.set_defaults(f=cmd_list)

    scaffold_p = sub.add_parser("scaffold", help="создать скелеты для узлов плана")
    _add_project(scaffold_p)
    scaffold_p.add_argument("--dry-run", action="store_true", help="печать операций без записи")
    scaffold_p.add_argument("--yes", "-y", action="store_true", help="без подтверждения")
    scaffold_p.set_defaults(f=cmd_scaffold)

    export_p = sub.add_parser("export", help="экспорт графа")
    _add_project(export_p)
    export_p.add_argument("--format", choices=["md", "json"], default="md")
    export_p.add_argument("--out", default=None, help="файл для записи (по умолчанию stdout)")
    export_p.set_defaults(f=cmd_export)

    reset_p = sub.add_parser("reset", help="удалить .studio/plan.json")
    _add_project(reset_p)
    reset_p.add_argument("--yes", "-y", action="store_true", help="без подтверждения")
    reset_p.set_defaults(f=cmd_reset)

    help_p = sub.add_parser("help", help="показать все команды")
    _add_project(help_p)
    help_p.set_defaults(f=cmd_help)
    return parser


def _safe_stdio() -> None:
    """cp1251-консоль Windows (пайп/редирект) не умеет '→' — help падал
    с UnicodeEncodeError. Кодировку не трогаем (кириллица остаётся верной),
    только непредставимые символы заменяем на '?', чтобы CLI не рвался."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if reconf is None:
            continue
        try:
            reconf(errors="replace")
        except (OSError, ValueError):
            pass  # поток не поддерживает reconfigure (например, замена в тестах)


def main(argv=None) -> int:
    _safe_stdio()
    args = build_parser().parse_args(argv)
    return args.f(args)


if __name__ == "__main__":
    sys.exit(main())
