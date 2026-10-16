"""AST-сканер: код → узлы, контракты, связи. Только чтение, код не трогает.

Адресация под стек event-infra / core-package / fastbase:
- слой: сначала маркеры содержимого (decorator/base_class/module_attr),
  затем пути из конфига (длиннейший матч), иначе "unknown";
- домен: сегмент пути из конфига, иначе default_domain.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from studio.config import UNKNOWN_LAYER, matches
from studio.metamodel import Contract, Graph, Link, Node, NodeProperties, NodeUI


def _is_abstract_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in ("ABC", "ABCMeta"):
            return True
        if isinstance(base, ast.Attribute) and base.attr in ("ABC", "ABCMeta"):
            return True
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in item.decorator_list:
                if _expr_name(dec) == "abstractmethod":
                    return True
    return False


def _expr_name(e: ast.expr | None) -> str:
    """Имя из выражения: Name.id / Attribute.attr / Call.func (для декораторов и баз)."""
    if isinstance(e, ast.Name):
        return e.id
    if isinstance(e, ast.Attribute):
        return e.attr
    if isinstance(e, ast.Call):
        return _expr_name(e.func)
    return ""


def _has_base(node: ast.ClassDef, base: str) -> bool:
    return any(_expr_name(b) == base for b in node.bases)


def _tablename(node: ast.ClassDef) -> str | None:
    """Значение __tablename__ = "..." в теле класса, если есть."""
    for item in node.body:
        targets = []
        value = None
        if isinstance(item, ast.Assign):
            targets, value = item.targets, item.value
        elif isinstance(item, ast.AnnAssign):
            targets, value = [item.target], item.value
        else:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in targets):
                return value.value
    return None


def _module_signals(tree: ast.Module) -> tuple[set[str], set[str], set[str]]:
    """Один проход по AST: (декораторы, базовые классы, атрибуты модуля)."""
    decorators, bases, attrs = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in getattr(node, "decorator_list", []):
                name = _expr_name(dec)
                if name:
                    decorators.add(name)
        if isinstance(node, ast.ClassDef):
            for b in node.bases:
                name = _expr_name(b)
                if name:
                    bases.add(name)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                attrs.add(t.id)
    return decorators, bases, attrs


def _layer_by_markers(tree: ast.Module, layers: list[dict], markers: dict) -> str | None:
    """Слой по содержимому файла. Первый совпавший (по order) выигрывает."""
    if not markers:
        return None
    decorators, bases, attrs = _module_signals(tree)
    for layer in sorted(layers, key=lambda l: l.get("order", 0)):
        kinds = markers.get(layer["name"], {})
        if not isinstance(kinds, dict):
            continue
        if kinds.get("decorator") and kinds["decorator"] in decorators:
            return layer["name"]
        if kinds.get("base_class") and kinds["base_class"] in bases:
            return layer["name"]
        if kinds.get("module_attr") and kinds["module_attr"] in attrs:
            return layer["name"]
    return None


def _layer_by_path(posix: str, layers: list[dict]) -> str | None:
    """Слой по путям из конфига. Точное совпадение или префикс с '/'; побеждает длиннейший."""
    best, best_len = None, -1
    for layer in layers:
        paths = layer.get("paths", [layer.get("path", layer.get("name", ""))])
        for p in paths:
            if not p:
                continue
            if posix == p or posix.startswith(p + "/"):
                if len(p) > best_len:
                    best, best_len = layer["name"], len(p)
    return best


def parse_contracts(source: str, layer: str, naming: dict) -> list[Contract]:
    """Находит контракты: абстрактные классы/методы + реализации. Фильтр — паттерны слоя.

    Дополнительно (стек): execute у наследников BaseScenario, публичные функции
    модуля в representation и классы SQLModel в infrastructure — всегда контракты.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    rules = naming.get(layer, {}) if isinstance(naming, dict) else {}
    abs_pat = rules.get("abstraction", "") if isinstance(rules, dict) else ""
    impl_pat = rules.get("implementation", "") if isinstance(rules, dict) else ""
    out: list[Contract] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            abstract_cls = _is_abstract_class(node)
            if layer == "infrastructure" and _has_base(node, "SQLModel"):
                # SQLModel-модель: реализация, имя таблицы — в owner
                out.append(Contract(name=node.name, kind="implementation", owner=_tablename(node), line=node.lineno))
                continue
            # Вид класса: структурно (ABC) либо по паттерну абстракции.
            # Пустой/отсутствующий паттерн видом не управляет (иначе все классы слоя станут абстракциями).
            kind = "abstraction" if (abstract_cls or (abs_pat and matches(abs_pat, node.name))) else "implementation"
            pat = abs_pat if kind == "abstraction" else impl_pat
            if matches(pat, node.name):
                out.append(Contract(name=node.name, kind=kind, owner=None, line=node.lineno))
            scenario_cls = layer == "logic" and _has_base(node, "BaseScenario")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name.startswith("_"):
                        continue
                    is_abs = any(_expr_name(d) == "abstractmethod" for d in item.decorator_list)
                    # Метод: абстрактность — структурная (декоратор / ABC-класс),
                    # паттерны именуют классы, поэтому AST-абстракцию держим всегда.
                    if is_abs or abstract_cls:
                        out.append(Contract(name=item.name, kind="abstraction", owner=node.name, line=item.lineno))
                    elif scenario_cls and item.name == "execute":
                        # execute сценария — реализация по определению слоя
                        out.append(Contract(name=item.name, kind="implementation", owner=node.name, line=item.lineno))
                    elif matches(impl_pat, item.name):
                        out.append(Contract(name=item.name, kind="implementation", owner=node.name, line=item.lineno))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            # публичные функции representation — реализации по определению слоя
            if layer == "representation" or matches(impl_pat, node.name):
                out.append(Contract(name=node.name, kind="implementation", owner=None, line=node.lineno))
    # дедупликация по (name, kind, owner)
    seen, uniq = set(), []
    for c in out:
        key = (c.name, c.kind, c.owner)
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def infer_domain_layer(rel: Path, cfg: dict, source: str | None = None) -> tuple[str, str]:
    """Адрес узла (домен, слой).

    Домен: первый совпавший path из domains как сегмент пути, иначе default_domain.
    Слой: маркеры содержимого (нужен source) → пути из конфига (длиннейший матч) → "unknown".
    """
    st = cfg.get("studio", cfg) if isinstance(cfg, dict) else {}
    domains = st.get("domains", []) if isinstance(st, dict) else []
    layers = st.get("layers", []) if isinstance(st, dict) else []
    markers = st.get("markers", {}) if isinstance(st, dict) else {}
    default_domain = (st.get("default_domain") or "app") if isinstance(st, dict) else "app"
    domain = default_domain
    for d in domains:
        if isinstance(d, dict) and d.get("path") and d["path"] in rel.parts:
            domain = d["name"]
            break
    posix = rel.as_posix()
    layer = None
    if source is not None:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            tree = None
        if tree is not None:
            layer = _layer_by_markers(tree, layers, markers if isinstance(markers, dict) else {})
    if layer is None:
        layer = _layer_by_path(posix, layers)
    return domain, layer or UNKNOWN_LAYER


def iter_project_files(root: Path) -> list[Path]:
    files = []
    for p in root.rglob("*.py"):
        parts = p.parts
        if ".studio" in parts or "__pycache__" in parts or ".venv" in parts or "venv" in parts or ".git" in parts:
            continue
        if "alembic" in parts and "versions" in parts:
            continue  # автогенерации миграций в графе не место
        files.append(p)
    return sorted(files)


def build_graph(project_root: Path, cfg: dict, old: Graph | None = None) -> tuple[Graph, dict[str, str]]:
    """Строит граф по коду. Возвращает (graph, hashes). UI-координаты переносятся из старого графа."""
    st = cfg.get("studio", cfg)
    naming = st.get("naming", {})
    old_pos = {n.id: n.ui for n in (old.nodes if old else [])}
    old_props = {n.id: n.properties for n in (old.nodes if old else []) if n.status != "deleted"}
    nodes: list[Node] = []
    hashes: dict[str, str] = {}
    for f in iter_project_files(project_root):
        rel = f.relative_to(project_root).as_posix()
        try:
            src = f.read_text(encoding="utf-8")
        except OSError:
            continue
        hashes[rel] = hashlib.sha256(src.encode("utf-8")).hexdigest()
        domain, layer = infer_domain_layer(f.relative_to(project_root), cfg, source=src)
        contracts = parse_contracts(src, layer, naming)
        ui = old_pos.get(rel, NodeUI(x=float(len(nodes) % 6) * 260.0, y=float(len(nodes) % 6) * 200.0))
        # Имя/описание из инспектора живут только в графе — переживают рескан.
        props = old_props.get(rel, NodeProperties(name=Path(rel).stem))
        nodes.append(Node(id=rel, path=rel, domain=domain, layer=layer, contracts=contracts,
                          properties=props, ui=ui))
    # Связи: совпадение имён контрактов между узлами. Реализация → абстракция.
    providers: dict[str, list[str]] = {}  # contract -> [node ids with abstraction]
    implementors: dict[str, list[str]] = {}
    concretes: dict[str, list[str]] = {}
    for n in nodes:
        for c in n.contracts:
            if c.kind == "abstraction":
                providers.setdefault(c.name, []).append(n.id)
            else:
                implementors.setdefault(c.name, []).append(n.id)
                concretes.setdefault(c.name, []).append(n.id)
    links: list[Link] = []
    for name, impls in implementors.items():
        provs = providers.get(name, [])
        if provs:
            for i in impls:
                for p in provs:
                    if i == p:
                        continue
                    links.append(Link(id=f"{i}::{name}>>{p}", contract=name, from_node=i, to_node=p))
        elif len(concretes.get(name, [])) > 1:
            # weak-связь: имя совпало, абстракции нет (цепочка по порядку файлов)
            ids = sorted(concretes[name])
            for a, b in zip(ids, ids[1:]):
                links.append(Link(id=f"{a}::{name}>>{b}", contract=name, from_node=a, to_node=b, weak=True))
    links.sort(key=lambda link: link.id)
    return Graph(nodes=sorted(nodes, key=lambda n: n.id), links=links), hashes
