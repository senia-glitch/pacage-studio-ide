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

_PATCH_FIELDS = ("description", "acceptance", "notes", "label", "file_path", "create_file", "append_model", "domain")


class PlanPatch(BaseModel):
    target_id: str
    is_edge: bool = False
    node: Node | None = None
    edge: Edge | None = None
    patch: dict | None = None
    # None = «не передано» (не трогать накопленное); явный false снимает метку удаления.
    mark_removed: bool | None = None


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
    """Заменить по target_id+is_edge или добавить.

    Частичный патч не затирает накопленное: node/edge сохраняются, если в новом
    патче их нет, словари полей сливаются. mark_removed: None — не передано,
    наследуется от старого патча; явный false снимает метку удаления.
    """
    for i, existing in enumerate(plan.patches):
        if existing.target_id != patch.target_id or existing.is_edge != patch.is_edge:
            continue
        merged_patch = dict(existing.patch or {})
        merged_patch.update(patch.patch or {})
        plan.patches[i] = PlanPatch(
            target_id=patch.target_id,
            is_edge=patch.is_edge,
            node=patch.node if patch.node is not None else existing.node,
            edge=patch.edge if patch.edge is not None else existing.edge,
            patch=merged_patch or None,
            mark_removed=(
                patch.mark_removed if patch.mark_removed is not None else existing.mark_removed
            ),
        )
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
            target = nodes_by_id.get(patch.node.id)
            if target is None:
                new_node = copy.deepcopy(patch.node)
                new_node.origin = "user"
                result.nodes.append(new_node)
                nodes_by_id[new_node.id] = new_node
            elif target.origin in ("user", "stale") and patch.node.origin == "user":
                # stale → user: патч с node снимает метку удаления (см. mark_removed=false).
                target.origin = "user"
                _sync_user_node(target, patch.node)

        if patch.edge is not None:
            target_edge = edges_by_id.get(patch.edge.id)
            if target_edge is None:
                new_edge = copy.deepcopy(patch.edge)
                new_edge.origin = "user"
                result.edges.append(new_edge)
                edges_by_id[new_edge.id] = new_edge
            elif target_edge.origin in ("user", "stale") and patch.edge.origin == "user":
                target_edge.origin = "user"
                _sync_user_edge(target_edge, patch.edge)

        if patch.patch is not None:
            if patch.is_edge:
                target_edge = edges_by_id.get(patch.target_id)
                if target_edge is not None:
                    _apply_patch_fields(target_edge, patch.patch)
            else:
                target_node = nodes_by_id.get(patch.target_id)
                if target_node is not None:
                    _apply_patch_fields(target_node, patch.patch)

        if patch.mark_removed:
            if patch.is_edge:
                target_edge = edges_by_id.get(patch.target_id)
                if target_edge is not None:
                    target_edge.origin = "stale"
            else:
                target_node = nodes_by_id.get(patch.target_id)
                if target_node is not None:
                    target_node.origin = "stale"

        if patch.mark_removed is False:
            if patch.is_edge:
                target_edge = edges_by_id.get(patch.target_id)
                if target_edge is not None and target_edge.origin == "stale":
                    target_edge.origin = "code"
            else:
                target_node = nodes_by_id.get(patch.target_id)
                if target_node is not None and target_node.origin == "stale":
                    target_node.origin = "code"

    result.nodes.sort(key=lambda n: n.id)
    result.edges.sort(key=lambda e: e.id)
    result.endpoints = sorted(n.id for n in result.nodes if n.kind == "endpoint")
    result.scenarios = sorted(n.id for n in result.nodes if n.kind == "scenario")
    return result


def _sync_user_node(target: Node, src: Node) -> None:
    """Обновить пользовательский узел данными из патча (id и origin не трогаем)."""
    target.kind = src.kind
    target.layer = src.layer
    target.label = src.label
    target.file_path = src.file_path
    target.line = src.line
    target.details = copy.deepcopy(src.details)
    target.description = src.description
    target.acceptance = copy.deepcopy(src.acceptance)
    target.notes = src.notes
    target.domain = src.domain
    target.scaffolded = src.scaffolded
    target.create_file = src.create_file
    target.append_model = src.append_model


def _sync_user_edge(target: Edge, src: Edge) -> None:
    """Обновить пользовательское ребро данными из патча (id и origin не трогаем)."""
    target.from_node = src.from_node
    target.to_node = src.to_node
    target.kind = src.kind
    target.label = src.label
    target.line = src.line
    target.description = src.description


def _apply_patch_fields(target: BaseModel, patch: dict) -> None:
    for key in _PATCH_FIELDS:
        if key in patch:
            setattr(target, key, copy.deepcopy(patch[key]))
