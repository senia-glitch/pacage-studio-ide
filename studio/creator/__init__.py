"""Конструктор «Создание» (§7.5): генерация файлов, содержащих только метаданные.

Код не пишется. Один файл на узел. Связи — блоки `edge` в метаданных.
"""

from __future__ import annotations

import contextlib
import keyword
import re
from dataclasses import dataclass, field
from pathlib import Path

from studio._atomic import atomic_write
from studio.env import ProjectEnv, dotted_to_path
from studio.metadata import render_metadata_file
from studio.metamodel import EDGE_KINDS, NODE_KINDS, node_anchor

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
            scenario = str(self.details.get("scenario") or "")
            method = str(self.details.get("method") or self.name)
            return f"{scenario}._db.{method}"
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
        if kind not in NODE_KINDS:
            raise CreateError(f"неизвестный вид узла: {kind!r}")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise CreateError("у каждого узла должно быть имя")
        if kind == "test":
            check = name
        elif kind == "io":
            check = str(raw.get("details", {}).get("method") or name)
        else:
            check = name
        if not _IDENT_RE.match(check):
            raise CreateError(f"недопустимое имя: {name!r}")
        if keyword.iskeyword(check):
            raise CreateError(f"имя {name!r} — ключевое слово Python")
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        if kind == "io":
            scenario = str(details.get("scenario") or raw.get("scenario") or "").strip()
            if not scenario:
                raise CreateError("для io-узла нужен сценарий")
            details = {"scenario": scenario, "method": check}
        if kind == "endpoint":
            method = str(details.get("http_method") or raw.get("http_method") or "").strip().lower()
            if method and method not in ("get", "post", "put", "patch", "delete"):
                raise CreateError(f"неизвестный HTTP-метод: {method!r}")
            path = str(details.get("path") or raw.get("path") or "").strip()
            if path and not path.startswith("/"):
                path = "/" + path
            details = {"http_method": method, "path": path}
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
            scenario = _snake(str(node.details.get("scenario") or "scenario"))
            method = _snake(str(node.details.get("method") or node.name))
            filename = f"{scenario}_{method}_io.py"
        return (base / filename).relative_to(root).as_posix()
    if node.kind == "model":
        return f"models_{_snake(node.name)}.py"
    if node.kind == "test":
        name = node.name
        if not name.startswith("test_"):
            name = f"test_{name}"
        return f"tests/{_snake(name)}.py"
    raise CreateError(f"неизвестный вид узла: {node.kind}")


def _parse_edges(
    data: dict, by_name: dict[str, NodeSpec]
) -> list[tuple[str, str, str, str, str]]:
    """(from_name, to_name, kind, anchor_from, anchor_to)."""
    raw_edges = data.get("edges") or []
    if not isinstance(raw_edges, list):
        raise CreateError("связи должны быть списком")
    out: list[tuple[str, str, str, str, str]] = []
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
        out.append((source, target, kind, by_name[source].anchor, by_name[target].anchor))
    return out


def _existing_anchors(env: ProjectEnv) -> dict[str, str]:
    """Якорь → описание существующего узла; ошибка скана не блокирует создание."""
    from studio.scanner import scan

    try:
        graph = scan(env.project_root)
    except Exception:  # noqa: BLE001 — конструктор не должен падать из-за скана
        return {}
    out: dict[str, str] = {}
    for node in graph.nodes:
        anchor = node_anchor(node)
        if anchor and anchor not in out:
            out[anchor] = f"{node.label} ({node.file_path})"
    return out


def plan_creation(
    data: dict, env: ProjectEnv
) -> tuple[list[tuple[str, str, str, str]], list[CreatedFile]]:
    """Проверить ввод и подготовить файлы, не записывая их."""
    nodes = _parse_nodes(data)
    by_name = {node.name: node for node in nodes}
    edges = _parse_edges(data, by_name)
    root = env.project_root
    existing = _existing_anchors(env)

    paths: dict[str, NodeSpec] = {}
    created: list[CreatedFile] = []
    anchors: dict[str, str] = {}
    for node in nodes:
        if node.anchor in existing:
            raise CreateError(
                f"имя {node.anchor} занято узлом {existing[node.anchor]}"
            )
        rel = _target_path(env, node)
        if rel in paths:
            raise CreateError(f"два узла претендуют на один файл: {rel}")
        target = (root / rel).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise CreateError(f"путь вне проекта: {rel}") from exc
        if target.exists():
            raise CreateError(f"файл уже существует: {rel}")
        paths[rel] = node
        anchors[rel] = node.anchor
        created.append(CreatedFile(path=rel, kind=node.kind, name=node.name))

    counts: dict[str, int] = {}
    for anchor in anchors.values():
        counts[anchor] = counts.get(anchor, 0) + 1

    def edge_anchor(rel: str) -> str:
        anchor = anchors[rel]
        if counts.get(anchor, 0) > 1 or anchor in existing:
            return f"{rel}::{anchor}"
        return anchor

    edges_by_source: dict[str, list[tuple[str, str, str]]] = {}
    rel_by_name = {node.name: rel for rel, node in paths.items()}
    for source, target_name, kind, _anchor_from, _anchor_to in edges:
        edges_by_source.setdefault(source, []).append(
            (edge_anchor(rel_by_name[source]), edge_anchor(rel_by_name[target_name]), kind)
        )

    files: list[tuple[str, str, str, str]] = []
    for rel, node in paths.items():
        domain = node.domain if node.kind == "endpoint" else ""
        content = render_metadata_file(
            description=node.description,
            acceptance=node.acceptance,
            domain=domain,
            edges=edges_by_source.get(node.name, []),
            anchor_note=(
                f"Желаемое имя/якорь: {node.anchor} — не утверждено, "
                "можно изменить при написании кода."
            ),
        )
        files.append((rel, node.name, node.kind, content))
    return files, created


def create_metadata_files(data: dict, env: ProjectEnv) -> list[CreatedFile]:
    files, created = plan_creation(data, env)
    root = env.project_root
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
    except (CreateError, OSError) as exc:
        for target in reversed(written):
            with contextlib.suppress(OSError):
                target.unlink()
        if isinstance(exc, CreateError):
            raise
        raise CreateError(f"не удалось создать файлы: {exc}") from exc
    return created
