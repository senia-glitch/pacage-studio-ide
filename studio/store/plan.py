"""Хранение пользовательских патчей: `.studio/plan.json`.

План — единственный источник пользовательских данных (origin=user/stale).
Переживает любой scan. Формат описан в §4 ТЗ.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from pydantic import BaseModel, Field

from studio.metamodel import Edge, Graph, Node, PlanMeta
from studio.store import studio_dir

PLAN_NAME = "plan.json"
PLAN_VERSION = 1

_PATCH_FIELDS = ("description", "acceptance", "notes", "label", "file_path", "create_file", "append_model")


class PlanPatch(BaseModel):
    target_id: str
    is_edge: bool = False
    node: Node | None = None
    edge: Edge | None = None
    patch: dict | None = None
    mark_removed: bool = False


class Plan(BaseModel):
    version: int = PLAN_VERSION
    patches: list[PlanPatch] = Field(default_factory=list)
    meta: PlanMeta = Field(default_factory=PlanMeta)


def plan_path(project_root: Path) -> Path:
    return studio_dir(project_root) / PLAN_NAME


def read_plan(project_root: Path) -> Plan:
    """Нет файла → пустой Plan."""
    path = plan_path(project_root)
    if not path.is_file():
        return Plan()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Plan()
    if not isinstance(data, dict):
        return Plan()
    try:
        return Plan.model_validate(data)
    except Exception:
        return Plan()


def write_plan(project_root: Path, plan: Plan) -> Path:
    path = plan_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(plan.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def upsert_patch(plan: Plan, patch: PlanPatch) -> None:
    """Заменить по target_id+is_edge или добавить."""
    for i, existing in enumerate(plan.patches):
        if existing.target_id == patch.target_id and existing.is_edge == patch.is_edge:
            plan.patches[i] = patch
            return
    plan.patches.append(patch)


def remove_patch(plan: Plan, target_id: str, is_edge: bool) -> None:
    plan.patches = [p for p in plan.patches if not (p.target_id == target_id and p.is_edge == is_edge)]


def apply_plan(scanned: Graph, plan: Plan) -> Graph:
    """Применить патчи к сканированному графу. Возвращает новый Graph."""
    result = copy.deepcopy(scanned)
    result.plan_meta = copy.deepcopy(plan.meta)

    nodes_by_id = result.by_id()
    edges_by_id = {e.id: e for e in result.edges}

    for patch in plan.patches:
        if patch.node is not None:
            if patch.node.id not in result.by_id():
                new_node = copy.deepcopy(patch.node)
                new_node.origin = "user"
                result.nodes.append(new_node)

        if patch.edge is not None:
            if patch.edge.id not in {e.id for e in result.edges}:
                new_edge = copy.deepcopy(patch.edge)
                new_edge.origin = "user"
                result.edges.append(new_edge)

        if patch.patch is not None:
            if patch.is_edge:
                target = edges_by_id.get(patch.target_id)
                if target is not None:
                    _apply_patch_fields(target, patch.patch)
            else:
                target = nodes_by_id.get(patch.target_id)
                if target is not None:
                    _apply_patch_fields(target, patch.patch)

        if patch.mark_removed:
            target = nodes_by_id.get(patch.target_id)
            if target is not None:
                target.origin = "stale"

    result.nodes.sort(key=lambda n: n.id)
    result.edges.sort(key=lambda e: e.id)
    result.endpoints = sorted(n.id for n in result.nodes if n.kind == "endpoint")
    result.scenarios = sorted(n.id for n in result.nodes if n.kind == "scenario")
    return result


def _apply_patch_fields(target: BaseModel, patch: dict) -> None:
    for key in _PATCH_FIELDS:
        if key in patch:
            setattr(target, key, copy.deepcopy(patch[key]))
