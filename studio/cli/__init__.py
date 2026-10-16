"""CLI: studio init / scan / dev / save / validate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from studio import config as config_mod
from studio import log as slog
from studio import store
from studio import writer as writer_mod
from studio.scanner import build_graph
from studio.validator import validate_graph


def _root(args) -> Path:
    return Path(args.project or ".").resolve()


def cmd_init(args) -> int:
    root = _root(args)
    slog.start_run(root, "init")
    root.mkdir(parents=True, exist_ok=True)
    store.ensure_layout(root)
    cfg_path = store.studio_dir(root) / store.CONFIG_NAME
    if cfg_path.exists() and not args.force:
        slog.step(f"config уже есть, без --force не трогаю: {cfg_path}")
        print(f"exists: {cfg_path} (overwrite: --force)")
        return 0
    store.write_config(root, config_mod.default_config())
    slog.step(f"записан дефолтный config: {cfg_path}")
    print(f"created: {cfg_path}")
    print("next: studio scan -> studio dev")
    return 0


def cmd_scan(args) -> int:
    root = _root(args)
    slog.start_run(root, "scan")
    cfg = store.read_config(root)
    st = cfg.get("studio", cfg)
    slog.step(f"config: доменов={len(st.get('domains', []))}, слоёв={len(st.get('layers', []))}, "
              f"разрешённых направлений={len(st.get('rules', {}).get('allowed', []))}")
    old = store.read_graph(root)
    slog.step("старый граф: " + ("найден, сохраняю позиции узлов" if old else "нет, строю с нуля"))
    graph, hashes = build_graph(root, cfg, old=old)
    for n in graph.nodes:
        slog.step(f"файл {n.path}: домен={n.domain} слой={n.layer} контрактов={len(n.contracts)} "
                  + ",".join(f"{c.name}:{c.kind}" for c in n.contracts))
    vs = {v.link_id for v in validate_graph(graph, cfg)}
    for link in graph.links:
        link.violation = link.id in vs
        slog.step(f"связь {link.from_node} --{link.contract}--> {link.to_node}"
                  + (" [НАРУШЕНИЕ]" if link.violation else ""))
    store.write_graph(root, graph)
    store.write_hashes(root, hashes)
    slog.step(f"итог: узлов={len(graph.nodes)} связей={len(graph.links)} нарушений={len(vs)}; "
              f"граф записан, файлы проекта не тронуты")
    print(f"scan: nodes {len(graph.nodes)}, links {len(graph.links)}, violations {len(vs)}")
    print(f"graph: {store.studio_dir(root) / store.GRAPH_NAME}")
    return 0


def cmd_validate(args) -> int:
    root = _root(args)
    slog.start_run(root, "validate")
    graph = store.read_graph(root)
    if graph is None:
        slog.step("графа нет — сначала нужен scan")
        print("no graph: run studio scan first")
        return 2
    slog.step(f"проверяю {len(graph.links)} связей по правилам из config.toml")
    vs = validate_graph(graph, store.read_config(root))
    for v in vs:
        slog.step(f"НАРУШЕНИЕ {v.from_node} [{v.from_layer}] --{v.contract}--> {v.to_node} [{v.to_layer}]")
    if not vs:
        slog.step("нарушений нет")
        print("ok: no clean-architecture violations")
        return 0
    print(f"violations: {len(vs)}")
    for v in vs:
        print(f"  [x] {v.from_node} [{v.from_layer}] --{v.contract}--> {v.to_node} [{v.to_layer}]")
    return 1


def cmd_save(args) -> int:
    root = _root(args)
    slog.start_run(root, "save")
    graph = store.read_graph(root)
    if graph is None:
        slog.step("графа нет — сначала нужен scan")
        print("no graph: run studio scan first")
        return 2
    cfg = store.read_config(root)
    slog.step("считаю операции (граф -> код), файлы пока не тронуты")
    ops, vs, errs = writer_mod.plan(root, graph, cfg)
    for op in ops:
        slog.step(f"план: {op.action} {op.path}")
    for v in vs:
        slog.step(f"нарушение (записи не мешает): {v.link_id}")
    if errs:
        for e in errs:
            slog.step(f"СИНТАКСИС: {e} — запись отменена")
        print("syntax errors, write cancelled:")
        for e in errs:
            print("  [x] " + e)
        return 1
    if args.diff_only:
        slog.step("режим --diff-only: только показываю diff, не пишу")
        print(writer_mod.diff_text(ops))
        return 0
    st = cfg.get("studio", cfg).get("save", {})
    if (st.get("show_diff") or args.show_diff) and not args.yes:
        mode = st.get("diff_mode", "on_danger")
        if mode == "always" or (mode == "on_danger" and vs):
            slog.step("показываю diff и жду подтверждения")
            print(writer_mod.diff_text(ops))
            print("---")
            ans = input("Write changes to code? [y/N] ").strip().lower()
            if ans not in ("y", "yes", "д", "да"):
                slog.step("пользователь отменил, файлы не тронуты")
                print("cancelled, files untouched")
                return 0
    ops2, _ = writer_mod.apply(root, graph, cfg, confirm=True)
    for op in ops2:
        slog.step(f"записано: {op.action} {op.path}")
    slog.step(f"итог: операций={len(ops2)}, граф и хеши обновлены")
    print(f"save: applied {len(ops2)} ops")
    for op in ops2:
        print(f"  {op.action}: {op.path}")
    return 0


def cmd_dev(args) -> int:
    from studio.server import serve
    serve(_root(args), host=args.host, port=args.port)
    return 0


def cmd_help(args) -> int:
    print("studio: код -> граф -> код. Офлайн dev-инструмент, проект не запускается.")
    print("")
    print("Цикл: scan (код -> граф) -> правки в UI -> save (граф -> код)")
    print("")
    print("Команды:")
    print("  init [--force]                         создать .studio/ с заготовкой config.toml")
    print("  scan                                   собрать граф по коду (код не трогает)")
    print("  validate                               проверить чистую архитектуру")
    print("  save [-y] [--show-diff] [--diff-only]  записать граф в код")
    print("  dev [--host] [--port]                  поднять интерфейс (по умолчанию :8777)")
    print("  help                                   этот экран")
    print("")
    print("Подробности: studio <команда> --help")
    print("Конфиг: шестеренка в UI или .studio/config.toml (подхватывается без перезапуска)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="studio", description="studio: код -> граф -> код (офлайн, без рантайма)")
    p.add_argument("--project", default=".", help="корень проекта (по умолчанию cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="создать .studio/ с заготовкой config.toml")
    i.add_argument("--force", action="store_true")
    i.set_defaults(f=cmd_init)
    s = sub.add_parser("scan", help="собрать граф по коду")
    s.set_defaults(f=cmd_scan)
    v = sub.add_parser("validate", help="проверить чистое архитектурное соответствие")
    v.set_defaults(f=cmd_validate)
    sv = sub.add_parser("save", help="применить изменения из графа в код")
    sv.add_argument("--yes", "-y", action="store_true", help="не спрашивать подтверждение diff")
    sv.add_argument("--show-diff", action="store_true", help="показать diff перед записью")
    sv.add_argument("--diff-only", action="store_true", help="только показать diff, не писать")
    sv.set_defaults(f=cmd_save)
    d = sub.add_parser("dev", help="поднять UI")
    d.add_argument("--host", default="127.0.0.1")
    d.add_argument("--port", type=int, default=8777)
    d.set_defaults(f=cmd_dev)
    h = sub.add_parser("help", help="показать все возможности")
    h.set_defaults(f=cmd_help)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.f(args)


if __name__ == "__main__":
    sys.exit(main())
