"""Мост «разработчик → AI-агент»: ТЗ по карте и проверка выполнения (S12)."""

from studio.agent.brief import (
    BriefError,
    BriefNotFound,
    BriefOptions,
    build_brief,
    load_brief,
    save_brief,
    save_brief_sidecars,
)
from studio.agent.decompose import build_decompose_prompt
from studio.agent.verify import render_report, verify_brief

__all__ = [
    "BriefError",
    "BriefNotFound",
    "BriefOptions",
    "build_brief",
    "build_decompose_prompt",
    "load_brief",
    "render_report",
    "save_brief",
    "save_brief_sidecars",
    "verify_brief",
]
