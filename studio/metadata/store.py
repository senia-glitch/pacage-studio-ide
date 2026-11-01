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
from studio.metadata.plans import (
    Plan,
    PlanError,
    plan_from_record,
    plan_to_record,
)
from studio.metamodel import Node, normalize_domain

METADATA_VERSION = 2
STUDIO_DIR = ".studio"
METADATA_NAME = "metadata.json"
# Ключи, которые читаются из metadata.json. file_path остаётся для чтения
# старых «жирных» записей; при сохранении узла запись становится компактной.
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
    "plan",
    "layer",
    "studio_owned",
)
_TOP_LEVEL_FIELDS = frozenset({"version", "nodes", "edges", "plans"})


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
    new_hash: str = ""


@dataclass
class Metadata:
    version: int = METADATA_VERSION
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    plans: dict[str, Plan] = field(default_factory=dict)
    # Неизвестные ключи корня: читаются и пишутся без потерь.
    extra: dict = field(default_factory=dict)
    # Версия выше поддерживаемой: файл не перезаписывается.
    write_blocked: bool = False

    def copy(self) -> Metadata:
        return Metadata(
            version=self.version,
            nodes={key: _clean_node(value) for key, value in self.nodes.items()},
            edges=[dict(value) for value in self.edges],
            plans={
                key: plan_from_record(key, plan_to_record(plan))
                for key, plan in self.plans.items()
            },
            extra=dict(self.extra),
            write_blocked=self.write_blocked,
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
        if key in ("description", "name", "kind", "file_path", "target_file", "plan", "layer"):
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
        elif key == "planned" or key == "studio_owned":
            out[key] = bool(value)
    for key, value in raw.items():
        if key not in ALLOWED_FIELDS:
            out[key] = value
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
        return Metadata(version=METADATA_VERSION, write_blocked=True)
    if not isinstance(raw, dict):
        if sink is not None:
            sink.add(
                "W_METADATA_INVALID",
                "metadata.json: в корне должен быть объект JSON",
                file=f"{STUDIO_DIR}/{METADATA_NAME}",
                hint="Исправьте форму файла — запись остановлена, файл не изменяется.",
            )
        return Metadata(version=METADATA_VERSION, write_blocked=True)
    raw_version = raw.get("version")
    version = 1 if raw_version is None else raw_version
    if not isinstance(version, int) or version < 1:
        if sink is not None:
            sink.add(
                "W_METADATA_INVALID",
                f"metadata.json: неизвестная версия схемы {raw_version!r}",
                file=f"{STUDIO_DIR}/{METADATA_NAME}",
                hint=f"Поддерживается версия {METADATA_VERSION}; файл не перезаписывается.",
            )
        return Metadata(version=METADATA_VERSION, write_blocked=True)
    if version > METADATA_VERSION:
        if sink is not None:
            sink.add(
                "W_METADATA_INVALID",
                f"metadata.json: версия схемы {version} новее поддерживаемой "
                f"{METADATA_VERSION}",
                file=f"{STUDIO_DIR}/{METADATA_NAME}",
                hint="Обновите studio; файл не перезаписывается.",
            )
        return Metadata(version=METADATA_VERSION, write_blocked=True)
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
    plans: dict[str, Plan] = {}
    raw_plans = raw.get("plans")
    if raw_plans is not None:
        if not isinstance(raw_plans, dict):
            if sink is not None:
                sink.add(
                    "W_PLAN_INVALID",
                    "metadata.json: plans должен быть объектом «slug → план»",
                    file=f"{STUDIO_DIR}/{METADATA_NAME}",
                    hint="Удалите plans или исправьте его форму.",
                )
        else:
            for key, value in raw_plans.items():
                try:
                    plan = plan_from_record(str(key), value)
                except PlanError as exc:
                    if sink is not None:
                        sink.add(
                            "W_PLAN_INVALID",
                            f"metadata.json: план {key!r} не читается: {exc}",
                            file=f"{STUDIO_DIR}/{METADATA_NAME}",
                            hint="Исправьте типы полей плана или удалите запись.",
                        )
                    continue
                plans[str(key)] = plan
    extra = {
        key: value for key, value in raw.items() if key not in _TOP_LEVEL_FIELDS
    }
    return Metadata(
        version=METADATA_VERSION,
        nodes=nodes,
        edges=edges,
        plans=plans,
        extra=extra,
    )


def metadata_payload(metadata: Metadata) -> str:
    payload: dict = {
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
        "plans": {
            key: plan_to_record(metadata.plans[key])
            for key in sorted(metadata.plans)
        },
    }
    payload.update(
        {key: value for key, value in metadata.extra.items() if key not in payload}
    )
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def metadata_hash(project_root: Path) -> str | None:
    """Хэш текущего metadata.json (None — файла нет)."""
    return _file_hash(metadata_path(project_root))


def _file_hash(path: Path) -> str | None:
    """Хэш байтов файла или None, если файла нет. OSError — MetadataError."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MetadataError(
            "not_writable", f"metadata.json не читается для сверки: {exc}"
        ) from exc
    return hashlib.sha1(raw).hexdigest()


def write_metadata(
    project_root: Path, metadata: Metadata, expected_hash: str | None = None
) -> bool:
    """Атомарно записать metadata.json. False — запись не удалась или запрещена.

    `expected_hash` — хэш файла на момент чтения. Если файл уже изменили,
    запись не выполняется: чужая правка не затирается.
    """
    if metadata.write_blocked:
        return False
    path = metadata_path(project_root)
    if expected_hash is not None and _file_hash(path) != expected_hash:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    return atomic_write(path, metadata_payload(metadata))


def _node_name(node: Node) -> str:
    details = dict(node.details or {})
    return str(
        details.get("function_name")
        or details.get("class_name")
        or details.get("test_name")
        or details.get("name")
        or details.get("method")
        or node.label
    )


def _record_for_node(
    node: Node,
    description: str,
    acceptance: list[str],
    domain: str,
    existing: dict | None = None,
) -> dict:
    """Компактная запись узла: только то, что не вычисляет сканер.

    Существующий узел: kind, name, description, acceptance, domain.
    У не-endpoint домен всегда пустой (наследуется сканером).
    Планируемый: то же + planned, target_file и details — ввод пользователя
    при создании, а не вычисленное сканером.
    """
    existing = existing or {}
    record: dict = {
        "kind": node.kind,
        "name": str(existing.get("name") or "") or _node_name(node),
        "description": description.strip(),
        "acceptance": [str(item).strip() for item in acceptance if str(item).strip()],
    }
    record["domain"] = normalize_domain(domain) if node.kind == "endpoint" else ""
    if node.planned:
        record["planned"] = True
        if existing.get("studio_owned"):
            record["studio_owned"] = True
        record["target_file"] = str(
            existing.get("target_file") or existing.get("file_path") or node.file_path
        )
        details = existing.get("details")
        record["details"] = dict(details) if isinstance(details, dict) else {}
    return record


def save_node_metadata(
    project_root: Path,
    node: Node,
    description: str,
    acceptance: list[str],
    domain: str,
) -> SaveResult:
    """Сохранить описание/критерии/домен узла в metadata.json.

    Запись компактная: без file_path и вычисляемых details. Старая «жирная»
    запись этого же узла худеет при сохранении; остальные записи не меняются.

    Между процессами — сверка хэша: если файл изменили, пока мы считали,
    один раз перечитываем его и применяем правку заново.
    """
    path = metadata_path(project_root)
    baseline = _file_hash(path)
    metadata = read_metadata(project_root)
    if metadata.write_blocked:
        raise MetadataError(
            "W_METADATA_INVALID",
            "metadata.json не читается — запись остановлена, файл не изменён. "
            "Исправьте JSON или удалите файл вручную.",
        )

    def apply(target: Metadata) -> None:
        existing = target.nodes.get(node.id) or {}
        target.nodes[node.id] = _clean_node(
            _record_for_node(node, description, acceptance, domain, existing)
        )

    before = metadata_payload(metadata)
    apply(metadata)
    after = metadata_payload(metadata)
    expected = baseline
    if _file_hash(path) != baseline:
        metadata = read_metadata(project_root)
        if metadata.write_blocked:
            raise MetadataError(
                "W_METADATA_INVALID",
                "metadata.json изменился и перестал читаться — запись остановлена, "
                "файл не изменён.",
            )
        expected = _file_hash(path)
        before = metadata_payload(metadata)
        apply(metadata)
        after = metadata_payload(metadata)
    changed = before != after
    if changed and not write_metadata(project_root, metadata, expected):
        if _file_hash(path) != expected:
            raise MetadataError(
                "W_METADATA_INVALID",
                "metadata.json изменили во время сохранения — повторите правку, "
                "файл не изменён.",
            )
        raise MetadataError("not_writable", "Не удалось записать .studio/metadata.json")
    return SaveResult(
        changed=changed,
        new_hash=hashlib.sha1(after.encode("utf-8")).hexdigest(),
    )


def _mutate_metadata(project_root: Path, mutate) -> SaveResult:
    """Apply one focused edit with the same optimistic write protection as node edits."""
    path = metadata_path(project_root)
    for attempt in range(2):
        baseline = _file_hash(path)
        metadata = read_metadata(project_root)
        if metadata.write_blocked:
            raise MetadataError(
                "W_METADATA_INVALID",
                "metadata.json не читается — запись остановлена, файл не изменён. "
                "Исправьте JSON или удалите файл вручную.",
            )
        before = metadata_payload(metadata)
        mutate(metadata)
        after = metadata_payload(metadata)
        if before == after:
            return SaveResult(False, hashlib.sha1(after.encode("utf-8")).hexdigest())
        if write_metadata(project_root, metadata, baseline):
            return SaveResult(True, hashlib.sha1(after.encode("utf-8")).hexdigest())
        if attempt == 0 and _file_hash(path) != baseline:
            continue
        raise MetadataError(
            "not_writable",
            "metadata.json изменили во время сохранения или он недоступен — "
            "обновите карту и повторите правку.",
        )
    raise MetadataError("not_writable", "Не удалось записать .studio/metadata.json")


def save_graph_node(
    project_root: Path,
    node_id: str,
    kind: str,
    name: str,
    description: str,
    acceptance: list[str],
    domain: str,
    target_file: str,
    details: dict,
    parent_id: str = "",
    edge_kind: str = "",
) -> SaveResult:
    """Create/update a visual TODO node and optionally connect it to a parent."""
    record = _clean_node({
        "kind": kind,
        "name": name.strip(),
        "description": description.strip(),
        "acceptance": acceptance,
        "domain": normalize_domain(domain) if kind == "endpoint" else "",
        "planned": True,
        "studio_owned": True,
        "target_file": target_file.strip(),
        "details": details,
    })

    def apply(metadata: Metadata) -> None:
        existing = metadata.nodes.get(node_id)
        if existing and not existing.get("studio_owned"):
            raise MetadataError(
                "invalid_input",
                "Можно редактировать только узлы, созданные в графе",
            )
        metadata.nodes[node_id] = record
        if parent_id and edge_kind:
            source_id, target_id = parent_id, node_id
            if kind == "test" and edge_kind == "tests":
                source_id, target_id = node_id, parent_id
            edge = {"from": source_id, "to": target_id, "kind": edge_kind, "label": ""}
            metadata.edges = [
                item for item in metadata.edges
                if not (
                    item.get("from") == source_id
                    and item.get("to") == target_id
                    and item.get("kind") == edge_kind
                )
            ]
            metadata.edges.append(edge)

    return _mutate_metadata(project_root, apply)


def delete_graph_node(
    project_root: Path,
    node_id: str,
    node_ids: set[str] | None = None,
) -> SaveResult:
    """Delete only graph-owned TODO nodes and the metadata links that touch them."""
    removable = set(node_ids or {node_id})

    def apply(metadata: Metadata) -> None:
        target = metadata.nodes.get(node_id)
        if not target or not target.get("studio_owned"):
            raise MetadataError("invalid_input", "Удалять можно только узел, созданный в графе")
        for item in removable:
            record = metadata.nodes.get(item)
            if record and record.get("studio_owned"):
                metadata.nodes.pop(item, None)
        metadata.edges = [
            item for item in metadata.edges
            if item.get("from") not in removable and item.get("to") not in removable
        ]

    return _mutate_metadata(project_root, apply)


__all__ = [
    "METADATA_NAME",
    "METADATA_VERSION",
    "Metadata",
    "MetadataError",
    "SaveResult",
    "metadata_hash",
    "metadata_path",
    "metadata_payload",
    "read_metadata",
    "save_node_metadata",
    "save_graph_node",
    "delete_graph_node",
    "studio_dir",
    "write_metadata",
]

