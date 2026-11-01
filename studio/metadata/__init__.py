"""Метаданные студии: единственный файл `.studio/metadata.json`.

Код проекта не читается и не изменяется. Здесь только чтение/запись своего
хранилища и подмешивание данных в граф.
"""

from studio.metadata.apply import apply_metadata
from studio.metadata.store import (
    METADATA_NAME,
    MetadataError,
    delete_graph_node,
    metadata_hash,
    metadata_path,
    metadata_payload,
    read_metadata,
    save_graph_node,
    save_node_metadata,
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
    "save_graph_node",
    "save_node_metadata",
    "write_metadata",
]
