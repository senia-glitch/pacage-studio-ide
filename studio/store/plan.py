"""Хранение пользовательских патчей: `.studio/plan.json`.

План — единственный источник пользовательских данных (origin=user/stale).
Переживает любой scan. Формат описан в §4 ТЗ.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
from pathlib import Path

from pydantic import BaseModel, Field

from studio.metamodel import Edge, Graph, Node, PlanMeta
from studio.store import studio_dir

PLAN_NAME = "plan.json"
PLAN_VERSION = 1

_PATCH_FIELDS = (
    "description",
    "acceptance",
    "notes",
    "label",
    "file_path",
    "create_file",
    "append_model",
    "domain",
    "area",
)


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


class PlanCorruptedError(Exception):
    """plan.json не читается: изменение прервано, данные сохранены в резервной копии."""


def _backup_corrupt(path: Path) -> None:
    """Сохранить испорченный plan.json рядом, чтобы пользовательские данные не пропали молча."""
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        path.rename(path.with_name(f"{PLAN_NAME}.corrupt-{stamp}"))
    except OSError:
        return


def _parse_plan_text(path: Path, text: str) -> Plan | None:
    """None — файл не читается (битый или не словарь)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return Plan.model_validate(data)
    except Exception:
        return None


def read_plan(project_root: Path) -> Plan:
    """Нет файла или файл не читается → пустой Plan. Запись не трогает (см. load_plan_for_write)."""
    path = plan_path(project_root)
    if not path.is_file():
        return Plan()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return Plan()
    if not text.strip():
        return Plan()
    return _parse_plan_text(path, text) or Plan()


def load_plan_for_write(project_root: Path) -> Plan:
    """План для записи. Битый файл не затираем: бэкап рядом + исключение с понятным текстом."""
    path = plan_path(project_root)
    if not path.is_file():
        return Plan()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return Plan()
    if not text.strip():
        return Plan()
    plan = _parse_plan_text(path, text)
    if plan is not None:
        return plan
    _backup_corrupt(path)
    raise PlanCorruptedError(
        f"plan.json повреждён и не читается — копия сохранена как "
        f"{PLAN_NAME}.corrupt-*. Проверьте её и удалите, если всё в порядке."
    )


def write_plan(project_root: Path, plan: Plan) -> Path:
    path = plan_path(project_root)
    if path.is_file():
        # Не затираем нечитаемый файл: иначе первая же запись потеряла бы данные плана.
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if text.strip() and _parse_plan_text(path, text) is None:
            _backup_corrupt(path)
            raise PlanCorruptedError(
                f"plan.json повреждён — копия сохранена как {PLAN_NAME}.corrupt-*, запись отменена."
            )
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


def remove_node_edges(plan: Plan, node_id: str) -> None:
    """Удалить из плана рёбра, которые ссылаются на узел (его удаление не оставляет висячих рёбер)."""
    plan.patches = [
        p
        for p in plan.patches
        if not (
            p.is_edge
            and (
                (p.edge is not None and node_id in (p.edge.from_node, p.edge.to_node))
                or (
                    p.edge is None
                    and (p.target_id.startswith(node_id + "::") or p.target_id.endswith("::" + node_id))
                )
            )
        )
    ]


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
            elif target.origin == "code" and patch.node.origin == "user":
                # Узел создан в плане (форма/палитра), после scaffold код появился под тем же id:
                # пользовательские поля плана переживают scan, структуру берёт код.
                _sync_plan_data(target, patch.node)

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
    target.area = src.area
    target.scaffolded = src.scaffolded
    target.create_file = src.create_file
    target.append_model = src.append_model


def _sync_plan_data(target: Node, src: Node) -> None:
    """Перенести в код-узел только пользовательские поля плана (структуру берёт скан)."""
    target.label = src.label
    target.description = src.description
    target.acceptance = copy.deepcopy(src.acceptance)
    target.notes = src.notes
    target.domain = src.domain
    target.area = src.area


def rename_node_refs(plan: Plan, old_id: str, new_id: str) -> None:
    """Перенести ссылки плана со старого id узла на новый (переименование class_name/function_name).

    Меняются: target_id и id узла патча, from/to у user-рёбер (и их id по схеме UI
    `from::kind::to`), called_from у io-узлов плана.
    """
    if old_id == new_id:
        return
    edge_remap: dict[str, str] = {}
    for patch in plan.patches:
        edge = patch.edge
        if edge is None or old_id not in (edge.from_node, edge.to_node):
            continue
        edge.from_node = new_id if edge.from_node == old_id else edge.from_node
        edge.to_node = new_id if edge.to_node == old_id else edge.to_node
        if edge.origin == "user":
            new_edge_id = f"{edge.from_node}::{edge.kind}::{edge.to_node}"
            edge_remap[patch.target_id] = new_edge_id
            edge.id = new_edge_id
    for patch in plan.patches:
        if patch.is_edge and patch.target_id in edge_remap:
            patch.target_id = edge_remap[patch.target_id]
        elif not patch.is_edge and patch.target_id == old_id:
            patch.target_id = new_id
        if patch.patch and patch.patch.get("area") == old_id:
            patch.patch = {**patch.patch, "area": new_id}
        node = patch.node
        if node is None:
            continue
        if node.id == old_id:
            node.id = new_id
        if node.area == old_id:
            node.area = new_id
        if node.kind == "io" and node.details.get("called_from") == old_id:
            node.details = {**node.details, "called_from": new_id}


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
