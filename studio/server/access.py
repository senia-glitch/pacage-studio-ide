"""Режимы доступа к API: полный (UI) и два режима AI-агента.

UI получает корневой токен и может всё. Агент получает производный токен
режима, который пользователь копирует из интерфейса:

- ``plan`` — планирование: читает проект, пишет идеи и плановые узлы; кода и
  описаний существующих узлов не трогает;
- ``implement`` — реализация: читает проект и сохранённое пользователем ТЗ,
  проверяет результат (verify), отправляет отчёт; план и идеи не меняет.

Запись режима открыта только на его этапах дорожной карты (``server.workflow``): идеи —
пока человек ждёт их, отчёт — пока идёт реализация.

Ни один режим агента не применяет JSON-ответы: ``apply`` разрешён только UI.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

FULL = "full"
PLAN = "plan"
IMPLEMENT = "implement"
AGENT_MODES = (PLAN, IMPLEMENT)

# Чтение: доступно обоим режимам агента.
_GET_READ = frozenset(
    {
        "/api/agent",
        "/api/state",
        "/api/graph",
        "/api/wait",
        "/api/frontend",
        "/api/frontend/graph",
        "/api/verify",
        "/api/ideas",
        "/api/brief/last",
        "/api/frontend/brief/last",
        "/api/frontend/verify",
        "/api/agent/reports",
        "/api/workflow",
    }
)
# POST, которые ничего не меняют в плане и коде проекта (отчёты, промпты,
# предпросмотр). Для приёма ответов apply=true агенту запрещён отдельно.
_POST_SAFE = frozenset(
    {
        "/api/rescan",
        "/api/report",
        "/api/frontend/report",
        "/api/sync/prompt",
        "/api/frontend/sync",
        "/api/decompose/prompt",
        "/api/plan/preview",
        "/api/sync/apply",
        "/api/frontend/sync/apply",
        "/api/decompose/parse",
    }
)
# Запись плана: только режим планирования. Файлы кода эти маршруты не пишут.
_POST_PLAN = frozenset(
    {
        "/api/ideas",
        "/api/metadata/graph-node",
        "/api/metadata/graph-node/delete",
        "/api/frontend/meta",
        "/api/workflow/propose",
        "/api/workflow/ideas-ready",
    }
)
# Отчёт по реализации: только режим реализации.
_POST_IMPLEMENT = frozenset({"/api/agent/report"})
# Записи, которые режим вправе делать лишь на «своих» этапах дорожной карты.
WRITE_PATHS: dict[str, frozenset[str]] = {PLAN: _POST_PLAN, IMPLEMENT: _POST_IMPLEMENT}
# Маршруты приёма JSON-ответа: агент может только проверить (preview).
APPLY_PATHS = frozenset({"/api/sync/apply", "/api/frontend/sync/apply"})

ALLOWED: dict[str, dict[str, frozenset[str]]] = {
    PLAN: {"GET": _GET_READ, "POST": _POST_SAFE | _POST_PLAN},
    IMPLEMENT: {"GET": _GET_READ, "POST": _POST_SAFE | _POST_IMPLEMENT},
}

def derive_token(root_token: str, mode: str) -> str:
    """Токен режима: HMAC от корневого токена; без корневого его не получить."""
    digest = hmac.new(
        str(root_token).encode("utf-8"), f"studio-mode:{mode}".encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:32]


def token_mode(root_token: str, provided: str | None) -> str | None:
    """Режим по токену из заголовка: full, plan, implement или None."""
    if not root_token or not provided:
        return None
    candidate = str(provided).encode("utf-8", "replace")
    if hmac.compare_digest(str(root_token).encode("utf-8"), candidate):
        return FULL
    for mode in AGENT_MODES:
        if hmac.compare_digest(derive_token(root_token, mode).encode("ascii"), candidate):
            return mode
    return None


def route_allowed(mode: str, method: str, path: str) -> bool:
    if mode == FULL:
        return True
    return path in ALLOWED.get(mode, {}).get(method, frozenset())


__all__ = [
    "AGENT_MODES",
    "ALLOWED",
    "APPLY_PATHS",
    "FULL",
    "IMPLEMENT",
    "PLAN",
    "WRITE_PATHS",
    "derive_token",
    "route_allowed",
    "token_mode",
]
