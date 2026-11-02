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
_UNSET = object()


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
    version = 1 if "version" not in raw else raw_version
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
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
    write_blocked = False

    def invalid(code: str, message: str, hint: str) -> None:
        nonlocal write_blocked
        write_blocked = True
        if sink is not None:
            sink.add(
                code,
                message,
                file=f"{STUDIO_DIR}/{METADATA_NAME}",
                hint=hint,
            )

    nodes: dict[str, dict] = {}
    raw_nodes = raw.get("nodes", {})
    if isinstance(raw_nodes, dict):
        for key, value in raw_nodes.items():
            record = _clean_node(value)
            if record:
                record.setdefault("kind", str(key).split(":", 1)[0])
                nodes[str(key)] = record
            else:
                invalid(
                    "W_METADATA_INVALID",
                    f"metadata.json: узел {key!r} должен быть объектом",
                    "Исправьте запись узла; сохранение заблокировано, чтобы не потерять данные.",
                )
    else:
        invalid(
            "W_METADATA_INVALID",
            "metadata.json: nodes должен быть объектом «id → узел»",
            "Исправьте nodes; сохранение заблокировано, чтобы не потерять данные.",
        )
    edges: list[dict] = []
    raw_edges = raw.get("edges", [])
    if isinstance(raw_edges, list):
        for value in raw_edges:
            edge = _clean_edge(value)
            if edge is not None:
                edges.append(edge)
            else:
                invalid(
                    "W_METADATA_INVALID",
                    "metadata.json: некорректная связь пропущена",
                    "Исправьте связь; сохранение заблокировано, чтобы не потерять данные.",
                )
    else:
        invalid(
            "W_METADATA_INVALID",
            "metadata.json: edges должен быть списком",
            "Исправьте edges; сохранение заблокировано, чтобы не потерять данные.",
        )
    plans: dict[str, Plan] = {}
    raw_plans = raw.get("plans")
    if raw_plans is not None:
        if not isinstance(raw_plans, dict):
            invalid(
                "W_PLAN_INVALID",
                "metadata.json: plans должен быть объектом «slug → план»",
                "Исправьте plans; сохранение заблокировано, чтобы не потерять данные.",
            )
        else:
            for key, value in raw_plans.items():
                try:
                    plan = plan_from_record(str(key), value)
                except PlanError as exc:
                    invalid(
                        "W_PLAN_INVALID",
                        f"metadata.json: план {key!r} не читается: {exc}",
                        "Исправьте поля плана; сохранение заблокировано, "
                        "чтобы не потерять данные.",
                    )
                    continue
                plans[str(key)] = plan
    elif "plans" in raw:
        invalid(
            "W_PLAN_INVALID",
            "metadata.json: plans должен быть объектом «slug → план»",
            "Исправьте plans; сохранение заблокировано, чтобы не потерять данные.",
        )
    extra = {
        key: value for key, value in raw.items() if key not in _TOP_LEVEL_FIELDS
    }
    return Metadata(
        version=METADATA_VERSION,
        nodes=nodes,
        edges=edges,
        plans=plans,
        extra=extra,
        write_blocked=write_blocked,
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
    project_root: Path, metadata: Metadata, expected_hash: str | None | object = _UNSET
) -> bool:
    """Атомарно записать metadata.json. False — запись не удалась или запрещена.

    `expected_hash` — хэш файла на момент чтения. Если файл уже изменили,
    запись не выполняется: чужая правка не затирается.
    """
    if metadata.write_blocked:
        return False
    path = metadata_path(project_root)
    if expected_hash is not _UNSET and _file_hash(path) != expected_hash:
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


def save_planned_change(project_root: Path, node_id: str, note: str, acceptance: list[str]) -> SaveResult:
    """Store a proposed change separately from the scanned facts of a code node."""
    def apply(metadata: Metadata) -> None:
        changes = metadata.extra.get("planned_changes")
        if not isinstance(changes, dict):
            changes = {}
            metadata.extra["planned_changes"] = changes
        cleaned = {"note": note.strip(), "acceptance": [str(x).strip() for x in acceptance if str(x).strip()]}
        if cleaned["note"] or cleaned["acceptance"]:
            changes[node_id] = cleaned
        else:
            changes.pop(node_id, None)
    return _mutate_metadata(project_root, apply)


def save_domain_plan(project_root: Path, data: dict) -> SaveResult:
    """Create a planned domain or propose renaming an existing factual domain."""
    source = str(data.get("source") or "").strip()
    from_name = str(data.get("from_name") or "").strip()
    name = str(data.get("name") or "").strip()
    if not name:
        raise MetadataError("invalid_input", "Укажите название домена")
    if len(name) > 120:
        raise MetadataError("invalid_input", "Название домена слишком длинное")
    acceptance = data.get("acceptance") or []
    if isinstance(acceptance, str):
        acceptance = [line.strip() for line in acceptance.splitlines() if line.strip()]
    if not isinstance(acceptance, list):
        raise MetadataError("invalid_input", "Критерии должны быть списком строк")
    record = {"name": name, "note": str(data.get("note") or "").strip(), "acceptance": [str(x).strip() for x in acceptance if str(x).strip()]}
    def apply(metadata: Metadata) -> None:
        domains = metadata.extra.get("planned_domains")
        if not isinstance(domains, list):
            domains = []
            metadata.extra["planned_domains"] = domains
        else:
            domains[:] = [item for item in domains if isinstance(item, dict)]
        changes = metadata.extra.get("domain_changes")
        if not isinstance(changes, dict):
            changes = {}
            metadata.extra["domain_changes"] = changes
        if source:
            if name == source and not record["note"] and not record["acceptance"]:
                changes.pop(source, None)
            else:
                changes[source] = record
        else:
            domain_id = str(data.get("id") or "")
            if not domain_id:
                raise MetadataError("invalid_input", "Не удалось определить идентификатор домена")
            domains[:] = [item for item in domains if str(item.get("id") or "") != str(data.get("id") or "")]
            if not data.get("delete"):
                domains.append({"id": domain_id, **record})
                for node in metadata.nodes.values():
                    details = node.get("details")
                    details = dict(details) if isinstance(details, dict) else {}
                    linked = details.get("domain_plan_id") == domain_id
                    if node.get("studio_owned") and node.get("kind") == "endpoint" and (linked or node.get("domain") in {name, from_name}):
                        node["domain"] = name
                        details["domain_plan_id"] = domain_id
                        node["details"] = details
    return _mutate_metadata(project_root, apply)


def delete_domain_plan(project_root: Path, domain_id: str) -> SaveResult:
    def apply(metadata: Metadata) -> None:
        domains = metadata.extra.get("planned_domains")
        domain = next((item for item in domains or [] if isinstance(item, dict) and str(item.get("id") or "") == domain_id), None)
        if domain is None:
            raise MetadataError("invalid_input", "Плановый домен не найден")
        removable = {
            key for key, record in metadata.nodes.items()
            if record.get("studio_owned") and (record.get("details") or {}).get("domain_plan_id") == domain_id
        }
        changed = True
        while changed:
            changed = False
            for edge in metadata.edges:
                parent = edge.get("to") if edge.get("kind") == "tests" else edge.get("from")
                child = edge.get("from") if edge.get("kind") == "tests" else edge.get("to")
                record = metadata.nodes.get(child) or {}
                if parent in removable and child not in removable and record.get("studio_owned"):
                    removable.add(child)
                    changed = True
        for node_id in removable:
            metadata.nodes.pop(node_id, None)
        if removable:
            metadata.edges = [edge for edge in metadata.edges if edge.get("from") not in removable and edge.get("to") not in removable]
        if isinstance(domains, list):
            metadata.extra["planned_domains"] = [item for item in domains if isinstance(item, dict) and str(item.get("id") or "") != domain_id]
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
    "save_planned_change",
    "save_domain_plan",
    "delete_domain_plan",
    "studio_dir",
    "write_metadata",
]

