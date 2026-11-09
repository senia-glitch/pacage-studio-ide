"""Цепочка «Продукт → Фронтенд» (прототип): роли и скиллы OpenCode, проверки артефактов.

Агенты и скиллы лежат в `assets/opencode/` и ставятся командой `studio chain install`;
механические проверки `.studio/chain/*.json` — `studio chain check`.
"""

from studio.productchain.check import (
    CHAIN_DIR,
    Report,
    exit_code,
    format_report,
    merge_research,
    report_json,
    summary,
    validate,
)
from studio.productchain.install import ASSETS, InstallResult, install

__all__ = [
    "ASSETS",
    "CHAIN_DIR",
    "InstallResult",
    "Report",
    "exit_code",
    "format_report",
    "install",
    "merge_research",
    "report_json",
    "summary",
    "validate",
]
