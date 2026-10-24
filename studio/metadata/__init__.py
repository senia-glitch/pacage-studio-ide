"""Метаданные студии: единственный файл `.studio/metadata.json`.

Код проекта не читается и не изменяется. Здесь только чтение/запись своего
хранилища и подмешивание данных в граф.
"""

from studio.metadata.apply import apply_metadata
from studio.metadata.store import (
    METADATA_NAME,
    METADATA_VERSION,
    Metadata,
    MetadataError,
    SaveResult,
    metadata_path,
    metadata_payload,
    read_metadata,
    save_node_metadata,
    studio_dir,
    write_metadata,
)

__all__ = [
    "METADATA_NAME",
    "METADATA_VERSION",
    "Metadata",
    "MetadataError",
    "SaveResult",
    "apply_metadata",
    "metadata_path",
    "metadata_payload",
    "read_metadata",
    "save_node_metadata",
    "studio_dir",
    "write_metadata",
]
