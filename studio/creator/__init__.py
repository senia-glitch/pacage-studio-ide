"""Конструктор «Создание»: планируемые узлы в metadata.json и файлы-скелеты.

Код проекта не перезаписывается: создаются только новые файлы с TODO. Все
описания, критерии, домен и связи живут в `.studio/metadata.json`.
"""

from __future__ import annotations

import contextlib
import keyword
import re
from dataclasses import dataclass, field
from pathlib import Path

from studio._atomic import atomic_write
from studio.env import ProjectEnv, dotted_to_path
from studio.metadata import (
    MetadataError,
    metadata_path,
    read_metadata,
    write_metadata,
)
from studio.metamodel import EDGE_KINDS, node_anchor

CREATABLE_KINDS = ("endpoint", "scenario", "io", "model", "test")
ENDPOINT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
CLASS_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
TEST_RE = re.compile(r"^test_[a-z0-9_]+$")
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
TODO_LINE = "# TODO: {kind} {name} — скелет студии, описание в .studio/metadata.json"


class CreateError(Exception):
    """Конструктор отклонён: понятное пользователю сообщение."""


@dataclass
class NodeSpec:
    kind: str
    name: str
    description: str = ""
    acceptance: list[str] = field(default_factory=list)
    domain: str = ""
    details: dict = field(default_factory=dict)

    @property
    def anchor(self) -> str:
        if self.kind == "io":
            scenario = str(self.details.get("scenario_class") or "")
            method = str(self.details.get("method") or self.name)
            return f"{scenario}._db.{method}#{int(self.details.get('ordinal') or 1)}"
        return self.name


@dataclass
class CreatedFile:
    path: str
    kind: str
    name: str


def _snake(name: str) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    text = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_").lower()
    return text or "node"


def _validate_name(kind: str, name: str) -> None:
    if kind == "endpoint" or kind == "io":
        if not ENDPOINT_RE.match(name):
            raise CreateError(f"имя {name!r}: нужен snake_case (например create_order)")
    elif kind in ("scenario", "model"):
        if not CLASS_RE.match(name):
            raise CreateError(f"имя {name!r}: нужен PascalCase (например CancelOrder)")
    elif kind == "test" and not TEST_RE.match(name):
        raise CreateError(f"имя {name!r}: нужен тест вида test_cancel_order")
    if keyword.iskeyword(name):
        raise CreateError(f"имя {name!r} — ключевое слово Python")


def _parse_nodes(data: dict) -> list[NodeSpec]:
    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise CreateError("нужен хотя бы один узел")
    nodes: list[NodeSpec] = []
    names: set[str] = set()
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            raise CreateError("узел должен быть объектом")
        kind = str(raw.get("kind") or "")
        if kind not in CREATABLE_KINDS:
            raise CreateError(f"неизвестный вид узла: {kind!r}")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise CreateError("у каждого узла должно быть имя")
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        details = dict(details)
        if kind == "io":
            scenario = str(details.get("scenario_class") or details.get("scenario") or "").strip()
            if not scenario:
                raise CreateError("для io-узла нужен сценарий")
            method = str(details.get("method") or name).strip()
            _validate_name("io", method)
            details = {
                "scenario_class": scenario,
                "method": method,
                "ordinal": int(details.get("ordinal") or 1),
            }
            name = method
        else:
            _validate_name(kind, name)
        if kind == "endpoint":
            method = str(details.get("http_method") or "").strip().lower()
            if method and method not in HTTP_METHODS:
                raise CreateError(f"неизвестный HTTP-метод: {method!r}")
            path = str(details.get("path") or "").strip()
            if path and not path.startswith("/"):
                path = "/" + path
            details = {"http_method": method, "path": path}
        if kind == "scenario":
            details = {"scenario_name": str(details.get("scenario_name") or name)}
        if kind == "model":
            details = {
                "tablename": str(details.get("tablename") or _snake(name)),
            }
        if name in names:
            raise CreateError(f"имя узла повторяется: {name}")
        names.add(name)
        acceptance_raw = raw.get("acceptance") or []
        if isinstance(acceptance_raw, str):
            acceptance = [
                re.sub(r"^-\s*", "", line.strip())
                for line in acceptance_raw.splitlines()
                if line.strip()
            ]
        elif isinstance(acceptance_raw, list):
            acceptance = [
                re.sub(r"^-\s*", "", str(item).strip())
                for item in acceptance_raw
                if str(item).strip()
            ]
        else:
            raise CreateError("критерии приёмки должны быть списком строк")
        nodes.append(
            NodeSpec(
                kind=kind,
                name=name,
                description=str(raw.get("description") or ""),
                acceptance=acceptance,
                domain=str(raw.get("domain") or ""),
                details=details,
            )
        )
    return nodes


def _target_path(env: ProjectEnv, node: NodeSpec) -> str:
    root = env.project_root
    if node.kind == "endpoint":
        base = dotted_to_path(root, env.routes_package)
        return (base / f"{_snake(node.name)}.py").relative_to(root).as_posix()
    if node.kind in ("scenario", "io"):
        base = dotted_to_path(root, env.core_discover)
        if node.kind == "scenario":
            filename = f"{_snake(node.name)}.py"
        else:
            scenario = _snake(str(node.details.get("scenario_class") or "scenario"))
            method = _snake(str(node.details.get("method") or node.name))
            filename = f"{scenario}_{method}_io.py"
        return (base / filename).relative_to(root).as_posix()
    if node.kind == "model":
        return f"models_{_snake(node.name)}.py"
    if node.kind == "test":
        return f"tests/{_snake(node.name)}.py"
    raise CreateError(f"неизвестный вид узла: {node.kind}")


def _node_id(node: NodeSpec, rel: str) -> str:
    if node.kind == "io":
        scenario = str(node.details.get("scenario_class") or "")
        method = str(node.details.get("method") or node.name)
        ordinal = int(node.details.get("ordinal") or 1)
        return f"io:{rel}:{scenario}._db.{method}#{ordinal}"
    return f"{node.kind}:{rel}:{node.name}"


def _parse_edges(
    data: dict, by_name: dict[str, NodeSpec]
) -> list[tuple[str, str, str]]:
    raw_edges = data.get("edges") or []
    if not isinstance(raw_edges, list):
        raise CreateError("связи должны быть списком")
    out: list[tuple[str, str, str]] = []
    for raw in raw_edges:
        if not isinstance(raw, dict):
            raise CreateError("связь должна быть объектом")
        source = str(raw.get("from") or "")
        target = str(raw.get("to") or "")
        kind = str(raw.get("kind") or "")
        if source not in by_name or target not in by_name:
            raise CreateError("связь ссылается на несуществующий узел")
        if kind not in EDGE_KINDS:
            raise CreateError(f"неизвестный тип связи: {kind!r}")
        out.append((source, target, kind))
    return out


def _existing_anchors(env: ProjectEnv) -> dict[str, str]:
    from studio.scanner import scan

    try:
        graph = scan(env.project_root)
    except Exception:  # noqa: BLE001 — конструктор не должен падать из-за скана
        return {}
    out: dict[str, str] = {}
    for node in graph.nodes:
        for key in (node.id, node_anchor(node), node.label):
            if key and key not in out:
                out[key] = f"{node.label} ({node.file_path})"
    return out


def _skeleton(node: NodeSpec) -> str:
    todo = TODO_LINE.format(kind=node.kind, name=node.name)
    if node.kind == "endpoint":
        method = str(node.details.get("http_method") or "")
        path = str(node.details.get("path") or "")
        lines = ["from fastapi import APIRouter", "", "router = APIRouter()", "", todo]
        if method and path:
            lines.extend(
                [
                    f'@router.{method}("{path}")',
                    f"async def {node.name}():",
                    "    ...",
                ]
            )
        else:
            lines.extend([f"async def {node.name}():", "    ..."])
        return "\n".join(lines) + "\n"
    if node.kind == "scenario":
        scenario_name = str(node.details.get("scenario_name") or node.name)
        return (
            "from core import BaseScenario, register_scenario\n\n"
            f"{todo}\n"
            f'@register_scenario("{scenario_name}")\n'
            f"class {node.name}(BaseScenario):\n"
            "    async def execute(self):\n"
            "        ...\n"
        )
    if node.kind == "model":
        table = str(node.details.get("tablename") or _snake(node.name))
        return (
            "from sqlmodel import SQLModel\n\n"
            f"{todo}\n"
            f"class {node.name}(SQLModel, table=True):\n"
            f'    __tablename__ = "{table}"\n'
        )
    if node.kind == "test":
        return f"{todo}\n\ndef {node.name}():\n    ...\n"
    if node.kind == "io":
        return f"{todo}\n"
    return f"{todo}\n"


def _plan(
    data: dict, env: ProjectEnv
) -> tuple[list[tuple[str, str, str, str]], list[CreatedFile], dict[str, dict], list[dict]]:
    nodes = _parse_nodes(data)
    by_name = {node.name: node for node in nodes}
    edges = _parse_edges(data, by_name)
    root = env.project_root.resolve()
    existing = _existing_anchors(env)

    paths: dict[str, NodeSpec] = {}
    ids: dict[str, str] = {}
    created: list[CreatedFile] = []
    for node in nodes:
        rel = _target_path(env, node)
        if rel in paths:
            raise CreateError(f"два узла претендуют на один файл: {rel}")
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise CreateError(f"путь вне проекта: {rel}") from exc
        if target.exists():
            raise CreateError(f"файл уже существует: {rel}")
        node_id = _node_id(node, rel)
        for key in (node_id, node.anchor, node.name):
            if key in existing:
                raise CreateError(f"имя {node.anchor} занято узлом {existing[key]}")
        paths[rel] = node
        ids[node.name] = node_id
        created.append(CreatedFile(path=rel, kind=node.kind, name=node.name))

    records: dict[str, dict] = {}
    for rel, node in paths.items():
        node_id = ids[node.name]
        details = dict(node.details)
        details["target_file"] = rel
        records[node_id] = {
            "kind": node.kind,
            "name": node.anchor,
            "file_path": rel,
            "target_file": rel,
            "details": details,
            "description": node.description.strip(),
            "acceptance": list(node.acceptance),
            "domain": node.domain.strip() if node.kind == "endpoint" else "",
            "planned": True,
        }

    edge_records: list[dict] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for source_name, target_name, kind in edges:
        edge = (ids[source_name], ids[target_name], kind)
        if edge in seen_edges:
            continue
        seen_edges.add(edge)
        edge_records.append({"from": edge[0], "to": edge[1], "kind": kind})

    files = [
        (rel, node.name, node.kind, _skeleton(node))
        for rel, node in paths.items()
    ]
    return files, created, records, edge_records


def plan_creation(
    data: dict, env: ProjectEnv
) -> tuple[list[tuple[str, str, str, str]], list[CreatedFile]]:
    """Проверить ввод и подготовить файлы, не записывая их."""
    files, created, _records, _edges = _plan(data, env)
    return files, created


def _restore_metadata(root: Path, backup: bytes | None) -> None:
    path = metadata_path(root)
    try:
        if backup is None:
            if path.is_file():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(backup)
    except OSError:
        pass


def create_metadata_files(data: dict, env: ProjectEnv) -> list[CreatedFile]:
    """Создать скелеты и записать планы. Всё или ничего."""
    files, created, records, edge_records = _plan(data, env)
    root = env.project_root
    metadata_file = metadata_path(root)
    backup = metadata_file.read_bytes() if metadata_file.is_file() else None
    written: list[Path] = []
    try:
        for rel, _name, _kind, content in files:
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise CreateError(f"файл уже существует: {rel}")
            if not atomic_write(target, content):
                raise CreateError(f"не удалось записать {rel}")
            written.append(target)
        metadata = read_metadata(root)
        metadata.nodes.update(records)
        known = {
            (str(item.get("from")), str(item.get("kind")), str(item.get("to")))
            for item in metadata.edges
        }
        for edge in edge_records:
            key = (edge["from"], edge["kind"], edge["to"])
            if key not in known:
                metadata.edges.append(edge)
                known.add(key)
        if not write_metadata(root, metadata):
            raise CreateError("не удалось записать .studio/metadata.json")
    except (CreateError, OSError, MetadataError) as exc:
        for target in reversed(written):
            with contextlib.suppress(OSError):
                target.unlink()
        _restore_metadata(root, backup)
        if isinstance(exc, CreateError):
            raise
        raise CreateError(f"не удалось создать файлы: {exc}") from exc
    return created


__all__ = [
    "CREATABLE_KINDS",
    "CreateError",
    "CreatedFile",
    "NodeSpec",
    "create_metadata_files",
    "plan_creation",
]
