"""Чтение окружения проекта: `.env.fastbase`, пути к слоям, models.py.

Сканеру нужно только знать, где лежат роуты, сценарии и модели.
Env-файлов может не быть — тогда работают дефолты. Никаких исключений наверх.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from studio import log as slog

DEFAULT_ROUTES_PACKAGE = "app.api.v1.routes"
DEFAULT_CORE_DISCOVER = "app.scenarios"
DEFAULT_API_PREFIX = "/api/v1"

ENV_FILENAME = ".env.fastbase"


@dataclass(frozen=True)
class ProjectEnv:
    project_root: Path
    routes_package: str
    core_discover: str
    api_prefix: str
    models_path: Path
    has_models: bool


def _dotted_package(value: str | None, fallback: str, key: str) -> str:
    """Dotted-путь пакета: только идентификаторы, иначе дефолт.

    Значение с `..`, слэшами или абсолютным путём увело бы сканер за корень
    проекта (ValueError в relative_to) или уронило бы scan.
    """
    raw = (value or "").strip()
    if not raw:
        return fallback
    parts = raw.split(".")
    if all(part.isidentifier() for part in parts):
        return raw
    slog.step(f"{key}={raw!r} некорректен (нужен dotted-путь из идентификаторов) — использую {fallback!r}")
    return fallback


def parse_env_text(text: str) -> dict[str, str]:
    """Плоский KEY=VALUE: `#`-строки пропускаются, кавычки снимаются."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def read_env_file(path: Path) -> dict[str, str]:
    """Прочитать env-файл. Отсутствует или не читается — пустой словарь."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return parse_env_text(text)


def read_env(project_root: Path) -> ProjectEnv:
    root = Path(project_root).resolve()
    raw = read_env_file(root / ENV_FILENAME)
    routes = _dotted_package(raw.get("FAT_ROUTES_PACKAGE"), DEFAULT_ROUTES_PACKAGE, "FAT_ROUTES_PACKAGE")
    core = _dotted_package(raw.get("FAT_CORE_DISCOVER"), DEFAULT_CORE_DISCOVER, "FAT_CORE_DISCOVER")
    prefix = raw.get("FAT_API_PREFIX") or DEFAULT_API_PREFIX
    models_path = root / "models.py"
    return ProjectEnv(
        project_root=root,
        routes_package=routes.strip(),
        core_discover=core.strip(),
        api_prefix=prefix.strip(),
        models_path=models_path,
        has_models=models_path.exists(),
    )


def dotted_to_path(root: Path, dotted: str) -> Path:
    """`app.api.v1.routes` → `<root>/app/api/v1/routes` (без проверки существования)."""
    parts = [p for p in dotted.strip().split(".") if p and p != "."]
    return Path(root).joinpath(*parts) if parts else Path(root)


def module_name(root: Path, path: Path) -> str:
    """Относительный путь файла → dotted-имя модуля (`app/scenarios/x.py` → `app.scenarios.x`)."""
    try:
        rel = Path(path).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return Path(path).stem
    parts = list(rel.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)
