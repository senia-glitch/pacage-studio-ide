"""Доступ к API: только человек, по токену, который студия вставляет в страницу.

Студия сама запускает агентов (CLI ``claude`` и ``opencode`` без доступа к студии), поэтому
отдельных режимов для внешнего агента нет: любой запрос к ``/api/*`` без верного токена
отклоняется.
"""

from __future__ import annotations

import hmac

FULL = "full"


def token_mode(root_token: str, provided: str | None) -> str | None:
    """Режим по токену из заголовка: ``full`` или ``None`` (нет или неверный токен)."""
    if not root_token or not provided:
        return None
    candidate = str(provided).encode("utf-8", "replace")
    if hmac.compare_digest(str(root_token).encode("utf-8"), candidate):
        return FULL
    return None


def route_allowed(mode: str, method: str, path: str) -> bool:
    """Человеку по верному токену открыты все маршруты."""
    return mode == FULL


__all__ = ["FULL", "route_allowed", "token_mode"]
