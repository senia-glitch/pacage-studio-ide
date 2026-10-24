"""Схема графа студии v6.

Карта пути HTTP-запроса:

    endpoint (fastbase) → scenario (core-package) → io → model (event-infra)

Ровно пять видов узлов (§4.2 ТЗ). Периферийные элементы (методы, экземпляры,
классы, файлы) не являются отдельными видами — они входят в состав этих пяти.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from studio.diagnostics import Diagnostic

NodeKind = Literal["endpoint", "scenario", "io", "model", "test", "nested"]
EdgeKind = Literal["dispatch", "scenario_call", "port_call", "table", "tests", "uses", "depends"]

NODE_KINDS: tuple[str, ...] = ("endpoint", "scenario", "io", "model", "test", "nested")
EDGE_KINDS: tuple[str, ...] = (
    "dispatch",
    "scenario_call",
    "port_call",
    "table",
    "tests",
    "uses",
    "depends",
)

FLOW_EDGE_KINDS: frozenset[str] = frozenset(
    {"dispatch", "scenario_call", "port_call", "table", "uses", "depends"}
)
FLOW = FLOW_EDGE_KINDS


class EndpointDetails(BaseModel):
    http_method: str
    path: str
    function_name: str
    router_module: str = ""
    tags: list[str] = Field(default_factory=list)
    url_path: str = ""
    extra_routes: list[dict] = Field(default_factory=list)
    signature: str = ""
    doc: str = ""
    todo_downstream: int = 0
    dynamic_path: bool = False


class ScenarioDetails(BaseModel):
    name: str
    class_name: str
    module: str = ""
    dto: str | None = None
    response: str | None = None
    signature: str = ""
    doc: str = ""
    has_execute: bool = True


class IODetails(BaseModel):
    method: str
    table: str | None = None
    tables: list[str] = Field(default_factory=list)
    via_attr: str = "_db"
    scenario_class: str
    scenario_id: str
    ordinal: int = 0
    via_method: str | None = None


class ModelDetails(BaseModel):
    tablename: str
    table: str = ""
    class_name: str
    source_file: str
    fields: list[dict] = Field(default_factory=list)
    doc: str = ""


class TestDetails(BaseModel):
    test_name: str
    module: str = ""
    signature: str = ""
    doc: str = ""


class NestedDetails(BaseModel):
    role: str
    parent_id: str
    name: str
    symbols: list[str] = Field(default_factory=list)


class Node(BaseModel):
    id: str
    kind: NodeKind
    label: str
    file_path: str
    line: int = 1
    # Последняя строка элемента кода — для образцов в ТЗ (S12).
    end_line: int = 1
    details: dict = Field(default_factory=dict)
    description: str = ""
    acceptance: list[str] = Field(default_factory=list)
    domain: str = ""
    # Узел существует только в метаданных: кода с таким якорем ещё нет.
    planned: bool = False
    # Сколько планируемых узлов достижимо от эндпоинта вниз по потоку.
    todo_downstream: int = 0
    # Достижим из эндпоинта по направленным рёбрам потока (S4).
    complete: bool = True
    # sha1 исходных байтов файла на момент скана (S7).
    file_hash: str = ""
    # Причины жёлтой подсветки: [{"code", "message", "hint"}].
    notes: list[dict] = Field(default_factory=list)


class Edge(BaseModel):
    id: str
    from_node: str
    to_node: str
    kind: EdgeKind
    label: str = ""
    line: int = 1
    # Найдено анализом кода или только из metadata.json (если пара совпала — code).
    origin: Literal["code", "metadata"] = "code"
    notes: list[dict] = Field(default_factory=list)


class ScenarioPath(BaseModel):
    endpoint: str | None = None
    endpoints: list[str] = Field(default_factory=list)
    scenario: str
    io_calls: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    called_scenarios: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)


class Graph(BaseModel):
    version: int = 6
    project_root: str = ""
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    endpoints: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
    scenario_paths: dict[str, ScenarioPath] = Field(default_factory=dict)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    generated_at: str = ""
    fingerprint: str = ""

    def by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}

    def edges_from(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.from_node == node_id]

    def edges_to(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.to_node == node_id]

    def index(self) -> GraphIndex:
        return GraphIndex(self)


class GraphIndex:
    """Индексы графа, построенные один раз: id → узел, входящие/исходящие рёбра."""

    def __init__(self, graph: Graph):
        self.by_id: dict[str, Node] = {}
        self.out_edges: dict[str, list[Edge]] = defaultdict(list)
        self.in_edges: dict[str, list[Edge]] = defaultdict(list)
        for node in graph.nodes:
            self.by_id[node.id] = node
        for edge in graph.edges:
            self.out_edges[edge.from_node].append(edge)
            self.in_edges[edge.to_node].append(edge)

    def out(self, node_id: str) -> list[Edge]:
        return self.out_edges.get(node_id, [])

    def incoming(self, node_id: str) -> list[Edge]:
        return self.in_edges.get(node_id, [])

    def node(self, node_id: str) -> Node | None:
        return self.by_id.get(node_id)


class GraphBuilder:
    """Сборка графа через dict: добавление узла/ребра не дублирует записи."""

    def __init__(self, project_root: str = "") -> None:
        self.graph = Graph(project_root=project_root)
        self._nodes: dict[str, Node] = {}
        self._edges: dict[str, Edge] = {}

    def add_node(self, node: Node) -> Node:
        existing = self._nodes.get(node.id)
        if existing is not None:
            return existing
        self._nodes[node.id] = node
        return node

    def add_edge(self, edge: Edge) -> Edge | None:
        if edge.from_node == edge.to_node:
            return None
        if edge.id in self._edges:
            return self._edges[edge.id]
        self._edges[edge.id] = edge
        return edge

    def remove_edge(self, edge_id: str) -> None:
        self._edges.pop(edge_id, None)

    def remove_edges_between(self, source: str, target: str) -> None:
        for edge_id, edge in list(self._edges.items()):
            if edge.from_node == source and edge.to_node == target:
                self._edges.pop(edge_id, None)

    def has_edge(self, edge_id: str) -> bool:
        return edge_id in self._edges

    def node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def edges(self) -> list[Edge]:
        return list(self._edges.values())

    def snapshot(self) -> Graph:
        """Текущее состояние без сортировки/финализации — для резолверов."""
        return Graph(
            project_root=self.graph.project_root,
            nodes=list(self._nodes.values()),
            edges=list(self._edges.values()),
        )

    def build(self) -> Graph:
        graph = self.graph
        graph.nodes = sorted(self._nodes.values(), key=lambda n: n.id)
        graph.edges = sorted(self._edges.values(), key=lambda e: e.id)
        graph.endpoints = sorted(n.id for n in graph.nodes if n.kind == "endpoint")
        graph.scenarios = sorted(n.id for n in graph.nodes if n.kind == "scenario")
        return graph


def normalize_domain(value: str) -> str:
    """Домены через запятую без пустых значений и дублей, порядок сохраняется."""
    names: list[str] = []
    for part in str(value or "").replace("\n", ",").split(","):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    return ", ".join(names)


def split_domains(value: str) -> list[str]:
    return [part.strip() for part in normalize_domain(value).split(",") if part.strip()]


def node_anchor(node: Node) -> str:
    """Якорь узла: имя элемента кода (§4.4). У io — стабильный `#n` (S1)."""
    details = node.details or {}
    if node.kind == "endpoint":
        return str(details.get("function_name") or "")
    if node.kind in ("scenario", "model"):
        return str(details.get("class_name") or "")
    if node.kind == "test":
        return str(details.get("test_name") or "")
    if node.kind == "io":
        scenario_class = str(details.get("scenario_class") or "")
        method = str(details.get("method") or "")
        base = f"{scenario_class}._db.{method}"
        ordinal = int(details.get("ordinal") or 0)
        if ordinal > 0:
            return f"{base}#{ordinal}"
        return base
    if node.kind == "nested":
        return str(details.get("name") or node.label)
    return ""


__all__ = [
    "EDGE_KINDS",
    "FLOW",
    "FLOW_EDGE_KINDS",
    "Edge",
    "EdgeKind",
    "EndpointDetails",
    "Graph",
    "GraphBuilder",
    "GraphIndex",
    "IODetails",
    "ModelDetails",
    "NODE_KINDS",
    "NestedDetails",
    "Node",
    "NodeKind",
    "ScenarioDetails",
    "ScenarioPath",
    "TestDetails",
    "node_anchor",
    "normalize_domain",
    "split_domains",
]

