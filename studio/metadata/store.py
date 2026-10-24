"""Хранилище метаданных студии: только `.studio/metadata.json`.

Код проекта здесь не читается и не пишется. Файл — простой JSON, ключ узла —
стабильный якорь (`endpoint:app/api/routes.py:create_order`). Запись атомарная
и выполняется только при явном действии пользователя.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from studio._atomic import atomic_write
from studio.diagnostics import Sink
from studio.metamodel import Node, normalize_domain

METADATA_VERSION = 1
STUDIO_DIR = ".studio"
METADATA_NAME = "metadata.json"
ALLOWED_FIELDS = (
    "kind",
    "name",
    "file_path",
    "target_file",
    "details",
    "description",
    "acceptance",
    "domain",
    "planned",
)


class MetadataError(Exception):
    """Метаданные нельзя прочитать или записать. code — машинный код."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        self.details: dict | None = None
        super().__init__(message)


@dataclass
class SaveResult:
    changed: bool
    removed_orphans: list[dict] = field(default_factory=list)
    new_hash: str = ""
    undo_id: str | None = None


@dataclass
class Metadata:
    version: int = METADATA_VERSION
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)

    def copy(self) -> Metadata:
        return Metadata(
            version=self.version,
            nodes={key: _clean_node(value) for key, value in self.nodes.items()},
            edges=[dict(value) for value in self.edges],
        )


def studio_dir(project_root: Path) -> Path:
    return Path(project_root) / STUDIO_DIR


def metadata_path(project_root: Path) -> Path:
    return studio_dir(project_root) / METADATA_NAME


def _clean_node(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key in ALLOWED_FIELDS:
        if key not in raw:
            continue
        value = raw[key]
        if key in ("description", "name", "kind", "file_path", "target_file"):
            out[key] = str(value or "")
        elif key == "domain":
            out[key] = normalize_domain(str(value or ""))
        elif key == "acceptance":
            if isinstance(value, str):
                out[key] = [
                    line.strip().lstrip("- ").strip()
                    for line in value.splitlines()
                    if line.strip()
                ]
            elif isinstance(value, list):
                out[key] = [str(item).strip() for item in value if str(item).strip()]
            else:
                out[key] = []
        elif key == "details":
            out[key] = dict(value) if isinstance(value, dict) else {}
        elif key == "planned":
            out[key] = bool(value)
    return out


def _clean_edge(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("from") or "")
    target = str(raw.get("to") or "")
    kind = str(raw.get("kind") or "")
    if not source or not target or not kind:
        return None
    return {"from": source, "to": target, "kind": kind, "label": str(raw.get("label") or "")}


def read_metadata(project_root: Path, sink: Sink | None = None) -> Metadata:
    """Прочитать metadata.json. Нет файла — пустые метаданные; битый — диагностика."""
    path = metadata_path(project_root)
    if not path.is_file():
        return Metadata()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        if sink is not None:
            sink.add(
                "W_METADATA_INVALID",
                f"metadata.json не читается: {exc}",
                file=f"{STUDIO_DIR}/{METADATA_NAME}",
                hint="Исправьте JSON или удалите файл — он будет создан заново.",
            )
        return Metadata()
    if not isinstance(raw, dict):
        if sink is not None:
            sink.add(
                "W_METADATA_INVALID",
                "metadata.json: в корне должен быть объект JSON",
                file=f"{STUDIO_DIR}/{METADATA_NAME}",
            )
        return Metadata()
    version = raw.get("version")
    if version not in (None, METADATA_VERSION) and sink is not None:
        sink.add(
            "W_METADATA_INVALID",
            f"metadata.json: неизвестная версия схемы {version!r}",
            file=f"{STUDIO_DIR}/{METADATA_NAME}",
            hint=f"Поддерживается версия {METADATA_VERSION}.",
        )
    nodes: dict[str, dict] = {}
    raw_nodes = raw.get("nodes")
    if isinstance(raw_nodes, dict):
        for key, value in raw_nodes.items():
            record = _clean_node(value)
            if record:
                record.setdefault("kind", str(key).split(":", 1)[0])
                nodes[str(key)] = record
    edges: list[dict] = []
    raw_edges = raw.get("edges")
    if isinstance(raw_edges, list):
        for value in raw_edges:
            edge = _clean_edge(value)
            if edge is not None:
                edges.append(edge)
    return Metadata(version=METADATA_VERSION, nodes=nodes, edges=edges)


def metadata_payload(metadata: Metadata) -> str:
    payload = {
        "version": METADATA_VERSION,
        "nodes": {
            key: metadata.nodes[key] for key in sorted(metadata.nodes)
        },
        "edges": sorted(
            metadata.edges,
            key=lambda item: (
                str(item.get("from") or ""),
                str(item.get("kind") or ""),
                str(item.get("to") or ""),
            ),
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def write_metadata(project_root: Path, metadata: Metadata) -> bool:
    """Атомарно записать metadata.json. False — запись не удалась."""
    path = metadata_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    return atomic_write(path, metadata_payload(metadata))


def _record_for_node(node: Node) -> dict:
    details = dict(node.details or {})
    return {
        "kind": node.kind,
        "name": str(details.get("function_name") or details.get("class_name")
                    or details.get("test_name") or details.get("name")
                    or details.get("method") or node.label),
        "file_path": node.file_path,
        "target_file": node.file_path,
        "details": details,
        "description": node.description or "",
        "acceptance": list(node.acceptance or []),
        "domain": node.domain if node.kind == "endpoint" else "",
        "planned": bool(node.planned),
    }


def save_node_metadata(
    project_root: Path,
    node: Node,
    description: str,
    acceptance: list[str],
    domain: str,
) -> SaveResult:
    """Сохранить описание/критерии/домен узла в metadata.json."""
    metadata = read_metadata(project_root)
    record = dict(metadata.nodes.get(node.id) or _record_for_node(node))
    record.update(
        {
            "kind": node.kind,
            "file_path": node.file_path,
            "description": description.strip(),
            "acceptance": [str(item).strip() for item in acceptance if str(item).strip()],
            "planned": bool(node.planned),
        }
    )
    if not record.get("name"):
        record["name"] = _record_for_node(node)["name"]
    if not record.get("target_file"):
        record["target_file"] = node.file_path
    record["domain"] = normalize_domain(domain) if node.kind == "endpoint" else ""
    record.setdefault("details", dict(node.details or {}))
    if record["kind"] == "endpoint":
        endpoint_details = dict(record.get("details") or {})
        endpoint_details.setdefault("function_name", node.details.get("function_name", ""))
        record["details"] = endpoint_details
    before = metadata_payload(metadata)
    metadata.nodes[node.id] = _clean_node(record)
    after = metadata_payload(metadata)
    changed = before != after
    if changed and not write_metadata(project_root, metadata):
        raise MetadataError("not_writable", "Не удалось записать .studio/metadata.json")
    return SaveResult(
        changed=changed,
        new_hash=hashlib.sha1(after.encode("utf-8")).hexdigest(),
    )


__all__ = [
    "METADATA_NAME",
    "METADATA_VERSION",
    "Metadata",
    "MetadataError",
    "SaveResult",
    "metadata_path",
    "metadata_payload",
    "read_metadata",
    "save_node_metadata",
    "studio_dir",
    "write_metadata",
]

