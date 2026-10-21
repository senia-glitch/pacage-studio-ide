from __future__ import annotations

from studio.env import (
    DEFAULT_API_PREFIX,
    DEFAULT_CORE_DISCOVER,
    DEFAULT_ROUTES_PACKAGE,
    parse_env_text,
    read_env,
)


def test_дефолты_без_env(tmp_path):
    env = read_env(tmp_path)
    assert env.routes_package == DEFAULT_ROUTES_PACKAGE
    assert env.core_discover == DEFAULT_CORE_DISCOVER
    assert env.api_prefix == DEFAULT_API_PREFIX
    assert env.has_models is False


def test_парсинг_env():
    values = parse_env_text(
        "# комментарий\n"
        "FAT_ROUTES_PACKAGE = 'app.routes'\n"
        "FAT_CORE_DISCOVER=app.scenarios\n"
        "FAT_API_PREFIX=api/v2/\n"
    )
    assert values["FAT_ROUTES_PACKAGE"] == "app.routes"
    assert values["FAT_CORE_DISCOVER"] == "app.scenarios"


def test_api_prefix_нормализуется(tmp_path):
    (tmp_path / ".env.fastbase").write_text("FAT_API_PREFIX=api/v1/\n", encoding="utf-8")
    assert read_env(tmp_path).api_prefix == "/api/v1"


def test_кривой_dotted_путь_заменяется_дефолтом(tmp_path):
    (tmp_path / ".env.fastbase").write_text(
        "FAT_ROUTES_PACKAGE=../../etc\nFAT_CORE_DISCOVER=app..scenarios\n",
        encoding="utf-8",
    )
    env = read_env(tmp_path)
    assert env.routes_package == DEFAULT_ROUTES_PACKAGE
    assert env.core_discover == DEFAULT_CORE_DISCOVER
