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
from pathlib import Path

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


SERVER_FILE = Path(".studio") / "server.json"


def find_server_file(start: str | Path | None = None) -> Path | None:
    """Файл подключения студии: от STUDIO_PROJECT или текущей папки вверх по дереву."""
    bases = [os.environ.get("STUDIO_PROJECT") or "", str(start or ""), str(Path.cwd())]
    for base in bases:
        if not base:
            continue
        folder = Path(base).resolve()
        for candidate in (folder, *folder.parents):
            target = candidate / SERVER_FILE
            if target.is_file():
                return target
    return None


def _read_server_file(path: Path) -> tuple[str, dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StudioError("studio_not_running", f"Файл {path} не читается: {exc}") from exc
    tokens = data.get("tokens") if isinstance(data, dict) else None
    url = data.get("url") if isinstance(data, dict) else None
    if not isinstance(url, str) or not isinstance(tokens, dict) or not tokens:
        raise StudioError("studio_not_running", f"Файл {path} повреждён: запустите studio заново")
    return url, {str(k): str(v) for k, v in tokens.items() if v}


class StudioClient:
    """Клиент студии. Токены режимов берутся из ``.studio/server.json`` (автообнаружение)
    или из строки подключения; ``mode`` выбирает, каким из них подписан запрос."""

    def __init__(
        self,
        url: str,
        token: str = "",
        timeout: float = 30.0,
        tokens: dict[str, str] | None = None,
        server_file: Path | None = None,
    ):
        self.url = check_url(url)
        self.token = token.strip()
        self.tokens = dict(tokens or {})
        self.server_file = server_file
        self.mode = "plan"
        if not self.token and not self.tokens:
            raise StudioError("bad_token", "Нужен токен: запустите studio в проекте")
        self.timeout = timeout

    @classmethod
    def discover(cls, start: str | Path | None = None, timeout: float = 30.0) -> StudioClient:
        """Подключиться к студии проекта без ручной строки (``.studio/server.json``)."""
        path = find_server_file(start)
        if path is None:
            raise StudioError(
                "studio_not_running",
                "Файл .studio/server.json не найден: запустите studio в проекте "
                "(или задайте STUDIO_PROJECT — путь к проекту).",
            )
        url, tokens = _read_server_file(path)
        return cls(url, tokens=tokens, timeout=timeout, server_file=path)

    def _refresh(self) -> bool:
        """Студию перезапустили: перечитать адрес и токены. True — данные изменились."""
        if self.server_file is None:
            return False
        try:
            url, tokens = _read_server_file(self.server_file)
            url = check_url(url)
        except StudioError:
            return False
        changed = (url, tokens) != (self.url, self.tokens)
        self.url, self.tokens = url, tokens
        return changed

    def _token_for(self, mode: str) -> str:
        if self.tokens:
            return self.tokens.get(mode) or next(iter(self.tokens.values()))
        return self.token

    def request(self, method: str, path: str, body: dict | None = None, mode: str | None = None):
        try:
            return self._send(method, path, body, mode)
        except StudioError as exc:
            if exc.code in ("bad_token", "unreachable") and self._refresh():
                return self._send(method, path, body, mode)
            raise

    def _send(self, method: str, path: str, body: dict | None, mode: str | None):
        data = None
        headers = {"X-Studio-Token": self._token_for(mode or self.mode)}
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

    def get(self, path: str, mode: str | None = None):
        return self.request("GET", path, mode=mode)

    def post(self, path: str, body: dict | None = None, mode: str | None = None):
        return self.request("POST", path, body if body is not None else {}, mode)


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
            "Токен неверный или студию перезапустили. Вызовите studio_connect без параметров "
            "(автоподключение) или попросите у человека новую строку из окна «AI-агент»."
        )
    return StudioError(code, message, exc.code, details)


__all__ = [
    "LOOPBACK",
    "StudioClient",
    "StudioError",
    "check_url",
    "find_server_file",
    "parse_connection",
]
