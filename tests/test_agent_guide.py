"""Руководство агента: только про инструменты MCP, сверка с сервером, `studio skill`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from studio import cli
from studio.agent import guide

ROOT = Path(__file__).resolve().parent.parent
TOOL_RE = re.compile(r"studio_[a-z_]+")
NOT_TOOLS = {"studio_version", "studio_not_running"}  # поле и код ошибки, не инструменты


# ----------------------------------------------- руководство и инструменты MCP


def test_руководство_только_про_mcp_без_http() -> None:
    text = guide.load_guide()
    for needle in ("studio_connect", "plan", "implement", "mode_forbidden", "bad_token",
                   "not_connected", "studio_report", "studio_verify", "studio_brief"):
        assert needle in text, needle
    for http in ("/api/", "X-Studio-Token", "Content-Type", "curl", "Authorization"):
        assert http not in text, f"в руководстве осталась отсылка к HTTP: {http!r}"


def test_руководство_совпадает_с_инструментами_сервера() -> None:
    pytest.importorskip("mcp")
    from studio.mcp_server import create_server

    tools = set(create_server().studio_tools)
    mentioned = set(TOOL_RE.findall(guide.load_guide())) - NOT_TOOLS
    assert mentioned <= tools, f"в руководстве есть несуществующие инструменты: {mentioned - tools}"
    assert tools <= mentioned, f"инструменты без описания в руководстве: {tools - mentioned}"


def test_руководство_совпадает_с_документацией() -> None:
    docs = (ROOT / "docs/agent-workflow.md").read_text(encoding="utf-8")
    assert guide.load_guide().strip() in docs.replace("\r\n", "\n")


# ------------------------------------------------------------ файлы скилла


def test_файл_скилла_claude_имеет_frontmatter() -> None:
    text = guide.skill_markdown("9.9.9")
    assert text.startswith('---\nname: pacage-studio\ndescription: "')
    # описание в кавычках: внутри есть «: », без них YAML-парсер ломается
    assert text.split("\n")[2].endswith('"')
    assert "\n---\n" in text and guide.load_guide().strip() in text
    assert "9.9.9" in text and "MCP" in text.split("\n")[2]


def test_блок_agents_md_обновляется_не_трогая_чужой_текст() -> None:
    block = guide.agents_block("1.0.0")
    fresh = guide.merge_agents_md("", block)
    assert fresh.startswith(guide.BEGIN) and fresh.endswith(guide.END + "\n")

    mine = "# Мой проект\n\nПравила проекта.\n"
    merged = guide.merge_agents_md(mine, block)
    assert merged.startswith(mine.rstrip("\n")) and guide.BEGIN in merged

    newer = guide.merge_agents_md(merged + "\nПосле блока.\n", guide.agents_block("2.0.0"))
    assert newer.count(guide.BEGIN) == 1 and "2.0.0" in newer and "1.0.0" not in newer
    assert "Правила проекта." in newer and "После блока." in newer
    assert guide.merge_agents_md(newer, guide.agents_block("2.0.0")) == newer  # идемпотентно


# ---------------------------------------------------------------- команда


def test_cli_skill_stdout(capsys) -> None:
    assert cli.main(["skill"]) == 0
    assert capsys.readouterr().out == guide.load_guide()


def test_cli_skill_устанавливает_скилл_и_agents(tmp_path: Path, capsys) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "AGENTS.md").write_text("# Мой проект\n", encoding="utf-8")
    assert cli.main(["skill", "--target", "all", "--project", str(root)]) == 0
    out = capsys.readouterr().out
    assert ".claude/skills/pacage-studio/SKILL.md" in out and "AGENTS.md" in out
    assert "claude mcp add pacage-studio -- studio mcp" in out  # как подключить сервер
    skill = (root / ".claude/skills/pacage-studio/SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---") and "name: pacage-studio" in skill
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith("# Мой проект") and guide.BEGIN in agents
    # повторный запуск ничего не дублирует
    assert cli.main(["skill", "--target", "agents", "--project", str(root)]) == 0
    assert (root / "AGENTS.md").read_text(encoding="utf-8").count(guide.BEGIN) == 1
