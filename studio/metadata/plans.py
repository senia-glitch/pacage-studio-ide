"""План сценария: цепочка слоёв endpoint → scenario → io → model (+ test).

Чистые функции: здесь нет ни чтения, ни записи диска. `build_plan` собирает
данные плана из одного текста, `validate_plan` проверяет конфликты с графом и
другими планами, `resolve_plan` живёт в `studio.scanner.plans`.

Ключ плана — slug (латиница, snake_case). Пользователь может задать его вручную;
иначе он строится транслитерацией заголовка.
"""

from __future__ import annotations

import datetime as _dt
import keyword
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath

from studio.diagnostics import Diagnostic
from studio.env import ProjectEnv, dotted_to_path
from studio.metamodel import GraphIndex, Node, node_anchor

LAYERS: tuple[str, ...] = ("endpoint", "scenario", "io", "model", "test")
PLAN_LAYERS: tuple[str, ...] = ("endpoint", "scenario", "io", "model", "test")
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SEPARATORS = re.compile(r"[^a-z0-9]+")
_KNOWN_PLAN_KEYS = frozenset(
    {
        "title",
        "description",
        "acceptance",
        "domain",
        "http_method",
        "path",
        "uses_db",
        "with_test",
        "created_at",
        "layers",
    }
)
_KNOWN_LAYER_KEYS = frozenset({"layer", "suggested_name", "target_file"})

# Транслитерация кириллицы: встроенная таблица, без внешних зависимостей.
_TRANSLIT: dict[str, str] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


class PlanError(Exception):
    """План нельзя собрать или прочитать. message — понятный пользователю текст."""


@dataclass
class PlanLayer:
    layer: str
    suggested_name: str = ""
    target_file: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Plan:
    slug: str
    title: str = ""
    description: str = ""
    acceptance: list[str] = field(default_factory=list)
    domain: str = ""
    http_method: str = ""
    path: str = ""
    uses_db: bool = False
    with_test: bool = False
    created_at: str = ""
    layers: list[PlanLayer] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def _transliterate(text: str) -> str:
    out: list[str] = []
    for char in str(text or "").lower():
        if char in _TRANSLIT:
            out.append(_TRANSLIT[char])
        elif char.isascii() and char.isalnum():
            out.append(char)
        else:
            out.append(" ")
    return "".join(out)


def slugify(title: str) -> str:
    """Заголовок → slug snake_case. Пусто или не с буквы — PlanError."""
    text = _SEPARATORS.sub("_", _transliterate(title)).strip("_")
    if not text:
        raise PlanError(
            f"из заголовка {title!r} не получается slug: нужны буквы или цифры"
        )
    if not text[0].isalpha():
        raise PlanError(
            f"slug {text!r} должен начинаться с латинской буквы; задайте slug вручную"
        )
    return text


def pascal_case(slug: str) -> str:
    return "".join(part.capitalize() for part in slug.split("_") if part)


def _acceptance_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [
            re.sub(r"^-\s*", "", line.strip())
            for line in value.splitlines()
            if line.strip()
        ]
    elif isinstance(value, list):
        items = []
        for item in value:
            if not isinstance(item, str):
                raise PlanError("критерии приёмки должны быть строками")
            items.append(item.strip())
    else:
        raise PlanError("критерии приёмки должны быть списком строк")
    return [item for item in items if item]


def _as_text(raw: dict, key: str) -> str:
    value = raw.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PlanError(f"поле {key!r} должно быть строкой")
    return value


def _as_bool(raw: dict, key: str) -> bool:
    value = raw.get(key)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise PlanError(f"поле {key!r} должно быть true или false")
    return value


def build_plan(
    title: str,
    slug: str,
    description: str,
    acceptance,
    domain: str,
    http_method: str,
    path: str,
    uses_db: bool,
    with_test: bool,
    env: ProjectEnv,
) -> Plan:
    """Собрать план: слои endpoint, scenario, [io, model], [test]."""
    clean_slug = str(slug or "").strip() or slugify(title)
    if not SLUG_RE.match(clean_slug):
        raise PlanError(
            f"slug {clean_slug!r}: нужен snake_case из латинских букв, цифр и «_»"
        )
    method = str(http_method or "").strip().lower()
    target_path = str(path or "").strip()
    if target_path and not target_path.startswith("/"):
        target_path = "/" + target_path

    root = env.project_root
    endpoint_file = (
        dotted_to_path(root, env.routes_package) / f"{clean_slug}.py"
    ).relative_to(root).as_posix()
    scenario_file = (
        dotted_to_path(root, env.core_discover) / f"{clean_slug}.py"
    ).relative_to(root).as_posix()

    layers = [
        PlanLayer("endpoint", clean_slug, endpoint_file),
        PlanLayer("scenario", f"{pascal_case(clean_slug)}Scenario", scenario_file),
    ]
    if uses_db:
        layers.extend([PlanLayer("io"), PlanLayer("model")])
    if with_test:
        layers.append(
            PlanLayer("test", f"test_{clean_slug}", f"tests/test_{clean_slug}.py")
        )

    return Plan(
        slug=clean_slug,
        title=str(title or "").strip(),
        description=str(description or "").strip(),
        acceptance=_acceptance_list(acceptance),
        domain=str(domain or "").strip(),
        http_method=method,
        path=target_path,
        uses_db=bool(uses_db),
        with_test=bool(with_test),
        created_at=_dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        layers=layers,
    )


def plan_from_record(slug: str, raw) -> Plan:
    """Прочитать запись плана из metadata.json. Битые типы — PlanError."""
    if not isinstance(raw, dict):
        raise PlanError("запись плана должна быть объектом")
    layers_raw = raw.get("layers")
    if layers_raw is None:
        layers_raw = []
    if not isinstance(layers_raw, list):
        raise PlanError("поле 'layers' должно быть списком")
    layers: list[PlanLayer] = []
    for item in layers_raw:
        if not isinstance(item, dict):
            raise PlanError("слой плана должен быть объектом")
        layer = str(item.get("layer") or "").strip()
        if layer not in LAYERS:
            raise PlanError(
                f"неизвестный слой {layer!r}; допустимы: " + ", ".join(LAYERS)
            )
        layers.append(
            PlanLayer(
                layer=layer,
                suggested_name=_as_text(item, "suggested_name").strip(),
                target_file=_as_text(item, "target_file").strip(),
                extra={
                    key: value
                    for key, value in item.items()
                    if key not in _KNOWN_LAYER_KEYS
                },
            )
        )
    return Plan(
        slug=str(slug),
        title=_as_text(raw, "title").strip(),
        description=_as_text(raw, "description").strip(),
        acceptance=_acceptance_list(raw.get("acceptance")),
        domain=_as_text(raw, "domain").strip(),
        http_method=_as_text(raw, "http_method").strip().lower(),
        path=_as_text(raw, "path").strip(),
        uses_db=_as_bool(raw, "uses_db"),
        with_test=_as_bool(raw, "with_test"),
        created_at=_as_text(raw, "created_at").strip(),
        layers=layers,
        extra={
            key: value for key, value in raw.items() if key not in _KNOWN_PLAN_KEYS
        },
    )


def plan_to_record(plan: Plan) -> dict:
    """Запись плана для metadata.json: известные поля и сохранённые чужие ключи."""
    record: dict = {
        "title": plan.title,
        "description": plan.description,
        "acceptance": list(plan.acceptance),
        "domain": plan.domain,
        "http_method": plan.http_method,
        "path": plan.path,
        "uses_db": plan.uses_db,
        "with_test": plan.with_test,
        "created_at": plan.created_at,
        "layers": [
            {
                "layer": item.layer,
                "suggested_name": item.suggested_name,
                "target_file": item.target_file,
                **item.extra,
            }
            for item in plan.layers
        ],
    }
    record.update(plan.extra)
    return record


# ---------------------------------------------------------------- слои плана


def plan_layer(plan: Plan, layer: str) -> PlanLayer | None:
    for item in plan.layers:
        if item.layer == layer:
            return item
    return None


def layer_name(plan: Plan, layer: str) -> str:
    """Имя слоя: из плана, иначе вычисленное из slug."""
    item = plan_layer(plan, layer)
    if item is not None and item.suggested_name:
        return item.suggested_name
    if layer == "endpoint":
        return plan.slug
    if layer == "scenario":
        return f"{pascal_case(plan.slug)}Scenario"
    if layer == "test":
        return f"test_{plan.slug}"
    return ""


def layer_file(plan: Plan, layer: str) -> str:
    item = plan_layer(plan, layer)
    return item.target_file if item is not None else ""


def has_layer(plan: Plan, layer: str) -> bool:
    return plan_layer(plan, layer) is not None


# ------------------------------------------------------------- проверка плана


def _node_names(node: Node) -> set[str]:
    names = {node.id, node.label, node_anchor(node)}
    details = node.details or {}
    for key in (
        "function_name",
        "class_name",
        "test_name",
        "name",
        "method",
        "tablename",
        "table",
    ):
        value = details.get(key)
        if value:
            names.add(str(value))
    names.discard("")
    return names


def _plan_slug(item) -> str:
    return str(getattr(item, "slug", "") or "")


def _plan_items(item) -> list:
    return list(getattr(item, "layers", []) or [])


def _layer_of(item) -> str:
    return str(getattr(item, "layer", "") or "")


def _inside_project(target: str) -> bool:
    posix = PurePosixPath(target)
    windows = PureWindowsPath(target)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return False
    return ".." not in posix.parts and ".." not in windows.parts


def _diagnostic(code: str, message: str) -> Diagnostic:
    return Diagnostic(
        level="warning",
        code=code,
        message=message,
        file=".studio/metadata.json",
    )


def validate_plan(plan: Plan, graph_index: GraphIndex | None) -> list[Diagnostic]:
    """Конфликты плана с графом и другими планами. Пустой список — всё чисто."""
    issues: list[Diagnostic] = []
    seen: set[tuple[str, str]] = set()

    def add(code: str, message: str) -> None:
        if (code, message) not in seen:
            seen.add((code, message))
            issues.append(_diagnostic(code, message))

    if not SLUG_RE.match(plan.slug) or keyword.iskeyword(plan.slug):
        add(
            "W_PLAN_INVALID",
            f"slug {plan.slug!r}: нужен snake_case и не ключевое слово Python",
        )
    if not plan.description.strip():
        add(
            "W_PLAN_NO_DESCRIPTION",
            f"план {plan.slug!r}: нет описания — ТЗ по нему не собрать",
        )

    nodes = list(graph_index.by_id.values()) if graph_index is not None else []
    other_plans = list(getattr(graph_index, "plans", []) or []) if graph_index is not None else []
    other_plans = [item for item in other_plans if _plan_slug(item) != plan.slug]

    occupied_slugs = {_plan_slug(item) for item in other_plans if _plan_slug(item)}
    if plan.slug in occupied_slugs:
        add("W_PLAN_CONFLICT", f"slug {plan.slug!r} уже занят другим планом")

    occupied_names: dict[str, str] = {}
    occupied_files: dict[str, str] = {}
    for node in nodes:
        if node.plan == plan.slug:
            continue
        for name in _node_names(node):
            occupied_names.setdefault(name, f"узлом {node.id}")
        if node.file_path:
            occupied_files.setdefault(node.file_path, f"узлом {node.id}")
    for other in other_plans:
        slug = _plan_slug(other)
        occupied_names.setdefault(slug, f"планом {slug}")
        for item in _plan_items(other):
            name = str(getattr(item, "suggested_name", "") or "")
            if name:
                occupied_names.setdefault(name, f"планом {slug}")
            target = str(getattr(item, "target_file", "") or "")
            if target:
                occupied_files.setdefault(target, f"планом {slug}")

    own_files: dict[str, str] = {}
    for item in plan.layers:
        layer = item.layer
        name = layer_name(plan, layer)
        if name:
            if keyword.iskeyword(name):
                add("W_PLAN_INVALID", f"слой {layer}: имя {name!r} — ключевое слово Python")
            owner = occupied_names.get(name)
            if owner is not None:
                add(
                    "W_PLAN_CONFLICT",
                    f"слой {layer}: имя {name!r} занято {owner}",
                )
        target = item.target_file
        if not target:
            continue
        if not _inside_project(target):
            add(
                "W_PLAN_INVALID",
                f"слой {layer}: файл {target!r} вне проекта",
            )
            continue
        if target in own_files:
            add(
                "W_PLAN_CONFLICT",
                f"слой {layer}: файл {target!r} уже занят слоем {own_files[target]}",
            )
        own_files[target] = layer
        owner = occupied_files.get(target)
        if owner is not None:
            add(
                "W_PLAN_CONFLICT",
                f"слой {layer}: файл {target!r} занят {owner}",
            )
    return issues


__all__ = [
    "LAYERS",
    "Plan",
    "PlanError",
    "PlanLayer",
    "build_plan",
    "has_layer",
    "layer_file",
    "layer_name",
    "pascal_case",
    "plan_from_record",
    "plan_layer",
    "plan_to_record",
    "slugify",
    "validate_plan",
]
