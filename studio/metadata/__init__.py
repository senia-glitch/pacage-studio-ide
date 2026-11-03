"""Метаданные студии: единственный файл `.studio/metadata.json`.

Код проекта не читается и не изменяется. Здесь только чтение/запись своего
хранилища и подмешивание данных в граф.
"""

from studio.metadata.apply import apply_metadata
from studio.metadata.store import (
    METADATA_NAME,
    MetadataError,
    delete_domain_plan,
    delete_graph_node,
    metadata_hash,
    metadata_path,
    metadata_payload,
    read_metadata,
    restore_graph_node,
    save_domain_plan,
    save_graph_node,
    save_node_metadata,
    save_planned_change,
    write_metadata,
)

__all__ = [
    "METADATA_NAME",
    "MetadataError",
    "apply_metadata",
    "metadata_hash",
    "metadata_path",
    "metadata_payload",
    "read_metadata",
    "delete_graph_node",
    "restore_graph_node",
    "save_graph_node",
    "save_node_metadata",
    "save_planned_change",
    "save_domain_plan",
    "delete_domain_plan",
    "write_metadata",
]
