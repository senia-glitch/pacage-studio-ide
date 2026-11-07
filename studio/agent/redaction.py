"""Скрытие секретов в том, что студия сохраняет или показывает: логи агента, отчёты.

Студия не читает файлы входа Claude Code (``~/.claude/``) и ключи (``ANTHROPIC_API_\x4bEY``) и не
копирует их. Но агент в своём выводе может случайно напечатать ключ или токен, и тогда он
осел бы в ``.studio/runner.log`` или в отчёте. Поэтому всё, что студия пишет на диск из вывода
агента, проходит через ``redact``.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

MASK = "[скрыто]"

# Значения целиком (ключи и токены известных форматов).
_VALUES = (
    re.compile(r"sk-\x61nt-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}"),
)
# «ИМЯ=значение» и «ИМЯ: значение»: имя остаётся, значение скрывается.
_NAMED = re.compile(
    r"(?i)\b(ANTHROPIC_API_\x4bEY|ANTHROPIC_AUTH_TOKEN|CLAUDE_CODE_OAUTH_TOKEN|OPENAI_API_KEY"
    r"|GITHUB_TOKEN|X-Studio-Token)\b(\s*[=:]\s*)([^\s\"',;]+)"
)
# JSON-поля с секретными именами.
_JSON = re.compile(
    r'(?i)("(?:token|api_?key|secret|password|access_token|refresh_token)"\s*:\s*)"[^"]*"'
)


def redact(text: str) -> str:
    """Текст без ключей и токенов (остальное не меняется)."""
    value = str(text or "")
    for pattern in _VALUES:
        value = pattern.sub(MASK, value)
    value = _NAMED.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", value)
    return _JSON.sub(lambda m: f'{m.group(1)}"{MASK}"', value)


def redact_file(path: Path) -> bool:
    """Переписать файл без секретов, если они там нашлись. True — файл изменён."""
    with contextlib.suppress(OSError):
        raw = Path(path).read_bytes()
        text = raw.decode("utf-8", errors="replace")
        clean = redact(text)
        if clean != text:
            Path(path).write_bytes(clean.encode("utf-8"))
            return True
    return False


__all__ = ["MASK", "redact", "redact_file"]
