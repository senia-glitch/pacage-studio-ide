"""Режимы доступа к API: полный (UI) и два режима AI-агента.

UI получает корневой токен и может всё. Агент получает производный токен
режима, который пользователь копирует из интерфейса:

- ``plan`` — планирование: читает проект, пишет идеи и плановые узлы; кода и
  описаний существующих узлов не трогает;
- ``implement`` — реализация: читает проект и сохранённое пользователем ТЗ,
  проверяет результат (verify); план и идеи не меняет.

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
        "/api/agent/guide",
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
        "/api/agent/report",
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
    }
)
# Маршруты приёма JSON-ответа: агент может только проверить (preview).
APPLY_PATHS = frozenset({"/api/sync/apply", "/api/frontend/sync/apply"})

ALLOWED: dict[str, dict[str, frozenset[str]]] = {
    PLAN: {"GET": _GET_READ, "POST": _POST_SAFE | _POST_PLAN},
    IMPLEMENT: {"GET": _GET_READ, "POST": _POST_SAFE},
}

ROUTE_DOCS: dict[tuple[str, str], str] = {
    ("GET", "/api/agent"): "Это описание: режим токена, разрешённые маршруты, порядок работы.",
    ("GET", "/api/state"): "Версия, ревизия, корень проекта, счётчики узлов.",
    ("GET", "/api/graph"): "Граф бэкенда: узлы, связи, планы, диагностика.",
    ("GET", "/api/wait"): "?rev=N — ждёт изменения проекта (до 25 с), возвращает ревизию.",
    ("GET", "/api/frontend"): "Результат скана frontend/.",
    ("GET", "/api/frontend/graph"): "Граф фронтенда: страницы, блоки, правила, нарушения.",
    ("GET", "/api/verify"): "?brief=last|id|путь — проверка выполнения ТЗ по коду.",
    ("GET", "/api/ideas"): "Доска идей {version, revision, activeId, ideas[]}.",
    ("GET", "/api/brief/last"): "ТЗ, сохранённое пользователем: {markdown, brief_id, tasks[]}.",
    ("GET", "/api/frontend/brief/last"): "Последнее ТЗ фронтенда, собранное в окне «⇄ ТЗ…»: "
    "{markdown, brief_id, tasks[]}.",
    ("GET", "/api/frontend/verify"): "?brief=last|путь — проверка выполнения ТЗ фронтенда по коду.",
    ("GET", "/api/agent/guide"): "Руководство агента (markdown): подключение, режимы, "
    "порядок работы, форматы.",
    ("POST", "/api/decompose/parse"): "{text} — проверить JSON ответа по идее "
    "(scenarios, pages): вернёт разобранное и предупреждения; ничего не записывает.",
    ("GET", "/api/agent/reports"): "Ваши отчёты и решения пользователя: [{id, part, status, "
    "counts}] (pending, accepted, rejected).",
    ("POST", "/api/agent/report"): "{part: backend|frontend, json} — отправить итоговый "
    "JSON-отчёт: студия проверит его и положит в «Отчёты агента»; применяет пользователь.",
    ("POST", "/api/rescan"): "Перечитать проект; возвращает граф.",
    ("POST", "/api/report"): "{sections?} — отчёт по бэкенду.",
    ("POST", "/api/frontend/report"): "{sections?} — отчёт по фронтенду.",
    ("POST", "/api/sync/prompt"): "{scope, domain} — промпт синхронизации описаний.",
    ("POST", "/api/frontend/sync"): "{scope, domain, pageId} — то же для фронтенда.",
    ("POST", "/api/decompose/prompt"): "{task, mode} — промпт разбора идеи на сценарии.",
    ("POST", "/api/plan/preview"): "Сухой прогон плана сценария (файлы не пишутся).",
    ("POST", "/api/sync/apply"): "{json} — проверка JSON-ответа. apply=true агенту запрещён.",
    ("POST", "/api/frontend/sync/apply"): "То же для фронтенда. apply=true агенту запрещён.",
    ("POST", "/api/ideas"): "Сохранить доску. Передайте revision, прочитанный в GET; "
    "при расхождении — 409 ideas_conflict. У идеи: scenarios (бэкенд) и pages "
    "(страницы фронтенда с blocks и calls).",
    ("POST", "/api/metadata/graph-node"): "Создать/изменить плановый узел бэкенда.",
    ("POST", "/api/metadata/graph-node/delete"): "Удалить плановый узел.",
    ("POST", "/api/frontend/meta"): "{action, …} — плановые страницы (page-plan:…), "
    "блоки (block-plan:…), правила, домены; calls — список вызываемых эндпоинтов "
    "(«GET /path»); существующие страницы и блоки агенту закрыты.",
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


def routes_for(mode: str) -> list[dict]:
    """Маршруты режима для /api/agent."""
    result = []
    allowed = ALLOWED.get(mode)
    if allowed is None:
        return result
    for method in ("GET", "POST"):
        for path in sorted(allowed[method]):
            result.append(
                {"method": method, "path": path, "about": ROUTE_DOCS.get((method, path), "")}
            )
    return result


__all__ = [
    "AGENT_MODES",
    "ALLOWED",
    "APPLY_PATHS",
    "FULL",
    "IMPLEMENT",
    "PLAN",
    "derive_token",
    "route_allowed",
    "routes_for",
    "token_mode",
]
