"""Чтение окружения проекта: `.env.fastbase`, пути к слоям, models.py.

Сканеру нужно только знать, где лежат роуты, сценарии и модели. Env-файлов
может не быть — тогда работают дефолты. Никаких исключений наверх: проблемы
окружения становятся диагностиками.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from studio._constants import EXCLUDED_DIRS
from studio.diagnostics import Diagnostic

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
    models_paths: tuple[Path, ...] = ()
    models_spec: str = ""
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)

    @property
    def env_file(self) -> Path:
        return Path(self.project_root) / ENV_FILENAME


def _dotted_package(value: str | None, fallback: str, key: str) -> tuple[str, Diagnostic | None]:
    """Dotted-путь пакета: только идентификаторы, иначе дефолт."""
    raw = (value or "").strip()
    if not raw:
        return fallback, None
    parts = raw.split(".")
    if all(part.isidentifier() for part in parts):
        return raw, None
    return fallback, Diagnostic(
        level="warning",
        code="W_ENV_INVALID",
        message=f"{key}={raw!r} некорректен — используется {fallback!r}",
        file=ENV_FILENAME,
        hint=f"Укажите dotted-путь из идентификаторов, например {fallback}.",
    )


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
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    return parse_env_text(text)


def _normalize_prefix(value: str) -> str:
    """`api/v1/` → `/api/v1`; пустое — «без префикса»; trailing `/` обрезается."""
    value = value.strip()
    if not value or value == "/":
        return ""
    if not value.startswith("/"):
        value = "/" + value
    return value.rstrip("/")


def _has_python(base: Path) -> bool:
    if base.is_file() and base.suffix == ".py":
        return True
    if not base.is_dir():
        return False
    for _dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in EXCLUDED_DIRS]
        if any(name.endswith(".py") for name in filenames):
            return True
    return False


def _package_python_files(base: Path) -> list[Path]:
    if base.is_file() and base.suffix == ".py":
        return [base]
    if not base.is_dir():
        return []
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames if name not in EXCLUDED_DIRS and not name.startswith(".")
        )
        out.extend(
            Path(dirpath) / name
            for name in sorted(filenames)
            if name.endswith(".py") and (not name.startswith("_") or name == "__init__.py")
        )
    return out


def _inside(root: Path, path: Path) -> bool:
    try:
        root_resolved = Path(root).resolve()
        target = Path(path).resolve()
    except OSError:
        return False
    return target == root_resolved or root_resolved in target.parents


def _models_paths(root: Path, spec: str, diagnostics: list[Diagnostic]) -> list[Path]:
    paths: list[Path] = []
    if spec.strip():
        for raw in spec.split(","):
            value = raw.strip()
            if not value:
                continue
            candidates = [root / value]
            if not value.endswith(".py"):
                candidates.append(dotted_to_path(root, value))
            found: list[Path] = []
            for candidate in candidates:
                if candidate.is_file():
                    found = [candidate]
                    break
                if candidate.is_dir():
                    found = _package_python_files(candidate)
                    break
            found = [path for path in found if _inside(root, path)]
            if not found:
                diagnostics.append(
                    Diagnostic(
                        level="warning",
                        code="W_ENV_INVALID",
                        message=f"STUDIO_MODELS: путь {value!r} не найден",
                        file=ENV_FILENAME,
                        hint="Укажите файл .py или пакет с моделями через запятую.",
                    )
                )
            paths.extend(found)
        return sorted(set(paths))
    if (root / "models.py").is_file():
        paths.append(root / "models.py")
    with contextlib.suppress(OSError):
        paths.extend(sorted(root.glob("models_*.py")))
    return sorted(set(paths))


def read_env(project_root: Path) -> ProjectEnv:
    root = Path(project_root).resolve()
    raw = read_env_file(root / ENV_FILENAME)
    diagnostics: list[Diagnostic] = []

    routes, routes_diag = _dotted_package(
        raw.get("FAT_ROUTES_PACKAGE"), DEFAULT_ROUTES_PACKAGE, "FAT_ROUTES_PACKAGE"
    )
    core, core_diag = _dotted_package(
        raw.get("FAT_CORE_DISCOVER"), DEFAULT_CORE_DISCOVER, "FAT_CORE_DISCOVER"
    )
    for diag in (routes_diag, core_diag):
        if diag is not None:
            diagnostics.append(diag)

    prefix_raw = raw.get("FAT_API_PREFIX")
    prefix = _normalize_prefix(prefix_raw) if prefix_raw is not None else DEFAULT_API_PREFIX

    routes_base = dotted_to_path(root, routes)
    if not _has_python(routes_base):
        diagnostics.append(
            Diagnostic(
                level="warning",
                code="W_NO_ROUTES",
                message=f"Пакет роутов fastbase не найден: {routes}",
                file=ENV_FILENAME,
                hint="Создайте пакет или задайте FAT_ROUTES_PACKAGE в .env.fastbase.",
            )
        )

    core_base = dotted_to_path(root, core)
    if not _has_python(core_base):
        diagnostics.append(
            Diagnostic(
                level="warning",
                code="W_NO_SCENARIOS",
                message=f"Пакет сценариев core-package не найден: {core}",
                file=ENV_FILENAME,
                hint="Создайте пакет сценариев или задайте FAT_CORE_DISCOVER.",
            )
        )

    models_spec = str(raw.get("STUDIO_MODELS") or "").strip()
    models_paths = _models_paths(root, models_spec, diagnostics)
    if not models_paths:
        diagnostics.append(
            Diagnostic(
                level="warning",
                code="W_NO_MODELS",
                message="Файл моделей event-infra не найден: models.py",
                file=ENV_FILENAME,
                hint="Создайте models.py или задайте STUDIO_MODELS в .env.fastbase.",
            )
        )

    return ProjectEnv(
        project_root=root,
        routes_package=routes.strip(),
        core_discover=core.strip(),
        api_prefix=prefix,
        models_path=root / "models.py",
        has_models=bool(models_paths),
        models_paths=tuple(models_paths),
        models_spec=models_spec,
        diagnostics=tuple(diagnostics),
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
