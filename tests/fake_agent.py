"""Поддельный «агент» для тестов автозапуска: короткий процесс, который делает работу этапа.

Запускается студией как обычная команда (``{prompt}`` приходит аргументом), находит студию по
``.studio/server.json`` (STUDIO_PROJECT) и выполняет свой этап через тот же клиент, что и MCP.
"""

from __future__ import annotations

import sys

from studio.agent.client import StudioClient

SCENARIO = {
    "title": "Список уроков",
    "slug": "list_lessons",
    "description": "Возвращает список уроков",
    "http_method": "GET",
    "path": "/lessons",
}


def main() -> int:
    client = StudioClient.discover()
    flow = client.get("/api/workflow")
    if flow["hint"]["action"] == "propose":
        client.post(
            "/api/workflow/propose",
            {"title": "Идея от агента", "scenarios": [SCENARIO]},
            mode="plan",
        )
        client.post("/api/workflow/ideas-ready", {}, mode="plan")
    elif flow["hint"]["action"] == "implement":
        client.post(
            "/api/agent/report",
            {"part": "backend", "summary": "Сделано агентом", "json": {"updates": []}},
            mode="implement",
        )
    else:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
