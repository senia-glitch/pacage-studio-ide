"""Руководство для AI-агента (guide.md): один текст для любого агента.

Единственный источник — ``studio/agent/guide.md`` (про инструменты MCP-сервера). Он же
отдаётся клиенту как ``instructions`` MCP при подключении и устанавливается в проект
командой ``studio skill``: как скилл Claude Code (``.claude/skills/pacage-studio/SKILL.md``)
и/или как блок в ``AGENTS.md``. Блок в ``AGENTS.md`` обновляется между маркерами и не
трогает остальной текст файла.
"""

from __future__ import annotations

import json
from pathlib import Path

from studio.store import atomic_write

SKILL_NAME = "pacage-studio"
SKILL_DESCRIPTION = (
    "Работа с проектом через MCP-сервер pacage-studio (инструменты studio_*): планирование "
    "идей и узлов, реализация выбранного ТЗ, проверка по коду и отчёт. Используй, когда "
    "пользователь даёт строку подключения студии (Studio, Token, Mode) или просит работать "
    "через studio."
)
BEGIN = "<!-- pacage-studio:begin -->"
END = "<!-- pacage-studio:end -->"
TARGETS = ("claude", "agents")


def load_guide() -> str:
    """Текст руководства."""
    return Path(__file__).with_name("guide.md").read_text(encoding="utf-8").strip() + "\n"


def skill_markdown(version: str) -> str:
    """Файл скилла Claude Code: frontmatter и руководство."""
    description = json.dumps(SKILL_DESCRIPTION, ensure_ascii=False)  # в описании есть «: »
    return (
        f"---\nname: {SKILL_NAME}\ndescription: {description}\n---\n"
        f"<!-- pacage-studio {version}: файл создан `studio skill`, правьте руководство "
        f"в пакете -->\n\n{load_guide()}"
    )


def agents_block(version: str) -> str:
    """Управляемый блок для AGENTS.md."""
    return (
        f"{BEGIN}\n<!-- pacage-studio {version}: блок создан `studio skill --target "
        f"agents`; обновляется между маркерами -->\n\n{load_guide()}\n{END}\n"
    )


def merge_agents_md(existing: str, block: str) -> str:
    """Вставить или заменить управляемый блок, не трогая остальной текст."""
    start = existing.find(BEGIN)
    stop = existing.find(END)
    if start >= 0 and stop > start:
        tail = existing[stop + len(END):].lstrip("\n")
        head = existing[:start]
        return head + block + ("\n" + tail if tail else "")
    prefix = existing.rstrip("\n")
    return (prefix + "\n\n" if prefix else "") + block


def install(root: Path, target: str, version: str) -> list[Path]:
    """Записать скилл/блок в проект; вернуть записанные файлы."""
    root = Path(root)
    wanted = TARGETS if target == "all" else (target,)
    written: list[Path] = []
    if "claude" in wanted:
        path = root / ".claude" / "skills" / SKILL_NAME / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not atomic_write(path, skill_markdown(version)):
            raise OSError(f"Не удалось записать {path}")
        written.append(path)
    if "agents" in wanted:
        path = root / "AGENTS.md"
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        if not atomic_write(path, merge_agents_md(current, agents_block(version))):
            raise OSError(f"Не удалось записать {path}")
        written.append(path)
    return written


__all__ = [
    "BEGIN",
    "END",
    "SKILL_NAME",
    "TARGETS",
    "agents_block",
    "install",
    "load_guide",
    "merge_agents_md",
    "skill_markdown",
]
