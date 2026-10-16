"""Обратная запись: граф → файлы. До «Сохранить» файлы не тронуты."""

from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass
from pathlib import Path

from studio import store
from studio.metamodel import Graph
from studio.validator import check_syntax, validate_graph


@dataclass
class FileOp:
    path: str  # относительный posix-путь
    action: str  # create | modify | delete
    old: str = ""
    new: str = ""


def file_template(domain: str, layer: str, contracts: list[str]) -> str:
    lines = [f'"""Domain: {domain} | Layer: {layer} (создано через studio)."""', "", ""]
    if not contracts:
        lines += ["# TODO: добавьте контракты", ""]
    for c in contracts:
        if c[:1].isupper():
            lines += [f"class {c}:", f'    """Контракт {c}."""', "", "    pass", "", ""]
        else:
            lines += [f"def {c}(*args, **kwargs):", f'    """Контракт {c} (создано studio)."""', "    raise NotImplementedError", "", ""]
    return "\n".join(lines)


def _top_defs(tree: ast.Module) -> tuple[set[str], set[str], dict[str, set[str]], dict[str, ast.ClassDef]]:
    funcs, classes, methods, cls_nodes = set(), set(), {}, {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.add(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.add(node.name)
            cls_nodes[node.name] = node
            methods[node.name] = {i.name for i in node.body if isinstance(i, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return funcs, classes, methods, cls_nodes


def ensure_contract_stubs(base: str, contracts: list) -> str:
    """Дописывает стабы контрактов из графа, которых нет в коде.

    Закрывает цикл edit→save для правок инспектора без связей: добавил контракт
    в узел — save создаст под него заготовку в файле. Существующий код не трогает.
    Возвращает новый текст (или исходный, если всё на месте / не парсится).
    """
    try:
        tree = ast.parse(base)
    except SyntaxError:
        return base
    funcs, classes, methods, cls_nodes = _top_defs(tree)
    taken = funcs | classes
    insertions: list[tuple[int, list[str]]] = []  # (индекс вставки в lines, строки)
    need_abc = False

    def want_abc_import() -> bool:
        return "from abc import" not in base and "import abc" not in base

    for c in contracts:
        name = getattr(c, "name", "")
        kind = getattr(c, "kind", "implementation")
        owner = getattr(c, "owner", None)
        if not name or not name.isidentifier():
            continue
        if owner:
            if owner in cls_nodes and name in methods.get(owner, set()):
                continue
            body = ["        raise NotImplementedError"]
            if kind == "abstraction":
                stub = ["    @abstractmethod", f"    def {name}(self, *args, **kwargs):"] + body
                need_abc = True
            else:
                stub = [f"    def {name}(self, *args, **kwargs):"] + body
            if owner in cls_nodes:
                insertions.append((cls_nodes[owner].end_lineno, [""] + stub + [""]))
                # класс уже учтён, повторный поиск не нужен
                methods.setdefault(owner, set()).add(name)
            elif owner not in taken and owner.isidentifier():
                base_cls = "(ABC)" if kind == "abstraction" else ""
                if base_cls:
                    need_abc = True
                insertions.append((10**9, ["", "", f"class {owner}{base_cls}:",
                                           f'    """Контракт {owner} (создано studio)."""'] + stub + [""]))
                taken.add(owner)
            continue
        if name in taken:
            continue
        if kind == "abstraction":
            need_abc = True
            insertions.append((10**9, ["", "", f"class {name}(ABC):",
                                       f'    """Контракт {name} (создано studio)."""', "", "    pass", ""]))
        elif name[:1].isupper():
            insertions.append((10**9, ["", "", f"class {name}:",
                                       f'    """Контракт {name} (создано studio)."""', "", "    pass", ""]))
        else:
            insertions.append((10**9, ["", "", f"def {name}(*args, **kwargs):",
                                       f'    """Стаб, создан studio для контракта {name}."""',
                                       "    raise NotImplementedError", ""]))
        taken.add(name)

    if not insertions:
        return base
    lines = base.splitlines()
    # Вставки с больших номеров строк к меньшим, чтобы номера не съезжали;
    # в одной точке — в обратном порядке, чтобы итог сохранил порядок контрактов.
    ordered = sorted(enumerate(insertions), key=lambda p: (-p[1][0], -p[0]))
    for _, (idx, stub) in ordered:
        pos = len(lines) if idx >= 10**9 else min(idx, len(lines))
        lines[pos:pos] = stub
    if need_abc and want_abc_import():
        anchor = 0
        if (tree.body and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str)):
            anchor = tree.body[0].end_lineno
        lines[anchor:anchor] = ["from abc import ABC, abstractmethod", ""]
    out = "\n".join(lines)
    if base.endswith("\n"):
        out += "\n"
    return out


def collect_ops(project_root: Path, graph: Graph) -> list[FileOp]:
    from studio.metamodel import Contract as ContractModel
    ops: list[FileOp] = []
    # path -> [old_text, base_new_text, ensure_contracts, action_hint]
    pending: dict[str, list] = {}
    for n in graph.nodes:
        target = project_root / Path(*n.id.split("/"))
        exists = target.exists()
        if n.status == "deleted":
            if exists:
                try:
                    old = target.read_text(encoding="utf-8")
                except OSError:
                    old = ""
                ops.append(FileOp(n.id, "delete", old=old, new=""))
            continue
        if n.code is not None:
            old = target.read_text(encoding="utf-8") if exists else ""
            pending[n.id] = [old, n.code, list(n.contracts), "modify" if exists else "create"]
        elif n.status == "added" and not exists:
            contracts = [c.name for c in n.contracts if c.name.isidentifier()]
            pending[n.id] = ["", file_template(n.domain, n.layer, contracts), list(n.contracts), "create"]
        elif exists:
            try:
                old = target.read_text(encoding="utf-8")
            except OSError:
                continue
            pending[n.id] = [old, old, list(n.contracts), "modify"]
    # Новые связи: контракт должен существовать в узле-реализации — иначе стаб.
    by_id = graph.by_id()
    for link in graph.links:
        if link.status != "added" or link.weak:
            continue
        src = by_id.get(link.from_node)
        if src is None or src.status == "deleted" or link.from_node not in pending:
            continue
        have = {c.name for c in pending[link.from_node][2]}
        if link.contract not in have and link.contract.isidentifier():
            pending[link.from_node][2].append(ContractModel(name=link.contract, kind="implementation"))
    for path, (old, base, contracts, hint) in pending.items():
        new = ensure_contract_stubs(base, contracts)
        if new != old:
            action = hint
            if action == "modify" and not old and not (project_root / Path(*path.split("/"))).exists():
                action = "create"  # файл пропал после scan — создаём заново
            ops.append(FileOp(path, action, old=old, new=new))
    return ops


def diff_text(ops: list[FileOp]) -> str:
    chunks = []
    for op in ops:
        if op.action == "create":
            d = "".join(difflib.unified_diff([], op.new.splitlines(keepends=True), fromfile=f"a/{op.path}", tofile=f"b/{op.path}"))
        elif op.action == "delete":
            d = "".join(difflib.unified_diff(op.old.splitlines(keepends=True), [], fromfile=f"a/{op.path}", tofile=f"b/{op.path}"))
        else:
            d = "".join(difflib.unified_diff(op.old.splitlines(keepends=True), op.new.splitlines(keepends=True), fromfile=f"a/{op.path}", tofile=f"b/{op.path}"))
        chunks.append(f"=== {op.action}: {op.path} ===\n{d if d else '(нет текстовых отличий)'}")
    return "\n".join(chunks) if chunks else "Изменений нет."


def plan(project_root: Path, graph: Graph, cfg: dict) -> tuple[list[FileOp], list, list[str]]:
    """Возвращает (ops, violations, syntax_errors). Ничего не пишет."""
    violations = validate_graph(graph, cfg)
    errors: list[str] = []
    ops = collect_ops(project_root, graph)
    for op in ops:
        if op.action in ("create", "modify"):
            err = check_syntax(op.new)
            if err:
                errors.append(f"{op.path}: {err}")
    return ops, violations, errors


def apply(project_root: Path, graph: Graph, cfg: dict, confirm: bool = False) -> tuple[list[FileOp], str]:
    """Прогоняет валидатор, при необходимости показывает diff, пишет файлы, обновляет graph.json и хеши."""
    from studio.scanner import build_graph
    ops, violations, errors = plan(project_root, graph, cfg)
    if errors:
        raise RuntimeError("Синтаксические ошибки, запись отменена:\n" + "\n".join(errors))
    st = cfg.get("studio", cfg)
    save_cfg = st.get("save", {})
    show = bool(save_cfg.get("show_diff", False))
    mode = save_cfg.get("diff_mode", "on_danger")
    need_confirm = confirm or show and (mode == "always" or (mode == "on_danger" and violations))
    if need_confirm:
        # CLI/сервер решают сами: возвращаем diff без записи, если confirm=False а показ обязателен
        if not confirm:
            return ops, diff_text(ops)
    for op in ops:
        t = project_root / Path(*op.path.split("/"))
        if op.action == "delete":
            try:
                t.unlink()
            except OSError:
                pass
        else:
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(op.new, encoding="utf-8")
    # сбросить статусы и code-буфер, пересканировать для актуального графа (сохранив UI)
    fresh, hashes = build_graph(project_root, cfg, old=graph)
    store.write_graph(project_root, fresh)
    store.write_hashes(project_root, hashes)
    return ops, diff_text(ops)
