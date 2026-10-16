"""Чтение/запись .studio/ : config.toml, graph.json, cache/."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from studio import config as config_mod
from studio.metamodel import Graph

STUDIO_DIR = ".studio"
CONFIG_NAME = "config.toml"
GRAPH_NAME = "graph.json"
CACHE_DIR = "cache"
HASHES_NAME = "hashes.json"


def studio_dir(project_root: Path) -> Path:
    return project_root / STUDIO_DIR


def ensure_layout(project_root: Path) -> Path:
    sd = studio_dir(project_root)
    (sd / CACHE_DIR).mkdir(parents=True, exist_ok=True)
    gitignore = sd / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("cache/\nstudio.log\n", encoding="utf-8")
    return sd


def read_config(project_root: Path) -> dict:
    p = studio_dir(project_root) / CONFIG_NAME
    if not p.exists():
        return config_mod.default_config()
    return config_mod.load(p)


def write_config(project_root: Path, cfg: dict) -> None:
    ensure_layout(project_root)
    (studio_dir(project_root) / CONFIG_NAME).write_text(config_mod.dumps(cfg), encoding="utf-8")


def read_graph(project_root: Path) -> Graph | None:
    p = studio_dir(project_root) / GRAPH_NAME
    if not p.exists():
        return None
    try:
        return Graph.model_validate(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None


def write_graph(project_root: Path, graph: Graph) -> None:
    ensure_layout(project_root)
    (studio_dir(project_root) / GRAPH_NAME).write_text(
        json.dumps(graph.model_dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_hashes(project_root: Path) -> dict[str, str]:
    p = studio_dir(project_root) / CACHE_DIR / HASHES_NAME
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_hashes(project_root: Path, hashes: dict[str, str]) -> None:
    ensure_layout(project_root)
    (studio_dir(project_root) / CACHE_DIR / HASHES_NAME).write_text(
        json.dumps(hashes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
