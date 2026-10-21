"""Схема графа студии v5.

Карта пути HTTP-запроса:

    endpoint (fastbase) → scenario (core-package) → io → model (event-infra)

Ровно пять видов узлов (§4.2 ТЗ). Периферийные элементы (методы, экземпляры,
классы, файлы) не являются отдельными видами — они входят в состав этих пяти.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

NodeKind = Literal["endpoint", "scenario", "io", "model", "test"]
EdgeKind = Literal["dispatch", "scenario_call", "port_call", "table", "tests", "uses", "depends"]

NODE_KINDS: tuple[str, ...] = ("endpoint", "scenario", "io", "model", "test")
EDGE_KINDS: tuple[str, ...] = (
    "dispatch",
    "scenario_call",
    "port_call",
    "table",
    "tests",
    "uses",
    "depends",
)


class EndpointDetails(BaseModel):
    http_method: str
    path: str
    function_name: str
    router_module: str = ""
    tags: list[str] = Field(default_factory=list)
    url_path: str = ""


class ScenarioDetails(BaseModel):
    name: str
    class_name: str
    module: str = ""
    dto: str | None = None
    response: str | None = None


class IODetails(BaseModel):
    method: str
    table: str | None = None
    via_attr: str = "_db"
    scenario_class: str
    scenario_id: str


class ModelDetails(BaseModel):
    tablename: str
    class_name: str
    source_file: str


class TestDetails(BaseModel):
    test_name: str
    module: str = ""


class Node(BaseModel):
    id: str
    kind: NodeKind
    label: str
    file_path: str
    line: int = 1
    details: dict = Field(default_factory=dict)
    description: str = ""
    acceptance: list[str] = Field(default_factory=list)
    domain: str = ""
    # Периферийные узлы: методы/классы/файлы внутри узла (§4.3).
    peripherals: list[dict] = Field(default_factory=list)
    # Узел существует только в метаданных: кода с таким якорем ещё нет.
    planned: bool = False


class Edge(BaseModel):
    id: str
    from_node: str
    to_node: str
    kind: EdgeKind
    label: str = ""
    line: int = 1


class ScenarioPath(BaseModel):
    endpoint: str | None = None
    scenario: str
    io_calls: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    called_scenarios: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)


class Graph(BaseModel):
    version: int = 5
    project_root: str = ""
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    endpoints: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
    scenario_paths: dict[str, ScenarioPath] = Field(default_factory=dict)

    def by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}

    def edges_from(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.from_node == node_id]

    def edges_to(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.to_node == node_id]


def normalize_domain(value: str) -> str:
    """Домены через запятую без пустых значений и дублей, порядок сохраняется."""
    names: list[str] = []
    for part in str(value or "").replace("\n", ",").split(","):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    return ", ".join(names)


def node_anchor(node: Node) -> str:
    """Якорь узла: имя элемента кода (§4.4)."""
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
        return base if node.line <= 0 else f"{base}:{node.line}"
    return ""
