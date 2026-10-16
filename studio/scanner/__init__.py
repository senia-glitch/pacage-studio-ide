"""AST-сканер: код → узлы, контракты, связи. Только чтение, код не трогает."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from studio.config import matches
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
                name = ""
                if isinstance(dec, ast.Name):
                    name = dec.id
                elif isinstance(dec, ast.Attribute):
                    name = dec.attr
                elif isinstance(dec, ast.Call):
                    f = dec.func
                    name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
                if name == "abstractmethod":
                    return True
    return False


def parse_contracts(source: str, layer: str, naming: dict) -> list[Contract]:
    """Находит контракты: абстрактные классы/методы + реализации. Фильтр — паттерны слоя."""
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
            # Вид класса: структурно (ABC) либо по паттерну абстракции.
            # Пустой/отсутствующий паттерн видом не управляет (иначе все классы слоя станут абстракциями).
            kind = "abstraction" if (abstract_cls or (abs_pat and matches(abs_pat, node.name))) else "implementation"
            pat = abs_pat if kind == "abstraction" else impl_pat
            if matches(pat, node.name):
                out.append(Contract(name=node.name, kind=kind, owner=None, line=node.lineno))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name.startswith("_"):
                        continue
                    is_abs = any(
                        (d.id if isinstance(d, ast.Name) else (d.attr if isinstance(d, ast.Attribute) else ""))
                        == "abstractmethod"
                        for d in item.decorator_list
                    )
                    # Метод: абстрактность — структурная (декоратор / ABC-класс),
                    # паттерны именуют классы, поэтому AST-абстракцию держим всегда.
                    if is_abs or abstract_cls:
                        out.append(Contract(name=item.name, kind="abstraction", owner=node.name, line=item.lineno))
                    elif matches(impl_pat, item.name):
                        out.append(Contract(name=item.name, kind="implementation", owner=node.name, line=item.lineno))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            if matches(impl_pat, node.name):
                out.append(Contract(name=node.name, kind="implementation", owner=None, line=node.lineno))
    # дедупликация по (name, kind, owner)
    seen, uniq = set(), []
    for c in out:
        key = (c.name, c.kind, c.owner)
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def infer_domain_layer(rel: Path, domains: list[dict], layers: list[dict]) -> tuple[str, str]:
    """Домен = папка верхнего уровня, слой = папка второго уровня (по ТЗ).

    Файлы в корне проекта и файлы сразу под доменом не имеют полного адреса —
    такие узлы уходят в shared/domain, чтобы не плодить домены из имён файлов.
    """
    parts = rel.parts
    if len(parts) == 1:
        return "shared", "domain"
    if len(parts) == 2:
        top = parts[0]
    else:
        top, second = parts[0], parts[1]
        domain_names = {d["name"] for d in domains}
        layer_names = {layer["name"] for layer in layers}
        domain_paths = {d.get("path", d["name"]): d["name"] for d in domains}
        layer_paths = {layer.get("path", layer["name"]): layer["name"] for layer in layers}
        domain = domain_paths.get(top, top if top in domain_names else top)
        layer = layer_paths.get(second, second if second in layer_names else second)
        return domain, layer
    domain_names = {d["name"] for d in domains}
    domain_paths = {d.get("path", d["name"]): d["name"] for d in domains}
    return domain_paths.get(top, top if top in domain_names else top), "domain"


def iter_project_files(root: Path) -> list[Path]:
    files = []
    for p in root.rglob("*.py"):
        if ".studio" in p.parts or "__pycache__" in p.parts or ".venv" in p.parts or "venv" in p.parts or ".git" in p.parts:
            continue
        files.append(p)
    return sorted(files)


def build_graph(project_root: Path, cfg: dict, old: Graph | None = None) -> tuple[Graph, dict[str, str]]:
    """Строит граф по коду. Возвращает (graph, hashes). UI-координаты переносятся из старого графа."""
    st = cfg.get("studio", cfg)
    domains = st.get("domains", [])
    layers = st.get("layers", [])
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
        domain, layer = infer_domain_layer(f.relative_to(project_root), domains, layers)
        contracts = parse_contracts(src, layer, naming)
        ui = old_pos.get(rel, NodeUI(x=float(len(nodes) % 6) * 260.0, y=float(len(nodes) // 6) * 200.0))
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
