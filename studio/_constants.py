"""Константы пакета: единственный источник для версии зависимостей и лимитов."""

from __future__ import annotations

FASTBASE_FULL_SPEC = (
    "fastbase[full] @ git+https://github.com/senia-glitch/fastapi-transport.git"
)

# Атрибут сценария, через который сценарий ходит в БД: `self._db.create(...)`.
PORT_ATTR = "_db"

# Файлы больше этого размера не разбираются (W_FILE_TOO_BIG).
MAX_SOURCE_BYTES = 2 * 1024 * 1024

# Каталоги, которые сканер не обходит.
EXCLUDED_DIRS = frozenset(
    {
        "tests",
        "alembic",
        "docs",
        ".venv",
        "venv",
        "__pycache__",
        ".git",
        ".studio",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        "node_modules",
    }
)

# Глубина анализа помощников сценария и эндпоинта.
MAX_HELPER_DEPTH = 3
MAX_ROUTE_INCLUDE_DEPTH = 5

# Лимит тела HTTP-запроса.
MAX_HTTP_BODY = 16 * 1024 * 1024
