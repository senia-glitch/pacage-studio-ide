"""Какие агенты поддерживает студия и что им запрещено.

Студия поддерживает два агента: Claude Code (подписка Pro) и OpenCode (Go Plus). Запускает их
``studio.pipeline.llm`` (текстовые роли без доступа к файлам) и ``studio.pipeline.coder``
(исполнитель кода с доступом к файлам проекта, но без опасных команд).
"""

from __future__ import annotations

CLAUDE = "claude"
OPENCODE = "opencode"
AGENTS = (CLAUDE, OPENCODE)

# Запрещено агенту всегда: пуш и смену истории делает студия, пакеты ставит человек.
DENIED_TOOLS = [
    "Bash(git push:*)",
    "Bash(git remote:*)",
    "Bash(git checkout:*)",
    "Bash(git reset:*)",
    "Bash(pip install:*)",
    "Bash(npm install:*)",
    "Bash(rm -rf:*)",
]
ALLOWED_BASH = ["python", "pytest", "npm", "git status", "git diff"]

__all__ = ["AGENTS", "ALLOWED_BASH", "CLAUDE", "DENIED_TOOLS", "OPENCODE"]
