"""Общие помощники интеграционных тестов агента: вызов HTTP-API студии и «реализация» кода."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path


def call(srv, method: str, path: str, token: str, payload: dict | None = None):
    """Запрос к студии с токеном (имитирует человека или агента); вернуть (код, тело)."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"X-Studio-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(srv.url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        finally:
            exc.close()


def implement_lessons(root: Path) -> None:
    """Как будто агент написал код: маршрут, сценарий и тест с теми же именами, что в плане."""
    (root / "app/api/v1/routes/lessons.py").write_text(
        "from fastapi import APIRouter\nfrom core import run\n\n"
        "router = APIRouter()\n\n"
        '@router.get("/lessons")\nasync def list_lessons():\n'
        '    return await run("list_lessons")\n',
        encoding="utf-8",
    )
    (root / "app/scenarios/lessons.py").write_text(
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("list_lessons")\n'
        "class ListLessonsScenario(BaseScenario):\n"
        "    async def execute(self):\n"
        '        await self._db.create("lessons", {})\n',
        encoding="utf-8",
    )
    (root / "tests/test_lessons.py").write_text(
        'def test_list_lessons(client):\n    client.get("/api/v1/lessons")\n\n'
        'def test_ListLessonsScenario(client):\n    client.get("/api/v1/lessons")\n',
        encoding="utf-8",
    )
