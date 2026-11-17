"""Ядро банка знаний: карточки, связи, операции, журнал. Без интерфейса и без моделей."""

from .errors import ConflictError, GraphError
from .model import (
    ACCEPTED,
    DETAIL,
    HUMAN,
    PROPOSED,
    REJECTED,
    STALE,
    STATUS_NAMES,
    STATUSES,
    author_name,
    is_human,
)
from .store import DIR_NAME, Store

__version__ = "0.1.0"

__all__ = [
    "ACCEPTED",
    "DETAIL",
    "DIR_NAME",
    "HUMAN",
    "PROPOSED",
    "REJECTED",
    "STALE",
    "STATUSES",
    "STATUS_NAMES",
    "ConflictError",
    "GraphError",
    "Store",
    "author_name",
    "is_human",
]
