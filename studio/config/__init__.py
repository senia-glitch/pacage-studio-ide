"""Чтение/запись .studio/config.toml + схема конфига по умолчанию."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

DEFAULT_DOMAINS = [
    {"name": "client", "path": "client"},
    {"name": "order", "path": "order"},
    {"name": "shared", "path": "shared"},
]

DEFAULT_LAYERS = [
    {"name": "domain", "path": "domain", "order": 0},
    {"name": "logic", "path": "logic", "order": 1},
    {"name": "io", "path": "io", "order": 2},
    {"name": "representation", "path": "representation", "order": 3},
    {"name": "validation", "path": "validation", "order": 4},
]

# Правила именования по слою: паттерн абстракции / паттерн реализации (regex, search).
DEFAULT_NAMING = {
    "domain": {"abstraction": r"^(Abstract.*|.*Contract|.*Base)$", "implementation": r".+"},
    "logic": {"abstraction": r"^(Abstract.*|.*Contract|.*UseCase|.*Service)$", "implementation": r".+"},
    "io": {"abstraction": r"^(Abstract.*|.*Contract)$", "implementation": r".+"},
    "representation": {"abstraction": r"^(Abstract.*|.*Contract)$", "implementation": r".+"},
    "validation": {"abstraction": r"^(Abstract.*|.*Contract|.*Validator)$", "implementation": r".+"},
}

# Разрешённые направления (from_layer → to_layer). Всё остальное — нарушение.
# Цепочки из ТЗ: representation → logic → domain, io → logic → domain, validation → logic → domain.
DEFAULT_ALLOWED = [
    ["representation", "logic"],
    ["representation", "domain"],
    ["logic", "domain"],
    ["io", "logic"],
    ["io", "domain"],
    ["validation", "logic"],
    ["validation", "domain"],
]


def default_config() -> dict:
    naming = {layer["name"]: dict(DEFAULT_NAMING.get(layer["name"], {"abstraction": r"^(Abstract.*|.*Contract)$", "implementation": r".+"})) for layer in DEFAULT_LAYERS}
    return {
        "studio": {
            "project_root": ".",
            "domains": [dict(d) for d in DEFAULT_DOMAINS],
            "layers": [dict(l) for l in DEFAULT_LAYERS],
            "naming": naming,
            "rules": {"allowed": [list(p) for p in DEFAULT_ALLOWED]},
            "save": {"show_diff": False, "diff_mode": "on_danger"},
        }
    }


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def dumps(cfg: dict) -> str:
    """Минимальный TOML-сериализатор под фиксированную схему (без внешних зависимостей)."""
    st = cfg.get("studio", {})
    out = ["[studio]", f'project_root = {_toml_str(str(st.get("project_root", ".")))}', ""]
    for d in st.get("domains", []):
        out += ["[[studio.domains]]", f'name = {_toml_str(d["name"])}', f'path = {_toml_str(d.get("path", d["name"]))}', ""]
    for layer in st.get("layers", []):
        out += ["[[studio.layers]]", f'name = {_toml_str(layer["name"])}', f'path = {_toml_str(layer.get("path", layer["name"]))}', f'order = {int(layer.get("order", 0))}', ""]
    for layer_name, rules in st.get("naming", {}).items():
        out += [f"[studio.naming.{layer_name}]", f'abstraction = {_toml_str(rules.get("abstraction", ""))}', f'implementation = {_toml_str(rules.get("implementation", ""))}', ""]
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
    if isinstance(st.get("domains"), list) and st["domains"]:
        cfg["domains"] = [{"name": str(d.get("name")), "path": str(d.get("path", d.get("name")))} for d in st["domains"] if isinstance(d, dict) and d.get("name")]
    if isinstance(st.get("layers"), list) and st["layers"]:
        cfg["layers"] = [{"name": str(layer.get("name")), "path": str(layer.get("path", layer.get("name"))), "order": int(layer.get("order", i))} for i, layer in enumerate(st["layers"]) if isinstance(layer, dict) and layer.get("name")]
    if isinstance(st.get("naming"), dict):
        for k, v in st["naming"].items():
            if isinstance(v, dict):
                cfg["naming"][k] = {"abstraction": str(v.get("abstraction", "")), "implementation": str(v.get("implementation", ""))}
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
