"""Pydantic-схема графа студии v4.

Один запрос: HTTP → endpoint (fastbase) → scenario (core) → _db.<method> (io) → model (event-infra).
Только модели данных, никакой логики. Версия схемы — 4.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

NodeKind = Literal["endpoint", "scenario", "io", "model", "test", "helper"]
Origin = Literal["code", "user", "stale"]
Layer = Literal["representation", "logic", "io", ""]
EdgeKind = Literal["dispatch", "scenario_call", "port_call", "table", "tests", "uses", "depends"]


class EndpointDetails(BaseModel):
    http_method: str
    path: str
    function_name: str
    router_module: str
    tags: list[str] = Field(default_factory=list)


class ScenarioDetails(BaseModel):
    name: str
    class_name: str
    module: str
    dto: str | None = None
    response: str | None = None


class IODetails(BaseModel):
    method: str
    table: str | None = None
    via_attr: str = "_db"
    called_from: str


class ModelDetails(BaseModel):
    tablename: str
    class_name: str
    source_file: str


class HelperDetails(BaseModel):
    role: Literal["schema", "dependency", "exception", "settings", "utils", "other"]
    module: str


class TestDetails(BaseModel):
    test_name: str
    test_for_scenario: str | None = None
    test_for_endpoint: str | None = None
    module: str


class Node(BaseModel):
    id: str
    kind: NodeKind
    layer: Layer = ""
    label: str
    file_path: str
    line: int = 1
    details: dict = Field(default_factory=dict)
    origin: Origin = "code"
    # Описание из плана (редактируется в студии).
    description: str = ""
    # Docstring из кода (только чтение; показывается, если описания в плане нет).
    doc: str = ""
    acceptance: list[str] = Field(default_factory=list)
    notes: str = ""
    domain: str = ""
    # Область плана: id эндпоинта плана, к которому относится узел ("" — без области).
    area: str = ""
    scaffolded: bool = False
    create_file: bool = True
    append_model: bool = False


class Edge(BaseModel):
    id: str
    from_node: str
    to_node: str
    kind: EdgeKind
    label: str = ""
    line: int = 1
    origin: Origin = "code"
    description: str = ""


class ScenarioPath(BaseModel):
    endpoint: str | None = None
    scenario: str
    io_calls: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    helpers: list[str] = Field(default_factory=list)
    called_scenarios: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)


class PlanMeta(BaseModel):
    last_scan: str | None = None
    last_edit: str | None = None
    last_scaffold: str | None = None


class Graph(BaseModel):
    version: int = 4
    project_root: str = ""
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    endpoints: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
    scenario_paths: dict[str, ScenarioPath] = Field(default_factory=dict)
    plan_meta: PlanMeta = Field(default_factory=PlanMeta)

    def by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}

    def edges_from(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.from_node == node_id]

    def edges_to(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.to_node == node_id]
