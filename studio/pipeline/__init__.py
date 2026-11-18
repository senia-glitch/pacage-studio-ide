"""Конвейер студии v3: банк знаний, ворота, запросы на изменение, режимы и роли агентов.

Это ядро без интерфейса и без моделей: записи и связи, проверки кодом, прогон ворот, запросы на
изменение и настройки. Этапы, агенты и экраны строятся поверх него.
"""

from .bank import ProjectBank
from .changes import ChangeLog
from .engine import Pipeline
from .errors import PipelineError
from .gates import GATES, STAGE_GATES, gate, run_code_layer
from .keeper import GateKeeper
from .kinds import KINDS, SOURCE, STAGE_NAMES
from .roles import ROLES
from .settings import MODE_NAMES, MODES, PipelineSettings

from . import advice, audit, debt, entry, flow, integrate, reset, maps, stage1, stage2, stage3, stage4, stage5, waves  # isort: skip  # noqa: E402, E501

__all__ = [
    "entry",
    "GATES",
    "KINDS",
    "MODES",
    "MODE_NAMES",
    "ROLES",
    "SOURCE",
    "STAGE_GATES",
    "STAGE_NAMES",
    "ChangeLog",
    "GateKeeper",
    "PipelineError",
    "Pipeline",
    "PipelineSettings",
    "ProjectBank",
    "gate",
    "run_code_layer",
    "flow",
    "maps",
    "stage1",
    "stage2",
    "advice",
    "audit",
    "debt",
    "integrate",
    "reset",
    "stage3",
    "waves",
    "stage4",
    "stage5",
]
