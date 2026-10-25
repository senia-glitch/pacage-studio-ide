"""Создание плана сценария одной операцией.

План — цепочка слоёв endpoint → scenario → [io, model] → [test]. Здесь только
чистая проверка и запись: план и планируемые узлы живут в `.studio/metadata.json`,
а для endpoint/scenario/test создаётся файл-скелет ровно с одной TODO-строкой.
Код проекта не перезаписывается, операция «всё или ничего».
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field, replace
from pathlib import Path

from studio import log as slog
from studio._atomic import atomic_write
from studio.creator import HTTP_METHODS, CreatedFile, CreateError
from studio.diagnostics import Sink, code_level
from studio.env import ProjectEnv
from studio.metadata import (
    MetadataError,
    metadata_path,
    read_metadata,
    write_metadata,
)
from studio.metadata.plans import (
    Plan,
    PlanError,
    PlanLayer,
    build_plan,
    has_layer,
    layer_file,
    layer_name,
    validate_plan,
)
from studio.metadata.store import Metadata
from studio.metamodel import PlanState, normalize_domain
from studio.scanner import scan
from studio.scanner.plans import resolve_plan

SKELETON_KINDS = ("endpoint", "scenario", "test")
AGENT_NAME_KINDS = ("io", "model")
AGENT_NAME_HINT = "имя выберет агент"


@dataclass
class PlanResult:
    """Итог create_plan: записанный план, файлы и его стартовое состояние."""

    plan: Plan
    created: list[CreatedFile] = field(default_factory=list)
    plan_state: PlanState | None = None

    @property
    def files(self) -> list[str]:
        return [item.path for item in self.created]


@dataclass
class PlanDeleteResult:
    """Итог delete_plan: что удалено и какие скелеты оставлены пользователю."""

    slug: str
    deleted_files: list[str] = field(default_factory=list)
    kept_files: list[str] = field(default_factory=list)
    removed_nodes: list[str] = field(default_factory=list)
    removed_edges: int = 0


def _text(data: dict, key: str, default: str = "") -> str:
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise CreateError(f"поле {key!r} должно быть строкой", "W_PLAN_INVALID")
    return value


def _flag(data: dict, key: str, default: bool = False) -> bool:
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise CreateError(f"поле {key!r} должно быть true или false", "W_PLAN_INVALID")
    return value


def _acceptance(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [
            line.strip().lstrip("- ").strip()
            for line in value.splitlines()
            if line.strip()
        ]
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise CreateError("критерии приёмки должны быть строками", "W_PLAN_INVALID")
        return [item.strip() for item in value if item.strip()]
    raise CreateError("критерии приёмки должны быть списком строк", "W_PLAN_INVALID")


def _normalize(data, env: ProjectEnv) -> Plan:
    if not isinstance(data, dict):
        raise CreateError("данные плана должны быть объектом", "W_PLAN_INVALID")
    title = _text(data, "title")
    slug = _text(data, "slug")
    description = _text(data, "description")
    domain = _text(data, "domain")
    http_method = _text(data, "http_method")
    path = _text(data, "path")
    uses_db = _flag(data, "uses_db")
    with_test = _flag(data, "with_test")
    acceptance = _acceptance(data.get("acceptance"))
    method = http_method.strip().lower()
    if method and method not in HTTP_METHODS:
        raise CreateError(f"неизвестный HTTP-метод: {method!r}", "W_PLAN_INVALID")
    try:
        plan = build_plan(
            title=title,
            slug=slug,
            description=description,
            acceptance=acceptance,
            domain=domain,
            http_method=method,
            path=path,
            uses_db=uses_db,
            with_test=with_test,
            env=env,
        )
    except PlanError as exc:
        raise CreateError(str(exc), "W_PLAN_INVALID") from exc
    return plan


def _layer_dict(item: PlanLayer) -> dict:
    agent = item.layer in AGENT_NAME_KINDS
    name = item.suggested_name
    return {
        "layer": item.layer,
        "suggested_name": name,
        "name": name,
        "target_file": item.target_file,
        "name_by_agent": agent,
        "name_hint": AGENT_NAME_HINT if agent else "",
    }


def _unnamed_layers(uses_db: bool, with_test: bool) -> list[PlanLayer]:
    layers = [PlanLayer("endpoint"), PlanLayer("scenario")]
    if uses_db:
        layers.extend([PlanLayer("io"), PlanLayer("model")])
    if with_test:
        layers.append(PlanLayer("test"))
    return layers


def _conflict(code: str, message: str, *, file: str = "", hint: str = "") -> dict:
    return {
        "code": code,
        "level": code_level(code),
        "message": message,
        "file": file,
        "hint": hint,
    }


def _scan(env: ProjectEnv):
    try:
        return scan(env.project_root)
    except Exception as exc:  # noqa: BLE001 — отказ вместо «имён нет»
        raise CreateError(
            f"Не удалось проверить занятость имён: {exc}", "E_INTERNAL"
        ) from exc


def _inside_project(root: Path, rel: str) -> bool:
    if not rel:
        return True
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _plan_conflicts(plan: Plan, env: ProjectEnv, metadata: Metadata, graph) -> list[dict]:
    issues: list[dict] = []
    if plan.slug in metadata.plans:
        issues.append(
            _conflict(
                "W_PLAN_CONFLICT",
                f"план {plan.slug!r} уже существует",
                file=".studio/metadata.json",
            )
        )
    root = Path(env.project_root).resolve()
    for item in plan.layers:
        target = item.target_file
        if not target:
            continue
        if not _inside_project(root, target):
            issues.append(
                _conflict(
                    "W_PLAN_INVALID",
                    f"слой {item.layer}: файл {target!r} вне проекта",
                    file=target,
                )
            )
            continue
        if (root / target).exists():
            issues.append(
                _conflict(
                    "W_PLAN_CONFLICT",
                    f"файл уже существует: {target}",
                    file=target,
                )
            )
    for diagnostic in validate_plan(plan, graph.index() if graph is not None else None):
        issues.append(
            _conflict(
                diagnostic.code,
                diagnostic.message,
                file=diagnostic.file,
                hint=diagnostic.hint,
            )
        )
    unique: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in issues:
        key = (item["code"], item["message"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _metadata_problems(root: Path) -> tuple[Metadata, list[dict]]:
    sink = Sink()
    metadata = read_metadata(root, sink)
    issues = [
        _conflict(item.code, item.message, file=item.file, hint=item.hint)
        for item in sink.diagnostics()
    ]
    if metadata.write_blocked and not issues:
        issues.append(
            _conflict(
                "W_METADATA_INVALID",
                "metadata.json новее поддерживаемой версии — запись запрещена",
                file=".studio/metadata.json",
            )
        )
    return metadata, issues


def preview_plan(data, env: ProjectEnv) -> dict:
    """Предложить цепочку и конфликты, ничего не записывая."""
    if not isinstance(data, dict):
        return {
            "ok": False,
            "slug": "",
            "layers": [],
            "conflicts": [
                _conflict("W_PLAN_INVALID", "данные плана должны быть объектом")
            ],
        }

    title = data.get("title") if isinstance(data.get("title"), str) else ""
    slug = data.get("slug") if isinstance(data.get("slug"), str) else ""
    try:
        uses_db = _flag(data, "uses_db")
        with_test = _flag(data, "with_test")
    except CreateError as exc:
        return {
            "ok": False,
            "slug": slug,
            "layers": [],
            "conflicts": [_conflict(exc.code, str(exc))],
        }

    if not title.strip() and not slug.strip():
        layers = _unnamed_layers(uses_db, with_test)
        return {
            "ok": True,
            "slug": "",
            "title": title,
            "layers": [_layer_dict(item) for item in layers],
            "conflicts": [],
        }

    try:
        plan = _normalize(data, env)
    except CreateError as exc:
        return {
            "ok": False,
            "slug": slug,
            "title": title,
            "layers": [
                _layer_dict(item) for item in _unnamed_layers(uses_db, with_test)
            ],
            "conflicts": [_conflict(exc.code, str(exc))],
        }

    root = Path(env.project_root).resolve()
    metadata, issues = _metadata_problems(root)
    try:
        graph = _scan(env)
    except CreateError as exc:
        issues.append(_conflict(exc.code, str(exc)))
        graph = None
    issues.extend(_plan_conflicts(plan, env, metadata, graph))
    return {
        "ok": not issues,
        "slug": plan.slug,
        "title": plan.title,
        "layers": [_layer_dict(item) for item in plan.layers],
        "conflicts": issues,
    }


# ---------------------------------------------------------------- запись плана


def _planned_nodes(plan: Plan) -> list[tuple[str, str, str, str]]:
    """(kind, name, target_file, id) только для слоёв, у которых есть файл."""
    out: list[tuple[str, str, str, str]] = []
    for kind in SKELETON_KINDS:
        if not has_layer(plan, kind):
            continue
        name = layer_name(plan, kind)
        target = layer_file(plan, kind)
        if not name or not target:
            continue
        out.append((kind, name, target, f"{kind}:{target}:{name}"))
    return out


def _skeleton_files(plan: Plan) -> list[tuple[str, str, str, str]]:
    out: list[tuple[str, str, str, str]] = []
    for kind, name, target, _node_id in _planned_nodes(plan):
        out.append((kind, name, target, f"# TODO: {kind} {name}\n"))
    return out


def _node_records(plan: Plan) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for kind, name, target, node_id in _planned_nodes(plan):
        details: dict = {}
        record: dict = {
            "kind": kind,
            "name": name,
            "description": plan.description,
            "acceptance": list(plan.acceptance),
            "planned": True,
            "target_file": target,
            "details": details,
        }
        if kind == "endpoint":
            details["http_method"] = plan.http_method
            details["path"] = plan.path
            record["domain"] = normalize_domain(plan.domain)
        records[node_id] = record
    return records


def _edge_records(plan: Plan) -> list[dict]:
    ids = {kind: node_id for kind, _name, _target, node_id in _planned_nodes(plan)}
    edges: list[dict] = []
    if "endpoint" in ids and "scenario" in ids:
        edges.append(
            {"from": ids["endpoint"], "to": ids["scenario"], "kind": "dispatch"}
        )
    if "test" in ids and "endpoint" in ids:
        edges.append({"from": ids["test"], "to": ids["endpoint"], "kind": "tests"})
    return edges


def _missing_dirs(root: Path, paths: list[Path]) -> list[Path]:
    missing: list[Path] = []
    for path in paths:
        current = path.parent
        while current != root and not current.exists() and current not in missing:
            missing.append(current)
            current = current.parent
    return missing


def _restore_metadata(root: Path, backup: bytes | None) -> list[str]:
    path = metadata_path(root)
    if backup is None:
        try:
            if path.is_file():
                path.unlink()
        except OSError as exc:
            return [f"{path}: {exc}"]
        return []
    try:
        text = backup.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"{path}: бэкап не в UTF-8 ({exc})"]
    try:
        if atomic_write(path, text):
            return []
    except OSError as exc:
        return [f"{path}: {exc}"]
    return [f"{path}: запись не удалась"]


def _rollback(
    root: Path,
    written: list[Path],
    backup: bytes | None,
    created_dirs: list[Path],
) -> list[str]:
    failures: list[str] = []
    for target in reversed(written):
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            failures.append(f"{target}: {exc}")
    failures.extend(_restore_metadata(root, backup))
    for directory in sorted(created_dirs, key=lambda item: len(item.parts), reverse=True):
        with contextlib.suppress(OSError):
            directory.rmdir()
    if failures:
        slog.step("откат плана: " + "; ".join(failures))
    return failures


def create_plan(data, env: ProjectEnv) -> PlanResult:
    """Проверить, записать скелеты и metadata.json. Всё или ничего."""
    plan = _normalize(data, env)
    root = Path(env.project_root).resolve()
    graph = _scan(env)
    metadata, issues = _metadata_problems(root)
    issues.extend(_plan_conflicts(plan, env, metadata, graph))
    if issues:
        message = "; ".join(item["message"] for item in issues)
        raise CreateError(message, issues[0]["code"])

    files = _skeleton_files(plan)
    records = _node_records(plan)
    edges = _edge_records(plan)
    metadata_file = metadata_path(root)
    backup = metadata_file.read_bytes() if metadata_file.is_file() else None
    targets = [(root / target, content) for _kind, _name, target, content in files]
    created_dirs = _missing_dirs(root, [target for target, _ in targets] + [metadata_file])

    written: list[Path] = []
    try:
        for target, content in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise CreateError(
                    f"файл уже существует: {target.relative_to(root).as_posix()}",
                    "W_PLAN_CONFLICT",
                )
            if not atomic_write(target, content):
                rel = target.relative_to(root).as_posix()
                raise CreateError(f"не удалось записать {rel}", "E_INTERNAL")
            written.append(target)

        metadata.nodes.update(records)
        known = {
            (str(item.get("from")), str(item.get("kind")), str(item.get("to")))
            for item in metadata.edges
        }
        for edge in edges:
            key = (edge["from"], edge["kind"], edge["to"])
            if key not in known:
                metadata.edges.append(edge)
                known.add(key)
        metadata.plans[plan.slug] = plan
        if not write_metadata(root, metadata):
            raise CreateError(
                "не удалось записать .studio/metadata.json", "not_writable"
            )
    except (CreateError, OSError, MetadataError) as exc:
        failures = _rollback(root, written, backup, created_dirs)
        if not failures:
            if isinstance(exc, CreateError):
                raise
            raise CreateError(f"не удалось создать план: {exc}", "E_INTERNAL") from exc
        suffix = f"файлы не удалось убрать: {'; '.join(failures)}"
        code = getattr(exc, "code", "E_INTERNAL")
        raise CreateError(f"{exc}; {suffix}", code) from exc

    created = [
        CreatedFile(path=target, kind=kind, name=name)
        for kind, name, target, _content in files
    ]
    return PlanResult(
        plan=plan,
        created=created,
        plan_state=resolve_plan(graph, plan),
    )


# ----------------------------------------------------------- правка и удаление


def _require_slug(data) -> str:
    if not isinstance(data, dict):
        raise CreateError("данные плана должны быть объектом", "W_PLAN_INVALID")
    slug = _text(data, "slug").strip()
    if not slug:
        raise CreateError("нужен slug плана", "W_PLAN_INVALID")
    return slug


def _sync_planned_nodes(metadata: Metadata, plan: Plan) -> None:
    for kind, _name, _target, node_id in _planned_nodes(plan):
        record = metadata.nodes.get(node_id)
        if not record or not record.get("planned"):
            continue
        record["description"] = plan.description
        record["acceptance"] = list(plan.acceptance)
        if kind == "endpoint":
            record["domain"] = normalize_domain(plan.domain)
            details = dict(record.get("details") or {})
            details["http_method"] = plan.http_method
            details["path"] = plan.path
            record["details"] = details


def update_plan(data, env: ProjectEnv) -> Plan:
    """Изменить описание, критерии, домен, метод и путь; слои и файлы не трогать."""
    slug = _require_slug(data)
    root = Path(env.project_root).resolve()
    metadata, issues = _metadata_problems(root)
    if issues:
        raise CreateError(issues[0]["message"], issues[0]["code"])
    plan = metadata.plans.get(slug)
    if plan is None:
        raise CreateError(f"план {slug!r} не найден", "plan_not_found")

    updated = replace(plan, layers=list(plan.layers))
    if "description" in data:
        updated.description = _text(data, "description").strip()
    if "acceptance" in data:
        updated.acceptance = _acceptance(data.get("acceptance"))
    if "domain" in data:
        updated.domain = _text(data, "domain").strip()
    if "http_method" in data:
        method = _text(data, "http_method").strip().lower()
        if method and method not in HTTP_METHODS:
            raise CreateError(f"неизвестный HTTP-метод: {method!r}", "W_PLAN_INVALID")
        updated.http_method = method
    if "path" in data:
        path = _text(data, "path").strip()
        if path and not path.startswith("/"):
            path = "/" + path
        updated.path = path

    metadata.plans[slug] = updated
    _sync_planned_nodes(metadata, updated)
    if not write_metadata(root, metadata):
        raise CreateError("не удалось записать .studio/metadata.json", "not_writable")
    return updated


def _is_todo_skeleton(text: str) -> bool:
    lines = text.splitlines()
    return len(lines) == 1 and lines[0].startswith("# TODO:")


def delete_plan(data, env: ProjectEnv) -> PlanDeleteResult:
    """Удалить план, его planned-узлы и рёбра; скелеты — только нетронутые."""
    slug = _require_slug(data)
    root = Path(env.project_root).resolve()
    metadata, issues = _metadata_problems(root)
    if issues:
        raise CreateError(issues[0]["message"], issues[0]["code"])
    plan = metadata.plans.get(slug)
    if plan is None:
        raise CreateError(f"план {slug!r} не найден", "plan_not_found")

    planned = _planned_nodes(plan)
    removed_nodes: list[str] = []
    for _kind, _name, _target, node_id in planned:
        record = metadata.nodes.get(node_id)
        if record is None or not record.get("planned"):
            continue
        del metadata.nodes[node_id]
        removed_nodes.append(node_id)

    removed_edges = 0
    kept_edges: list[dict] = []
    removed = set(removed_nodes)
    for edge in metadata.edges:
        if str(edge.get("from") or "") in removed or str(edge.get("to") or "") in removed:
            removed_edges += 1
        else:
            kept_edges.append(edge)
    metadata.edges = kept_edges
    del metadata.plans[slug]
    if not write_metadata(root, metadata):
        raise CreateError("не удалось записать .studio/metadata.json", "not_writable")

    result = PlanDeleteResult(
        slug=slug,
        removed_nodes=removed_nodes,
        removed_edges=removed_edges,
    )
    seen: set[str] = set()
    for _kind, _name, target, _node_id in planned:
        if not target or target in seen:
            continue
        seen.add(target)
        if not _inside_project(root, target):
            continue
        path = root / target
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            result.kept_files.append(target)
            continue
        if not _is_todo_skeleton(text):
            result.kept_files.append(target)
            continue
        try:
            path.unlink()
            result.deleted_files.append(target)
        except OSError as exc:
            slog.step(f"план {slug}: скелет не удалён ({target}): {exc}")
            result.kept_files.append(target)
    return result


__all__ = [
    "AGENT_NAME_HINT",
    "PlanDeleteResult",
    "PlanResult",
    "create_plan",
    "delete_plan",
    "preview_plan",
    "update_plan",
]
