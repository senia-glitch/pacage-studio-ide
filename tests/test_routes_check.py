"""Перекрытие маршрутов: статичный путь позади параметрического недостижим."""

from __future__ import annotations

from pathlib import Path

import pytest

from studio.scanner import scan
from studio.scanner.routes_check import shadows


@pytest.mark.parametrize(
    ("early", "late", "expected"),
    [
        ("/api/leads/{id}", "/api/leads/export", True),
        ("/api/leads/{lead_id}", "/api/leads/{id}", True),  # то же множество запросов под другим именем
        ("/api/leads/{id}", "/api/leads/{id}", False),
        ("/api/leads/export", "/api/leads/{id}", False),  # статичный раньше — всё работает
        ("/api/leads/{id}", "/api/plans/export", False),
        ("/api/leads/{id}", "/api/leads/export/csv", False),  # разная длина
        ("/api/leads/{id:int}", "/api/leads/export", False),  # int-конвертер слово не примет
        ("/api/leads/{id:int}", "/api/leads/42", True),
        ("/api/{kind}/{id}", "/api/leads/export", True),
        ("/", "/x", False),
        ("", "", False),
    ],
)
def test_перекрытие_пар_путей(early: str, late: str, expected: bool) -> None:
    assert shadows(early, late) is expected


def _route(demo: Path, name: str, method: str, path: str) -> None:
    (demo / f"app/api/v1/routes/{name}.py").write_text(
        "from fastapi import APIRouter\nfrom core import run\n\nrouter = APIRouter()\n\n"
        f'@router.{method}("{path}")\nasync def {name}_handler():\n'
        f'    return await run("{name}")\n',
        encoding="utf-8",
    )


def _shadow_messages(demo: Path) -> list[str]:
    return [d.message for d in scan(demo).diagnostics if d.code == "W_ROUTE_SHADOW"]


def test_алфавит_файлов_решает_какой_маршрут_достижим(demo: Path) -> None:
    _route(demo, "aaa_item", "get", "/zone/{item_id}")
    _route(demo, "zzz_export", "get", "/zone/export")  # файл позже по алфавиту → перекрыт
    messages = _shadow_messages(demo)
    assert len(messages) == 1
    assert "GET /api/v1/zone/export недостижим" in messages[0] and "/zone/{item_id}" in messages[0]
    (demo / "app/api/v1/routes/zzz_export.py").unlink()
    _route(demo, "aaa_export", "get", "/zone/export")  # этот идёт раньше — работает
    (demo / "app/api/v1/routes/aaa_item.py").rename(demo / "app/api/v1/routes/mmm_item.py")
    assert _shadow_messages(demo) == []


def test_другой_метод_не_перекрывается(demo: Path) -> None:
    _route(demo, "aaa_item", "get", "/zone/{item_id}")
    _route(demo, "zzz_export", "post", "/zone/export")
    assert _shadow_messages(demo) == []


def _news_with_include(demo: Path, include_before_param: bool) -> None:
    """Как в реальном проекте: поиск лежит в другом файле под именем search_router."""
    (demo / "app/api/v1/routes/search_news.py").write_text(
        "from fastapi import APIRouter\n\nsearch_router = APIRouter()\n\n"
        '@search_router.get("/news/search")\nasync def search_news():\n    return {}\n',
        encoding="utf-8",
    )
    include = "router.include_router(search_router)\n\n"
    param = '@router.get("/news/{slug}")\nasync def get_news(slug: str):\n    return {}\n'
    body = (include + param) if include_before_param else (param + "\n" + include)
    (demo / "app/api/v1/routes/news.py").write_text(
        "from fastapi import APIRouter\nfrom app.api.v1.routes.search_news import search_router\n\n"
        "router = APIRouter()\n\n"
        '@router.get("/news")\nasync def list_news():\n    return {}\n\n' + body,
        encoding="utf-8",
    )


def test_include_router_до_параметрического_маршрута_перекрытия_нет(demo: Path) -> None:
    _news_with_include(demo, include_before_param=True)
    assert _shadow_messages(demo) == []  # файл search_news.py позже по алфавиту, но подключён раньше


def test_include_router_после_параметрического_маршрута_перекрыт(demo: Path) -> None:
    _news_with_include(demo, include_before_param=False)
    messages = _shadow_messages(demo)
    assert len(messages) == 1 and "/news/search недостижим" in messages[0]
