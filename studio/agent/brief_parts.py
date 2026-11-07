"""Разбор markdown ТЗ на разделы и задачи: чтобы агент читал его по частям, а не целиком.

ТЗ бывает очень большим (десятки тысяч символов с образцами кода), и целиком оно не
помещается в ответ инструмента. Разделы — заголовки ``## N. …``, задачи — ``### T<n>. …``;
оба вида есть у ТЗ бэкенда и фронтенда. Модуль чистый: только разбор текста.
"""

from __future__ import annotations

import re

SECTION_RE = re.compile(r"^## (?P<key>\d+(?:\.\d+)?)\.\s*(?P<title>.*)$")
TASK_RE = re.compile(r"^### T(?P<number>\d+)\.\s*(?P<title>.*)$")
PAGE_LIMIT = 40000  # символов за один ответ инструмента


def split_brief(markdown: str) -> dict:
    """``{"sections": [{key, title, text}], "tasks": [{number, title, text}]}`` по тексту ТЗ."""
    sections: list[dict] = []
    tasks: list[dict] = []
    section: dict | None = None
    task: dict | None = None
    for line in str(markdown or "").splitlines():
        found = SECTION_RE.match(line)
        if found:
            task = None
            section = {"key": found["key"], "title": found["title"].strip(), "lines": [line]}
            sections.append(section)
            continue
        started = TASK_RE.match(line)
        if started:
            task = {
                "number": int(started["number"]),
                "title": started["title"].strip(),
                "lines": [line],
            }
            tasks.append(task)
        elif task is not None:
            task["lines"].append(line)
        if section is not None:
            section["lines"].append(line)
    return {
        "sections": [
            {"key": s["key"], "title": s["title"], "text": "\n".join(s["lines"]).rstrip() + "\n"}
            for s in sections
        ],
        "tasks": [
            {
                "number": t["number"],
                "title": t["title"],
                "text": "\n".join(t["lines"]).rstrip() + "\n",
            }
            for t in tasks
        ],
    }


def page(text: str, offset: int = 0, limit: int = PAGE_LIMIT) -> dict:
    """Кусок текста с позицией продолжения (``next_offset`` — None, если это конец)."""
    start = max(0, int(offset or 0))
    end = start + limit
    return {
        "markdown": text[start:end],
        "offset": start,
        "next_offset": end if end < len(text) else None,
        "chars": len(text),
    }


def outline(markdown: str, records: list[dict] | None = None) -> dict:
    """Оглавление: задачи и разделы с размерами; записи задач сжимаются до имени."""
    parts = split_brief(markdown)
    names: dict[int, str] = {}
    kinds: dict[int, str] = {}
    for record in records or []:
        number = record.get("number")
        if isinstance(number, int):
            kinds[number] = str(record.get("type") or record.get("kind") or "")
            names[number] = str(
                record.get("slug") or record.get("anchor") or record.get("name")
                or record.get("id") or ""
            )
    return {
        "chars": len(markdown or ""),
        "tasks": [
            {
                "number": t["number"],
                "kind": kinds.get(t["number"], ""),
                "name": names.get(t["number"], ""),
                "title": t["title"],
                "chars": len(t["text"]),
            }
            for t in parts["tasks"]
        ],
        "sections": [
            {"key": s["key"], "title": s["title"], "chars": len(s["text"])}
            for s in parts["sections"]
        ],
    }


__all__ = ["PAGE_LIMIT", "outline", "page", "split_brief"]
