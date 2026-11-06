"""Связи «узел фронтенда → эндпоинт бэкенда».

Связь — это объявление на странице или блоке: поле ``calls`` со строками вида
``GET /lessons/{id}``. Сканер кода вызовы не ищет (это эвристика); студия только
хранит объявленное и сверяет его с графом бэкенда, чтобы показать, какого
эндпоинта ещё нет. Путь можно писать с префиксом API или без него.
"""

from __future__ import annotations

import re

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_CALL_RE = re.compile(r"^\s*([A-Za-z]+)\s+(/\S*)\s*$")
_PARAM_RE = re.compile(r"\$\{[^}/]*\}|\{[^}/]*\}|<[^>/]*>|:[A-Za-z_]\w*")


def normalize_call(raw) -> str | None:
    """Привести вызов к виду ``METHOD /path``; None, если это не вызов."""
    match = _CALL_RE.match(str(raw or ""))
    if match is None:
        return None
    method = match.group(1).upper()
    if method not in METHODS:
        return None
    path = match.group(2).split("?", 1)[0].split("#", 1)[0]
    return f"{method} {path}"


def normalize_calls(raw) -> list[str]:
    """Список вызовов узла: строгая проверка, дубликаты убираются, порядок сохраняется.

    Некорректная строка — ValueError с самой строкой, чтобы пользователь увидел,
    что именно не принято.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("calls должен быть списком строк вида «GET /path»")
    result: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        call = normalize_call(text)
        if call is None:
            raise ValueError(f"Вызов «{text}» не распознан: нужен вид «GET /path»")
        if call not in result:
            result.append(call)
    return result


def _path_key(path: str) -> str:
    path = _PARAM_RE.sub("{}", path)
    return path.rstrip("/") or "/"


def _keys(method: str, path: str, prefix: str) -> set[str]:
    """Ключи сопоставления: путь как есть и без/с префиксом API."""
    prefix = "/" + prefix.strip("/") if prefix.strip("/") else ""
    keys = {f"{method} {_path_key(path)}"}
    if prefix and path.startswith(prefix + "/"):
        keys.add(f"{method} {_path_key(path[len(prefix):])}")
    if prefix and not path.startswith(prefix + "/"):
        keys.add(f"{method} {_path_key(prefix + path)}")
    return keys


def endpoint_index(backend_graph, api_prefix: str = "") -> dict[str, str]:
    """Ключ вызова → id эндпоинта бэкенда (факт и плановые)."""
    index: dict[str, str] = {}
    for node in getattr(backend_graph, "nodes", None) or []:
        if node.kind != "endpoint":
            continue
        details = node.details or {}
        method = str(details.get("http_method") or "").upper()
        if method not in METHODS:
            continue
        for field in ("url_path", "path"):
            path = str(details.get(field) or "")
            if path.startswith("/"):
                for key in _keys(method, path, api_prefix):
                    index.setdefault(key, node.id)
    return index


def _iter_calls(front_graph: dict):
    for page in front_graph.get("pages") or []:
        for item in (page, *(page.get("blocks") or [])):
            for call in item.get("calls") or []:
                yield page, item, call


def check_links(front_graph: dict, backend_graph, api_prefix: str = "") -> dict:
    """Сверить объявленные вызовы с эндпоинтами бэкенда.

    Ответ: ``rows`` (узел, вызов, статус ok/missing, id эндпоинта) и счётчики.
    """
    index = endpoint_index(backend_graph, api_prefix)
    rows = []
    for page, item, call in _iter_calls(front_graph):
        method, _, path = call.partition(" ")
        endpoint = next(
            (index[key] for key in _keys(method, path, api_prefix) if key in index), ""
        )
        rows.append(
            {
                "node": item["id"],
                "name": item.get("name") or item["id"],
                "page": page.get("name") or page["id"],
                "call": call,
                "endpoint": endpoint,
                "status": "ok" if endpoint else "missing",
            }
        )
    missing = sum(1 for row in rows if row["status"] == "missing")
    return {"rows": rows, "ok": len(rows) - missing, "missing": missing}


__all__ = [
    "METHODS",
    "check_links",
    "endpoint_index",
    "normalize_call",
    "normalize_calls",
]
