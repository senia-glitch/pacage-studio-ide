"""Поддельный агент для сквозного теста автоматического хода: действует по тексту запроса.

Различает запуски так же, как настоящий агент читал бы запрос студии: итерация идей, решение
карточек контролёром, проверка/тесты сценария, исправление, итоговый отчёт. Каждый запуск
дописывает строку в ``.studio/fake-calls.log``, а проверка сценария в первый раз находит замечание.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from studio.agent.client import StudioClient

SCENARIOS = [
    {"title": "Список уроков", "slug": "list_lessons", "description": "Возвращает список",
     "acceptance": ["список"], "http_method": "GET", "path": "/lessons"},
    {"title": "Создать урок", "slug": "create_lesson", "description": "Создаёт урок",
     "acceptance": ["201"], "http_method": "POST", "path": "/lessons"},
    {"title": "Счётчик уроков", "slug": "count_lessons", "description": "Считает уроки",
     "acceptance": ["число"], "http_method": "GET", "path": "/lessons/count"},
]


def main() -> int:
    prompt = sys.argv[1]
    root = Path(os.environ["STUDIO_PROJECT"])
    log = root / ".studio" / "fake-calls.log"
    client = StudioClient.discover()

    def note(kind: str) -> None:
        with open(log, "a", encoding="utf-8") as stream:
            stream.write(kind + "\n")

    if "Карточки к решению" in prompt:
        ids = re.findall(r"id=(\S+) \|", prompt)
        note("decide")
        client.post("/api/control/decide", {"decisions": [
            {"id": item, "decision": "accepted", "reason": "нужно"} for item in ids]}, mode="plan")
    elif "Только что реализован" in prompt:
        step = "verify" if "Проверь реализацию" in prompt else "tests"
        marker = root / ".studio" / "fake-failed.json"
        failed = json.loads(marker.read_text("utf-8")) if marker.exists() else []
        unit = re.search(r"«([^»]+)»", prompt.split("Только что реализован:")[1]).group(1)
        if step == "verify" and unit not in failed:
            marker.write_text(json.dumps([*failed, unit]), encoding="utf-8")
            note(f"check:{step}:fail")
            client.post("/api/control/result", {"ok": False, "remarks": ["нет проверки прав"]},
                        mode="implement")
        else:
            note(f"check:{step}:ok")
            client.post("/api/control/result", {"ok": True}, mode="implement")
    elif "Контроль нашёл замечания" in prompt:
        note("fix")
    elif "Все сценарии ТЗ реализованы" in prompt:
        note("final")
        client.post("/api/agent/report", {"part": "backend", "summary": "Всё сделано",
                                          "json": {"updates": []}}, mode="implement")
    elif "Реализация идёт по сценариям" in prompt:
        note("unit")
    elif "на этапе «Реализация»" in prompt:
        note("implement")
        client.post("/api/agent/report", {"part": "backend", "summary": "Сделано агентом",
                                          "json": {"updates": []}}, mode="implement")
    else:  # идеи
        match = re.search(r"Итерация идей (\d+) из", prompt)
        number = int(match.group(1)) if match else 1
        note(f"ideas:{number}")
        client.post("/api/workflow/propose", {"title": f"Идея {number}",
                                              "scenarios": [SCENARIOS[number - 1]]}, mode="plan")
        client.post("/api/workflow/ideas-ready", {}, mode="plan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
