"""Движок «Графа идеи»: проект (граф и банк знаний), действия человека, работа агентов.

Цепочка линейна: сфера → конкуренты → анализ → роли → сценарии → как есть → решение.
Интерфейс — в ``idea-graph-shell``.
"""

from .agents import Adapters
from .config import Settings
from .errors import EngineError
from .project import Project, Run

__version__ = "0.3.0"

__all__ = ["Adapters", "EngineError", "Project", "Run", "Settings"]
