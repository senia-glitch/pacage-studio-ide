"""Уборка хвостов старой модели «заглушка + узел кода» в ``.studio/metadata.json``.

Раньше планируемые узлы жили заглушками рядом с реальным кодом; теперь план сам привязывается к
коду. Но в уже существующих проектах остаются записи заглушек:

* записи слоёв реализованных планов (создавались вместе с планом, а код получил другие имена);
* заглушки ``studio:<uuid>``, для которых в коде уже есть узел с тем же видом и якорем.

Они ничего не дают, но множат узлы и предупреждения. ``plan_cleanup`` находит такие записи,
``migrate`` убирает их (с копией файла и только по явному запросу).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from studio.metadata.apply import _plan_slug_for_record
from studio.metadata.store import (
    MetadataError,
    metadata_hash,
    metadata_path,
    read_metadata,
    write_metadata,
)
from studio.metamodel import Graph, node_anchor

BACKUP_SUFFIX = ".pre-migrate.bak"


def find_leftovers(root: Path, graph: Graph) -> dict[str, str]:
    """``{id записи: причина}`` для записей, которые можно убрать без потери смысла."""
    metadata = read_metadata(root)
    by_id = graph.by_id()
    done = {state.slug for state in graph.plans if state.status == "done"}
    code_anchors = {
        (node.kind, node_anchor(node))
        for node in graph.nodes
        if not node.planned and node_anchor(node)
    }
    found: dict[str, str] = {}
    for node_id, record in metadata.nodes.items():
        if not record.get("planned"):
            continue
        node = by_id.get(node_id)
        if node is None:
            slug = _plan_slug_for_record(metadata, record)
            if slug and slug in done:
                found[node_id] = f"заглушка слоя реализованного плана {slug}"
        elif (
            node.planned
            and node_id.startswith("studio:")
            and (node.kind, node_anchor(node)) in code_anchors
        ):
            found[node_id] = "дубль: такой узел уже есть в коде"
    return found


def migrate(root: Path, graph: Graph, apply: bool = False) -> dict:
    """Убрать найденные записи (``apply=True``) или только показать, что будет убрано."""
    leftovers = find_leftovers(root, graph)
    metadata = read_metadata(root)
    edges_before = len(metadata.edges)
    kept = [
        edge for edge in metadata.edges
        if edge.get("from") not in leftovers and edge.get("to") not in leftovers
    ]
    result = {
        "removed": [{"id": key, "reason": reason} for key, reason in sorted(leftovers.items())],
        "edges_removed": edges_before - len(kept),
        "applied": False,
        "backup": "",
    }
    if not apply or not leftovers:
        return result
    path = metadata_path(root)
    expected = metadata_hash(root)
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    try:
        shutil.copyfile(path, backup)
    except OSError as exc:
        raise MetadataError(
            "not_writable", f"Не удалось сделать копию metadata.json: {exc}"
        ) from exc
    for key in leftovers:
        metadata.nodes.pop(key, None)
    metadata.edges = kept
    if not write_metadata(root, metadata, expected):
        raise MetadataError("not_writable", "metadata.json изменили во время миграции: повторите")
    result.update(applied=True, backup=backup.name)
    return result


__all__ = ["BACKUP_SUFFIX", "find_leftovers", "migrate"]
