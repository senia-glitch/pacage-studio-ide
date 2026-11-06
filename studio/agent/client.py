"""HTTP-клиент студии для агента: только стандартная библиотека.

Им пользуется MCP-сервер (`studio mcp`), но он годится и для скриптов: подставляет токен,
разбирает ответы и ошибки студии. Подключаться разрешено только к локальной студии
(loopback): токен и данные проекта не должны уходить на чужой адрес.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_FIELD_RE = re.compile(r"^\s*(studio|url|token|mode)\s*[:=]\s*(\S+)\s*$", re.I | re.M)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Редиректы запрещены: заголовок с токеном не должен уйти на другой адрес."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(request: urllib.request.Request, timeout: float):
    return _OPENER.open(request, timeout=timeout)


class StudioError(Exception):
    """Ошибка обращения к студии: code — машинный код, message — текст для человека."""

    def __init__(self, code: str, message: str, status: int = 0, details=None):
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        super().__init__(f"[{code}] {message}")


def parse_connection(text: str) -> dict[str, str]:
    """Разобрать строку из окна «AI-агент» (Studio / Token / Mode); лишний текст допустим."""
    found: dict[str, str] = {}
    for key, value in _FIELD_RE.findall(text or ""):
        name = "url" if key.lower() == "studio" else key.lower()
        found.setdefault(name, value)
    return found


def check_url(url: str) -> str:
    """Нормализовать адрес студии и проверить, что он локальный."""
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise StudioError("bad_url", f"Некорректный адрес студии: {url!r} (нужен http://127.0.0.1:порт)")
    if parsed.hostname not in LOOPBACK and os.environ.get("STUDIO_ALLOW_REMOTE") != "1":
        raise StudioError(
            "bad_url",
            f"Адрес {parsed.hostname} не локальный: токен студии нельзя отправлять на чужой "
            "хост. Студия слушает только 127.0.0.1.",
        )
    return f"{parsed.scheme}://{parsed.netloc}"


class StudioClient:
    def __init__(self, url: str, token: str, timeout: float = 30.0):
        self.url = check_url(url)
        self.token = token.strip()
        if not self.token:
            raise StudioError("bad_token", "Нужен токен из окна «AI-агент» студии")
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict | None = None):
        data = None
        headers = {"X-Studio-Token": self.token}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.url + path, data=data, headers=headers, method=method
        )
        try:
            with _open(request, self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise _http_error(exc) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StudioError(
                "unreachable",
                f"Студия не отвечает по {self.url}: запущена ли она и тот ли это адрес? ({exc})",
            ) from exc

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, body: dict | None = None):
        return self.request("POST", path, body if body is not None else {})


def _http_error(exc: urllib.error.HTTPError) -> StudioError:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError, OSError):
        payload = None
    finally:
        exc.close()
    code, message, details = f"http_{exc.code}", f"HTTP {exc.code}", None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or code)
            message = str(error.get("message") or message)
            details = error.get("details")
        elif isinstance(error, str):
            code = str(payload.get("code") or code)
            message = error
    if code == "bad_token":
        message = (
            "Токен неверный или студию перезапустили. Попросите у пользователя новую строку "
            "подключения из окна «AI-агент» и вызовите studio_connect снова."
        )
    return StudioError(code, message, exc.code, details)


__all__ = ["LOOPBACK", "StudioClient", "StudioError", "check_url", "parse_connection"]
