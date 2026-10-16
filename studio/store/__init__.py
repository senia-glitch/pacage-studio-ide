"""Чтение/запись `.studio/`: graph.json и studio.log. Больше ничего.

Граф — артефакт сканирования: его можно удалить, studio пересоберёт его сам.
Версия схемы 1 (прототип) не читается — возвращается None, файл перезапишется.
"""

from __future__ import annotations

import json
from pathlib import Path

from studio.metamodel import Graph

STUDIO_DIR = ".studio"
GRAPH_NAME = "graph.json"

GRAPH_VERSION = 2


def studio_dir(project_root: Path) -> Path:
    return Path(project_root) / STUDIO_DIR


def graph_path(project_root: Path) -> Path:
    return studio_dir(project_root) / GRAPH_NAME


def ensure_layout(project_root: Path) -> Path:
    """Создать .studio/ с .gitignore внутри."""
    sd = studio_dir(project_root)
    sd.mkdir(parents=True, exist_ok=True)
    gitignore = sd / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("graph.json\nstudio.log\n", encoding="utf-8")
    return sd


def read_graph(project_root: Path) -> Graph | None:
    """Прочитать graph.json. Нет файла, версия != 2 или он битый — None."""
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
    except Exception:
        return None


def write_graph(project_root: Path, graph: Graph) -> Path:
    ensure_layout(project_root)
    path = graph_path(project_root)
    path.write_text(
        json.dumps(graph.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
