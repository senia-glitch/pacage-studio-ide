"""Отчёт по бэкенду: разделы, содержимое, CLI и HTTP-состояние."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio import cli
from studio.agent.report import REPORT_SECTIONS, build_report
from studio.metadata import MetadataError
from studio.scanner import scan
from studio.server.state import ProjectState


def test_все_разделы_по_умолчанию(demo: Path) -> None:
    result = build_report(scan(demo))
    text = result["markdown"]
    assert text.startswith("# Отчёт по проекту")
    for title in (
        "Сводка",
        "Структура",
        "Покрытие описаниями и тестами",
        "Планы сценариев",
        "Модели и таблицы",
        "Диагностика",
    ):
        assert f"## {title}" in text
    assert result["data"]["sections"] == list(REPORT_SECTIONS)
    assert set(REPORT_SECTIONS) <= set(result["data"])


def test_структура_и_покрытие_сходятся_с_графом(demo: Path) -> None:
    graph = scan(demo)
    data = build_report(graph)["data"]
    endpoints = [e for block in data["structure"] for e in block["endpoints"]]
    assert {e["id"] for e in endpoints} == {n.id for n in graph.nodes if n.kind == "endpoint"}
    cover = data["coverage"]["kinds"]["endpoint"]
    facts = [n for n in graph.nodes if n.kind == "endpoint" and not n.planned]
    assert cover["total"] == len(facts)
    assert cover["described"] == sum(1 for n in facts if n.description.strip())
    missing = {item["id"] for item in data["coverage"]["without_description"]}
    assert all(not n.description.strip() for n in graph.nodes if n.id in missing)
    assert data["summary"]["counts"]["endpoint"]["fact"] == len(facts)


def test_выбранные_разделы_в_фиксированном_порядке(demo: Path) -> None:
    result = build_report(scan(demo), sections=["models", "summary"])
    text = result["markdown"]
    assert text.index("## Сводка") < text.index("## Модели и таблицы")
    assert "## Структура" not in text
    assert set(result["data"]) == {"root", "sections", "summary", "models"}


def test_ошибки_ввода(demo: Path) -> None:
    graph = scan(demo)
    with pytest.raises(ValueError, match="Выберите хотя бы один раздел"):
        build_report(graph, sections=[])
    with pytest.raises(ValueError, match="Неизвестные разделы отчёта: ghost"):
        build_report(graph, sections=["summary", "ghost"])
    with pytest.raises(ValueError, match="списком"):
        build_report(graph, sections="summary")


def test_отчёт_детерминирован(demo: Path) -> None:
    graph = scan(demo)
    assert build_report(graph)["markdown"] == build_report(graph)["markdown"]


def test_cli_report_файл_stdout_json(demo: Path, capsys) -> None:
    assert cli.main(["report", "--project", str(demo)]) == 0
    assert "Отчёт сохранён: .studio/report.md" in capsys.readouterr().out
    saved = (demo / ".studio" / "report.md").read_text(encoding="utf-8")
    assert saved.startswith("# Отчёт по проекту")

    assert cli.main(["report", "--project", str(demo), "--stdout", "--section", "summary"]) == 0
    out = capsys.readouterr().out
    assert "## Сводка" in out and "## Структура" not in out

    assert cli.main(["report", "--project", str(demo), "--json", "--section", "models"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["part"] == "backend" and payload["path"] == ".studio/report.md"
    assert list(payload["data"]) == ["root", "sections", "models"]


def test_cli_report_ошибки_и_фронтенд(demo: Path, capsys) -> None:
    assert cli.main(["report", "--project", str(demo), "--section", "ghost"]) == 2
    assert "Неизвестные разделы" in capsys.readouterr().out
    assert cli.main(["report", "--project", str(demo), "--part", "frontend"]) == 2
    assert "frontend/ отсутствует" in capsys.readouterr().out
    front = demo / "frontend" / "src" / "ui" / "pages" / "home"
    front.mkdir(parents=True)
    (front / "index.vue").write_text("<template><div/></template>", encoding="utf-8")
    assert (
        cli.main(["report", "--project", str(demo), "--part", "frontend", "--stdout",
                  "--section", "structure"])
        == 0
    )
    assert "## Структура" in capsys.readouterr().out


def test_state_report_пишет_файл_и_отклоняет_плохой_ввод(demo: Path) -> None:
    state = ProjectState(demo)
    state.rescan("start")
    result = state.report({"sections": ["summary", "plans"]})
    assert result["path"] == ".studio/report.md"
    assert (demo / ".studio" / "report.md").read_text(encoding="utf-8") == result["markdown"]
    with pytest.raises(MetadataError) as info:
        state.report({"sections": ["ghost"]})
    assert info.value.code == "invalid_input"
