"""Безопасность локального сервера: Host/Origin/token/Content-Type/static."""

from __future__ import annotations

import hmac
import re
from pathlib import Path

FROM_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
STATIC_EXTENSIONS = {".html", ".css", ".js", ".svg", ".ico"}
MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)
TOKEN_META_RE = re.compile(r'<meta\s+name="studio-token"\s+content="[^"]*"\s*>')


def parse_host(host_header: str) -> tuple[str, str]:
    value = str(host_header or "").strip().lower()
    if not value:
        return "", ""
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return value, ""
        hostname = value[1:end]
        rest = value[end + 1 :]
        port = rest[1:] if rest.startswith(":") else ""
        return hostname, port
    if ":" in value:
        hostname, _, port = value.rpartition(":")
        return hostname, port
    return value, ""


def host_allowed(host_header: str, bound_port: int, configured_host: str = "") -> bool:
    hostname, port = parse_host(host_header)
    if not hostname:
        return False
    if port and port != str(bound_port):
        return False
    allowed = set(FROM_LOOPBACK)
    if configured_host:
        allowed.add(configured_host.strip().lower())
    return hostname in allowed


def origin_allowed(origin: str | None, host_header: str) -> bool:
    if not origin:
        return True
    expected = f"http://{str(host_header or '').strip().lower()}"
    return origin.strip().lower() == expected


def token_ok(expected: str, provided: str | None) -> bool:
    if not expected or not provided:
        return False
    return hmac.compare_digest(str(expected), str(provided))


def static_path(root: Path, url_path: str) -> Path | None:
    """Безопасный путь статики: без .., абсолютных путей и лишних расширений."""
    relative = url_path
    if relative.startswith("/static/"):
        relative = relative[len("/static/") :]
    elif relative.startswith("static/"):
        relative = relative[len("static/") :]
    relative = relative.split("?", 1)[0]
    if not relative or relative.startswith("/") or "\x00" in relative:
        return None
    parts = Path(relative.replace("\\", "/")).parts
    if any(part in ("..", ".", "") for part in parts):
        return None
    suffix = Path(relative).suffix.lower()
    if suffix not in STATIC_EXTENSIONS:
        return None
    candidate = (Path(root) / Path(*parts)).resolve()
    try:
        root_resolved = Path(root).resolve()
    except OSError:
        return None
    if root_resolved not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def inject_token(html: str, token: str) -> str:
    meta = f'<meta name="studio-token" content="{token}">'
    if TOKEN_META_RE.search(html):
        return TOKEN_META_RE.sub(meta, html, count=1)
    return html.replace("<head>", "<head>\n" + meta, 1)


def content_type(path: Path) -> str:
    return MIME_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")


__all__ = [
    "CSP",
    "SECURITY_HEADERS",
    "STATIC_EXTENSIONS",
    "content_type",
    "host_allowed",
    "inject_token",
    "origin_allowed",
    "parse_host",
    "static_path",
    "token_ok",
]
