"""CLI студии.

Запуск без команды (`studio`) автоматически сканирует проект и открывает
интерфейс (§2 ТЗ).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from studio import log as slog
from studio import store
from studio.scanner import scan
from studio.server import serve


def _root(args) -> Path:
    return Path(getattr(args, "project", None) or ".").resolve()


def _count(graph, kind: str) -> int:
    return sum(1 for n in graph.nodes if n.kind == kind)


def cmd_scan(args) -> int:
    root = _root(args)
    slog.start_run(root, "scan")
    slog.step(f"проект: {root}")
    graph = scan(root)
    path = store.write_graph(root, graph)
    slog.step(
        f"итог: endpoints={len(graph.endpoints)} scenarios={len(graph.scenarios)} "
        f"io={_count(graph, 'io')} models={_count(graph, 'model')} "
        f"tests={_count(graph, 'test')} связей={len(graph.edges)}"
    )
    print(
        f"scan: endpoints {len(graph.endpoints)}, scenarios {len(graph.scenarios)}, "
        f"io {_count(graph, 'io')}, models {_count(graph, 'model')}, "
        f"tests {_count(graph, 'test')}"
    )
    print(f"graph: {path}")
    return 0


def _endpoint_label(graph, endpoint_id: str) -> str:
    node = graph.by_id().get(endpoint_id)
    if node is None:
        return endpoint_id
    details = node.details
    method = str(details.get("http_method", "")).upper()
    path = details.get("url_path") or details.get("path") or "/"
    return f"{method} {path}".strip()


def cmd_list(args) -> int:
    root = _root(args)
    graph = store.read_graph(root)
    if graph is None:
        graph = scan(root)
        store.write_graph(root, graph)
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
            print(f"  scenario {node.label if node else scenario_id}")
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
                print(f"    io: {io_node.label}{suffix}")
            if path.called_scenarios:
                names = [
                    nodes[called_id].label if called_id in nodes else called_id
                    for called_id in path.called_scenarios
                ]
                print(f"    calls: {', '.join(names)}")
    if not graph.scenarios:
        print("no scenarios: run studio scan first")
    return 0


def cmd_dev(args) -> int:
    root = _root(args)
    slog.start_run(root, f"dev {args.host}:{args.port}")
    slog.step(f"проект: {root}")
    serve(root, host=args.host, port=args.port)
    return 0


def cmd_help(args) -> int:
    print("studio — карта пути HTTP-запроса")
    print()
    print("  HTTP → endpoint (fastbase) → scenario (core) → io → model (event-infra)")
    print()
    print("Команды:")
    print("  dev   [--project .] [--host 127.0.0.1] [--port 8777]")
    print("                                       автоскан и интерфейс")
    print("  scan  [--project .]                  код → .studio/graph.json")
    print("  list  [--project .]                  сценарии в терминале")
    print("  help                                 этот экран")
    print()
    print("Без команды studio запускает dev.")
    return 0


def _add_project(sub) -> None:
    sub.add_argument("--project", default=argparse.SUPPRESS, help="корень проекта (по умолчанию cwd)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="studio",
        description="pacage-studio — карта пути HTTP-запроса",
    )
    parser.add_argument("--project", default=".", help="корень проекта (по умолчанию cwd)")
    sub = parser.add_subparsers(dest="cmd")

    dev_p = sub.add_parser("dev", help="автоскан и интерфейс")
    _add_project(dev_p)
    dev_p.add_argument("--host", default="127.0.0.1")
    dev_p.add_argument("--port", type=int, default=8777)
    dev_p.set_defaults(f=cmd_dev)

    scan_p = sub.add_parser("scan", help="код → .studio/graph.json")
    _add_project(scan_p)
    scan_p.set_defaults(f=cmd_scan)

    list_p = sub.add_parser("list", help="список сценариев в терминале")
    _add_project(list_p)
    list_p.set_defaults(f=cmd_list)

    help_p = sub.add_parser("help", help="показать все команды")
    _add_project(help_p)
    help_p.set_defaults(f=cmd_help)
    return parser


def _safe_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if reconf is None:
            continue
        try:
            reconf(errors="replace")
        except (OSError, ValueError):
            pass


def main(argv=None) -> int:
    _safe_stdio()
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    if not argv:
        argv = ["dev"]
    args = build_parser().parse_args(argv)
    if not hasattr(args, "f"):
        # `studio --project X` без команды = запуск интерфейса.
        args = build_parser().parse_args(["dev", *argv])
    return args.f(args)


if __name__ == "__main__":
    sys.exit(main())
