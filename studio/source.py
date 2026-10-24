"""Единственное место чтения файлов проекта.

`load_source` никогда не бросает исключение: ошибка кодировки, синтаксиса или
доступа превращается в диагностику, а остальной скан продолжается.
"""

from __future__ import annotations

import ast
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from studio._constants import EXCLUDED_DIRS, MAX_SOURCE_BYTES
from studio.diagnostics import Diagnostic, Sink
from studio.env import ENV_FILENAME, ProjectEnv, dotted_to_path

BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class SourceFile:
    rel: str
    path: Path
    text: str
    lines: list[str] = field(default_factory=list)
    tree: ast.Module | None = None
    sha1: str = ""
    raw: bytes = b""
    error: Diagnostic | None = None


@dataclass
class SourceSet:
    root: Path
    files: list[SourceFile] = field(default_factory=list)
    readonly: list[SourceFile] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_rel = {item.rel: item for item in [*self.files, *self.readonly]}

    def by_rel(self, rel: str) -> SourceFile | None:
        return self._by_rel.get(str(rel).replace("\\", "/"))

    def all_files(self) -> list[SourceFile]:
        return [*self.files, *self.readonly]

    def fingerprint(self) -> str:
        digest = hashlib.sha1()
        for item in sorted(self.all_files(), key=lambda f: f.rel):
            digest.update(f"{item.rel}:{item.sha1}\n".encode())
        return digest.hexdigest()


def load_source(root: Path, path: Path) -> SourceFile:
    """Прочитать и разобрать один файл. Никогда не бросает."""
    root = Path(root)
    path = Path(path)
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        rel = path.name
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return SourceFile(
            rel=rel,
            path=path,
            text="",
            error=Diagnostic(
                level="error",
                code="E_INTERNAL",
                message=f"Файл не читается: {exc}",
                file=rel,
                hint="Проверьте права доступа и что файл не занят другой программой.",
            ),
        )

    original = raw
    sha1 = hashlib.sha1(original).hexdigest()
    if raw.startswith(BOM):
        raw = raw[len(BOM) :]

    if len(raw) > MAX_SOURCE_BYTES:
        return SourceFile(
            rel=rel,
            path=path,
            text="",
            sha1=sha1,
            raw=original,
            error=Diagnostic(
                level="warning",
                code="W_FILE_TOO_BIG",
                message=f"Файл больше {MAX_SOURCE_BYTES // (1024 * 1024)} МБ и пропущен",
                file=rel,
                hint="Уменьшите файл или исключите его из источников проекта.",
            ),
        )

    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return SourceFile(
            rel=rel,
            path=path,
            text="",
            sha1=sha1,
            raw=original,
            error=Diagnostic(
                level="error",
                code="E_ENCODING",
                message=f"Файл не в UTF-8 (байт {exc.start})",
                file=rel,
                line=exc.start,
                hint="Пересохраните файл в UTF-8; запись метаданных в него запрещена.",
            ),
        )

    text = decoded.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()

    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        line = int(exc.lineno or 0)
        return SourceFile(
            rel=rel,
            path=path,
            text=text,
            lines=lines,
            sha1=sha1,
            raw=original,
            error=Diagnostic(
                level="error",
                code="E_SYNTAX",
                message=f"Синтаксическая ошибка: {exc.msg}",
                file=rel,
                line=line,
                hint="Исправьте код — до этого файл не участвует в карте и не пишется.",
            ),
        )
    except (ValueError, RecursionError, MemoryError) as exc:
        return SourceFile(
            rel=rel,
            path=path,
            text=text,
            lines=lines,
            sha1=sha1,
            raw=original,
            error=Diagnostic(
                level="error",
                code="E_SYNTAX",
                message=f"Файл не разбирается: {type(exc).__name__}",
                file=rel,
                hint="Упростите конструкцию — сканер работает только по AST.",
            ),
        )

    return SourceFile(
        rel=rel,
        path=path,
        text=text,
        lines=lines,
        tree=tree,
        sha1=sha1,
        raw=original,
    )


def _is_inside(root: Path, base: Path) -> bool:
    try:
        root_resolved = root.resolve()
        base_resolved = base.resolve()
    except OSError:
        return False
    return base_resolved == root_resolved or root_resolved in base_resolved.parents


def module_files(root: Path, dotted: str) -> list[Path]:
    """Dotted-путь → .py-файлы пакета/модуля без захода в служебные каталоги."""
    root = Path(root)
    base = dotted_to_path(root, dotted)
    if not _is_inside(root, base):
        return []
    if base.is_dir():
        out: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in EXCLUDED_DIRS and not name.startswith(".")
            )
            for name in sorted(filenames):
                if not name.endswith(".py"):
                    continue
                if name.startswith("_") and name != "__init__.py":
                    continue
                out.append(Path(dirpath) / name)
        return out
    single = base.with_suffix(".py")
    return [single] if single.is_file() else []


def package_init_files(root: Path, dotted: str) -> list[Path]:
    """Все `__init__.py` пакета и его родителей внутри проекта."""
    root = Path(root)
    base = dotted_to_path(root, dotted)
    try:
        base_rel = base.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return []
    out: list[Path] = []
    current = root
    for part in base_rel.parts:
        current = current / part
        init = current / "__init__.py"
        if init.is_file():
            out.append(init)
    init = base / "__init__.py" if base.is_dir() else None
    if init is not None and init.is_file() and init not in out:
        out.append(init)
    return out


def models_files(env: ProjectEnv) -> list[Path]:
    return [path for path in env.models_paths if path.is_file()]


def tests_files(root: Path) -> list[Path]:
    tests_dir = Path(root) / "tests"
    if not tests_dir.is_dir():
        return []
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(tests_dir, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames if name not in EXCLUDED_DIRS and not name.startswith(".")
        )
        for name in sorted(filenames):
            if not name.endswith(".py") or name.startswith("_"):
                continue
            if not (name.startswith("test_") or name.endswith("_test.py")):
                continue
            out.append(Path(dirpath) / name)
    return out


def iter_source_paths(env: ProjectEnv) -> list[Path]:
    """Все файлы-источники (для чтения и для stat-отпечатка watcher'а)."""
    root = Path(env.project_root)
    paths: list[Path] = []
    paths.extend(module_files(root, env.routes_package))
    paths.extend(module_files(root, env.core_discover))
    paths.extend(models_files(env))
    paths.extend(tests_files(root))
    for rel in ("main.py", "app/main.py"):
        candidate = root / rel
        if candidate.is_file():
            paths.append(candidate)
    paths.extend(package_init_files(root, env.routes_package))
    env_file = root / ENV_FILENAME
    if env_file.is_file():
        paths.append(env_file)
    unique: dict[str, Path] = {}
    for path in paths:
        try:
            key = path.resolve().as_posix()
        except OSError:
            key = path.as_posix()
        unique[key] = path
    return sorted(unique.values(), key=lambda p: p.as_posix())


def stat_fingerprint(env: ProjectEnv) -> tuple[tuple[str, int, int], ...]:
    """Отпечаток по stat: (относительный путь, mtime_ns, размер)."""
    root = Path(env.project_root).resolve()
    out: list[tuple[str, int, int]] = []
    for path in iter_source_paths(env):
        try:
            st = path.stat()
            rel = path.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        out.append((rel, int(st.st_mtime_ns), int(st.st_size)))
    return tuple(sorted(out))


def load_sources(env: ProjectEnv, sink: Sink | None = None) -> SourceSet:
    root = Path(env.project_root).resolve()
    normal_paths = [
        *module_files(root, env.routes_package),
        *module_files(root, env.core_discover),
        *models_files(env),
        *tests_files(root),
    ]
    readonly_paths = [
        *(root / rel for rel in ("main.py", "app/main.py") if (root / rel).is_file()),
        *package_init_files(root, env.routes_package),
    ]

    files: dict[str, SourceFile] = {}
    readonly: dict[str, SourceFile] = {}
    for path in normal_paths:
        try:
            key = path.resolve().as_posix()
        except OSError:
            key = path.as_posix()
        if key in files:
            continue
        source = load_source(root, path)
        files[key] = source
        if source.error is not None and sink is not None:
            sink.extend([source.error])
    normal_keys = set(files)
    for path in readonly_paths:
        try:
            key = path.resolve().as_posix()
        except OSError:
            key = path.as_posix()
        if key in normal_keys or key in readonly:
            continue
        source = load_source(root, path)
        readonly[key] = source
        if source.error is not None and sink is not None:
            sink.extend([source.error])

    ordered = sorted(files.values(), key=lambda item: item.rel)
    ordered_readonly = sorted(readonly.values(), key=lambda item: item.rel)
    return SourceSet(root=root, files=ordered, readonly=ordered_readonly)


__all__ = [
    "SourceFile",
    "SourceSet",
    "iter_source_paths",
    "load_source",
    "load_sources",
    "models_files",
    "module_files",
    "package_init_files",
    "stat_fingerprint",
    "tests_files",
]

