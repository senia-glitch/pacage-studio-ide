"""Руководство агента: согласованность с маршрутами, команда `studio skill`, `/api/agent/guide`."""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

import pytest

from studio import cli
from studio.agent import guide
from studio.server import access
from studio.server.state import ProjectState

ROOT = Path(__file__).resolve().parent.parent
ROUTE_RE = re.compile(r"\b(GET|POST) (/api/[A-Za-z0-9_/.-]+)")


# ------------------------------------------------- руководство и маршруты


def test_каждый_маршрут_из_руководства_существует_и_доступен_агенту() -> None:
    text = guide.load_guide()
    routes = {(method, path.rstrip(".")) for method, path in ROUTE_RE.findall(text)}
    assert len(routes) >= 15, routes
    for method, path in sorted(routes):
        assert (method, path) in access.ROUTE_DOCS, f"{method} {path} нет в ROUTE_DOCS"
        assert any(
            access.route_allowed(mode, method, path) for mode in access.AGENT_MODES
        ), f"{method} {path} закрыт обоим режимам агента"


def test_руководство_называет_режимы_подключение_и_запреты() -> None:
    text = guide.load_guide()
    for needle in ("X-Studio-Token", "GET /api/agent", "planning", "implement",
                   "POST /api/agent/report", "GET /api/agent/guide", "mode_forbidden",
                   "ideas_conflict", "`/api/plan`"):
        assert needle in text, needle


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
    assert "9.9.9" in text


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
    skill = (root / ".claude/skills/pacage-studio/SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---") and "name: pacage-studio" in skill
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith("# Мой проект") and guide.BEGIN in agents
    # повторный запуск ничего не дублирует
    assert cli.main(["skill", "--target", "agents", "--project", str(root)]) == 0
    assert (root / "AGENTS.md").read_text(encoding="utf-8").count(guide.BEGIN) == 1


# ------------------------------------------------------------------- HTTP


@pytest.mark.integration
def test_guide_по_токенам_режимов(demo: Path) -> None:
    from test_server import _Server

    with _Server(demo) as srv:
        for mode in access.AGENT_MODES:
            token = access.derive_token(srv.token, mode)
            request = urllib.request.Request(
                srv.url + "/api/agent/guide", headers={"X-Studio-Token": token}
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.loads(response.read().decode("utf-8"))
            assert body["markdown"] == guide.load_guide() and body["studio_version"]
        info = ProjectState(demo).agent_info(access.PLAN)
        assert info["guide"] == "/api/agent/guide"
