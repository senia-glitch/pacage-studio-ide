"""Чтение/запись .studio/config.toml + схема конфига по умолчанию.

Схема под стек event-infra / core-package / fastbase:
- слой = список путей (папка или файл) + маркеры содержимого;
- домены опциональны, несовпавшие файлы уходят в default_domain;
- несовпавшие ни с чем файлы получают слой "unknown".
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

UNKNOWN_LAYER = "unknown"

DEFAULT_DOMAIN = "app"

DEFAULT_DOMAINS: list[dict] = []

DEFAULT_LAYERS = [
    {"name": "infrastructure", "paths": ["infrastructure", "models.py", "run_infrastructure.py", "alembic"], "order": 0},
    {"name": "logic",          "paths": ["scenarios", "interfaces", "utils"],                                "order": 1},
    {"name": "representation", "paths": ["app/api/v1/routes", "app/api/handlers.py"],                       "order": 2},
    {"name": "schema",         "paths": ["app/schemas"],                                                    "order": 3},
    {"name": "config",         "paths": ["app/core"],                                                       "order": 4},
]

# Правила именования по слою: паттерн абстракции / паттерн реализации (regex, search).
DEFAULT_NAMING = {
    "infrastructure": {"abstraction": r"^Abstract.*$", "implementation": r".+"},
    "logic":          {"abstraction": r"^Abstract.*$", "implementation": r".+"},
    "representation": {"abstraction": r"^Abstract.*$", "implementation": r".+"},
    "schema":         {"abstraction": r"^Abstract.*$", "implementation": r".+"},
    "config":         {"abstraction": r"^Abstract.*$", "implementation": r".+"},
}

# Маркеры слоя по содержимому файла. Виды: decorator, base_class, module_attr.
DEFAULT_MARKERS = {
    "infrastructure": {"base_class": "SQLModel"},
    "logic":          {"decorator": "register_scenario", "base_class": "BaseScenario"},
    "representation": {"module_attr": "router",          "decorator": "scenario_route"},
    "schema":         {"base_class": "BaseModel"},
    "config":         {"base_class": "BaseAppSettings"},
}

MARKER_KINDS = ("decorator", "base_class", "module_attr")

# Разрешённые направления (from_layer → to_layer). Всё остальное — нарушение.
DEFAULT_ALLOWED = [
    ["representation", "logic"],
    ["representation", "schema"],
    ["logic",          "infrastructure"],
    ["logic",          "schema"],
    ["infrastructure", "schema"],
    ["schema",         "schema"],
]


def default_config() -> dict:
    layer_names = [layer["name"] for layer in DEFAULT_LAYERS]
    naming = {name: dict(DEFAULT_NAMING.get(name, {"abstraction": r"^Abstract.*$", "implementation": r".+"})) for name in layer_names}
    markers = {name: dict(DEFAULT_MARKERS.get(name, {})) for name in layer_names}
    return {
        "studio": {
            "project_root": ".",
            "default_domain": DEFAULT_DOMAIN,
            "domains": [dict(d) for d in DEFAULT_DOMAINS],
            "layers": [{"name": layer["name"], "paths": list(layer["paths"]), "order": layer["order"]} for layer in DEFAULT_LAYERS],
            "naming": naming,
            "markers": markers,
            "rules": {"allowed": [list(p) for p in DEFAULT_ALLOWED]},
            "save": {"show_diff": False, "diff_mode": "on_danger"},
        }
    }


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_arr(items: list[str]) -> str:
    return "[" + ", ".join(_toml_str(str(i)) for i in items) + "]"


def dumps(cfg: dict) -> str:
    """Минимальный TOML-сериализатор под фиксированную схему (без внешних зависимостей)."""
    st = cfg.get("studio", {})
    out = ["[studio]",
           f'project_root = {_toml_str(str(st.get("project_root", ".")))}',
           f'default_domain = {_toml_str(str(st.get("default_domain", DEFAULT_DOMAIN)))}', ""]
    domains = st.get("domains", [])
    if domains:
        for d in domains:
            out += ["[[studio.domains]]", f'name = {_toml_str(d["name"])}', f'path = {_toml_str(d.get("path", d["name"]))}', ""]
    for layer in st.get("layers", []):
        # обратно совместимо: paths всегда массивом; одиночный path мигрирует в paths при чтении
        paths = layer.get("paths", [layer.get("path", layer.get("name", ""))])
        out += ["[[studio.layers]]", f'name = {_toml_str(layer["name"])}',
                f'paths = {_toml_arr([p for p in paths if p])}', f'order = {int(layer.get("order", 0))}', ""]
    for layer_name, rules in st.get("naming", {}).items():
        out += [f"[studio.naming.{layer_name}]", f'abstraction = {_toml_str(rules.get("abstraction", ""))}', f'implementation = {_toml_str(rules.get("implementation", ""))}', ""]
    for layer_name, kinds in st.get("markers", {}).items():
        if kinds:
            out += [f"[studio.markers.{layer_name}]"] + [f'{k} = {_toml_str(str(v))}' for k, v in kinds.items()] + [""]
    out += ["[studio.rules]", "allowed = ["]
    for a, b in st.get("rules", {}).get("allowed", []):
        out += [f"  [{_toml_str(a)}, {_toml_str(b)}],"]
    out += ["]", ""]
    save = st.get("save", {})
    out += ["[studio.save]", f'show_diff = {"true" if save.get("show_diff", False) else "false"}', f'diff_mode = {_toml_str(save.get("diff_mode", "on_danger"))}', ""]
    return "\n".join(out)


def loads(text: str) -> dict:
    raw = tomllib.loads(text or "")
    return _normalize(raw)


def load(path: Path) -> dict:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return _normalize(raw)


def _normalize(raw: dict) -> dict:
    st = raw.get("studio", raw) if isinstance(raw, dict) else {}
    cfg = default_config()["studio"]
    if not isinstance(st, dict):
        return {"studio": cfg}
    if isinstance(st.get("project_root"), str):
        cfg["project_root"] = st["project_root"]
    if isinstance(st.get("default_domain"), str) and st["default_domain"]:
        cfg["default_domain"] = st["default_domain"]
    if isinstance(st.get("domains"), list):
        cfg["domains"] = [{"name": str(d.get("name")), "path": str(d.get("path", d.get("name")))} for d in st["domains"] if isinstance(d, dict) and d.get("name")]
    if isinstance(st.get("layers"), list) and st["layers"]:
        norm_layers = []
        for i, layer in enumerate(st["layers"]):
            if not isinstance(layer, dict) or not layer.get("name"):
                continue
            # миграция старого формата: одиночный path -> paths=[path]
            if isinstance(layer.get("paths"), list):
                paths = [str(p) for p in layer["paths"] if p]
            elif layer.get("path"):
                paths = [str(layer["path"])]
            else:
                paths = [str(layer["name"])]
            norm_layers.append({"name": str(layer["name"]), "paths": paths, "order": int(layer.get("order", i))})
        if norm_layers:
            cfg["layers"] = norm_layers
    if isinstance(st.get("naming"), dict):
        for k, v in st["naming"].items():
            if isinstance(v, dict):
                cfg["naming"][k] = {"abstraction": str(v.get("abstraction", "")), "implementation": str(v.get("implementation", ""))}
    if isinstance(st.get("markers"), dict):
        for k, v in st["markers"].items():
            if isinstance(v, dict):
                cfg["markers"][k] = {kind: str(v[kind]) for kind in MARKER_KINDS if v.get(kind)}
    if isinstance(st.get("rules"), dict) and isinstance(st["rules"].get("allowed"), list):
        cfg["rules"]["allowed"] = [[str(a), str(b)] for p in st["rules"]["allowed"] if isinstance(p, (list, tuple)) and len(p) == 2 for a, b in [p]]
    if isinstance(st.get("save"), dict):
        if isinstance(st["save"].get("show_diff"), bool):
            cfg["save"]["show_diff"] = st["save"]["show_diff"]
        if st["save"].get("diff_mode") in ("always", "on_danger", "manual"):
            cfg["save"]["diff_mode"] = st["save"]["diff_mode"]
    return {"studio": cfg}


def matches(pattern: str, name: str) -> bool:
    if not pattern:
        return True
    try:
        return re.search(pattern, name) is not None
    except re.error:
        return True
