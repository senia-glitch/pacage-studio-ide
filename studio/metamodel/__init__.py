"""Pydantic-схема графа. Файл = узел, контракт = имя, связь = совпадение имён.

Измерения узла: домен (пакет), слой (тип файла), бизнес-домен (предметная область).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from studio.config import DEFAULT_BIZ

ContractKind = Literal["abstraction", "implementation"]
NodeState = Literal["collapsed", "expanded", "code"]
NodeStatus = Literal["tracked", "added", "modified", "deleted"]
LinkStatus = Literal["tracked", "added", "deleted"]


class Contract(BaseModel):
    name: str
    kind: ContractKind = "implementation"
    owner: str | None = None  # имя класса-владельца (None = функция уровня модуля)
    line: int | None = None


class NodeUI(BaseModel):
    x: float = 0.0
    y: float = 0.0
    state: NodeState = "collapsed"


class NodeProperties(BaseModel):
    name: str = ""
    description: str = ""
    metadata: dict = Field(default_factory=dict)


class Node(BaseModel):
    id: str  # относительный posix-путь файла, напр. "app/scenarios/order/create.py"
    path: str  # то же, что id (оставлено для читаемости writer'а)
    domain: str = "shared"  # пакет: infra | core | presentation | shared
    layer: str = "unknown"  # тип файла: models | scenarios | ...
    business: str = DEFAULT_BIZ  # предметная область: папка после scenarios/routes/schemas, иначе общее
    contracts: list[Contract] = Field(default_factory=list)
    properties: NodeProperties = Field(default_factory=NodeProperties)
    ui: NodeUI = Field(default_factory=NodeUI)
    status: NodeStatus = "tracked"
    code: str | None = None  # переопределение содержимого файла (code view). None = без правок.


class Link(BaseModel):
    id: str  # f"{from}::{contract}>>{to}"
    contract: str
    from_node: str  # узел-реализация (зависимый)
    to_node: str  # узел-абстракция (провайдер)
    weak: bool = False  # True = обе стороны concrete, абстракции нет
    violation: bool = False
    status: LinkStatus = "tracked"


class Graph(BaseModel):
    version: int = 1
    nodes: list[Node] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)

    def by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}
