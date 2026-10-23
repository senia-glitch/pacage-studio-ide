"""Мост «разработчик → AI-агент»: ТЗ по карте и проверка выполнения (S12)."""

from studio.agent.brief import (
    BRIEF_FILENAME,
    BRIEF_VERSION,
    MAP_MODES,
    MAX_BRIEF_CHARS,
    Brief,
    BriefError,
    BriefNotFound,
    BriefOptions,
    build_brief,
    load_brief,
    redact_sample,
    save_brief,
    save_brief_sidecars,
)
from studio.agent.verify import render_report, verify_brief, verify_task

__all__ = [
    "BRIEF_FILENAME",
    "BRIEF_VERSION",
    "MAX_BRIEF_CHARS",
    "MAP_MODES",
    "Brief",
    "BriefError",
    "BriefNotFound",
    "BriefOptions",
    "build_brief",
    "load_brief",
    "redact_sample",
    "render_report",
    "save_brief",
    "save_brief_sidecars",
    "verify_brief",
    "verify_task",
]
