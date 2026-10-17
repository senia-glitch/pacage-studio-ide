"""Pydantic-схема графа студии v2.

Один запрос: HTTP → endpoint (fastbase) → scenario (core) → _db.<method> (io) → model (event-infra).
Только модели данных, никакой логики. Версия схемы — 2.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

NodeKind = Literal["endpoint", "scenario", "io", "model", "helper"]
Layer = Literal["representation", "logic", "io", ""]
EdgeKind = Literal["dispatch", "scenario_call", "port_call", "table", "uses", "depends"]


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


class Node(BaseModel):
    id: str
    kind: NodeKind
    layer: Layer = ""
    label: str
    file_path: str
    line: int = 1
    doc: str | None = None
    details: dict = Field(default_factory=dict)


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
    helpers: list[str] = Field(default_factory=list)
    called_scenarios: list[str] = Field(default_factory=list)


class Graph(BaseModel):
    version: int = 2
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
