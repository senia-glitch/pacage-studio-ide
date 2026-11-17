"""Ядро графа идеи: карточки, связи, операции, журнал. Без интерфейса и без моделей."""

from . import views
from .bank import Bank, normalize_url
from .errors import ConflictError, GraphError
from .model import (
    ACCEPTED,
    DETAIL,
    HUMAN,
    PROPOSED,
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
    "STATUSES",
    "STATUS_NAMES",
    "Bank",
    "ConflictError",
    "GraphError",
    "Store",
    "normalize_url",
    "author_name",
    "is_human",
    "views",
]
