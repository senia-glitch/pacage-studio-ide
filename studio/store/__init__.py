"""Чтение/запись `.studio/`: graph.json и служебные файлы.

Граф — артефакт сканирования: его можно удалить, studio пересоберёт его сам.
Старая версия схемы не читается и пересоздаётся автоматически.
"""

from __future__ import annotations

import json
from pathlib import Path

from studio._atomic import atomic_write
from studio.metamodel import Graph

STUDIO_DIR = ".studio"
GRAPH_NAME = "graph.json"

GRAPH_VERSION = 6


def studio_dir(project_root: Path) -> Path:
    return Path(project_root) / STUDIO_DIR


def graph_path(project_root: Path) -> Path:
    return studio_dir(project_root) / GRAPH_NAME


def ensure_layout(project_root: Path) -> Path:
    """Создать .studio/ с .gitignore внутри."""
    sd = studio_dir(project_root)
    sd.mkdir(parents=True, exist_ok=True)
    gitignore = sd / ".gitignore"
    lines = [
        "graph.json",
        "metadata.json",
        "studio.log",
        "agent-brief.md",
        "briefs/",
        "last-brief.json",
    ]
    try:
        existing = [ln for ln in gitignore.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        existing = []
    missing = [ln for ln in lines if ln not in existing]
    if missing:
        atomic_write(gitignore, "\n".join(existing + missing) + "\n")
    return sd


def read_graph(project_root: Path) -> Graph | None:
    path = graph_path(project_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != GRAPH_VERSION:
        return None
    try:
        return Graph.model_validate(data)
    except Exception:  # noqa: BLE001 — битый artifact не должен ронять studio
        return None


def write_graph(project_root: Path, graph: Graph) -> Path:
    ensure_layout(project_root)
    path = graph_path(project_root)
    payload = json.dumps(graph.model_dump(), indent=2, ensure_ascii=False) + "\n"
    if not atomic_write(path, payload):
        raise OSError(f"не удалось записать {path}")
    return path
