"""Необязательные разделы ТЗ бэкенда и сохранение ТЗ с правками пользователя."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio import cli
from studio.agent import BriefError, BriefOptions, build_brief
from studio.agent.brief import BRIEF_SECTIONS
from studio.env import read_env
from studio.metadata import MetadataError
from studio.scanner import scan
from studio.server.state import ProjectState
from studio.source import load_sources

GOLDEN = Path(__file__).resolve().parent / "golden" / "full"

HEADINGS = {
    "map": "## 5. Карта проекта",
    "unready": "## 6. Не готово к реализации",
    "diagnostics": "## 7. Предупреждения по коду",
    "anchors": "## 8. Формат якорей и связей",
}
ALWAYS = (
    "## 1. Что нужно сделать",
    "## 2. Правила работы",
    "## 4. Задачи",
    "## 9. Отчёт и проверка",
)


def _brief(root: Path, **options):
    graph = scan(root)
    return build_brief(
        graph,
        load_sources(read_env(root)),
        BriefOptions(generated_at="2026-01-01 00:00 UTC", map_mode="full", **options),
    )


def test_без_sections_все_разделы() -> None:
    text = _brief(GOLDEN).markdown
    for heading in (*HEADINGS.values(), *ALWAYS):
        assert heading in text


def test_каждый_раздел_отключается_отдельно() -> None:
    for key in BRIEF_SECTIONS:
        others = tuple(k for k in BRIEF_SECTIONS if k != key)
        text = _brief(GOLDEN, sections=others).markdown
        assert HEADINGS[key] not in text, key
        for other in others:
            assert HEADINGS[other] in text, (key, other)
        for heading in ALWAYS:
            assert heading in text


def test_пустой_набор_оставляет_только_обязательное() -> None:
    text = _brief(GOLDEN, sections=()).markdown
    for heading in HEADINGS.values():
        assert heading not in text
    for heading in ALWAYS:
        assert heading in text
    assert "ОДИН JSON-блок" in text


def test_sections_меняют_brief_id_и_попадают_в_служебную_копию() -> None:
    full = _brief(GOLDEN)
    partial = _brief(GOLDEN, sections=("map",))
    assert full.brief_id != partial.brief_id
    assert full.payload()["options"]["sections"] is None
    assert partial.payload()["options"]["sections"] == ["map"]
    assert _brief(GOLDEN, sections=("map", "anchors")).brief_id == _brief(
        GOLDEN, sections=("anchors", "map")
    ).brief_id


def test_неизвестный_раздел_это_ошибка_ввода() -> None:
    with pytest.raises(BriefError) as info:
        _brief(GOLDEN, sections=("map", "ghost"))
    assert info.value.code == "invalid_input" and "ghost" in info.value.message


def test_cli_section(demo: Path, capsys) -> None:
    code = cli.main(["brief", "--project", str(demo), "--stdout", "--section", "map"])
    assert code == 0
    out = capsys.readouterr().out
    assert HEADINGS["map"] in out and HEADINGS["anchors"] not in out
    assert cli.main(["brief", "--project", str(demo), "--stdout", "--section", "ghost"]) == 1
    assert "ghost" in capsys.readouterr().out


def _state(demo: Path) -> ProjectState:
    state = ProjectState(demo)
    state.rescan("start")
    return state


def test_state_sections_и_плохой_тип(demo: Path) -> None:
    state = _state(demo)
    data = state.brief({"map": "none", "sections": ["anchors"]})
    assert HEADINGS["anchors"] in data["markdown"]
    assert HEADINGS["unready"] not in data["markdown"]
    with pytest.raises(MetadataError):
        state.brief({"sections": "anchors"})


def test_сохранение_с_правками_пользователя(demo: Path) -> None:
    state = _state(demo)
    generated = state.brief({"map": "none"})["markdown"]
    edited = generated.replace("## 1. Что нужно сделать", "## 1. Моя правка")
    saved = state.save_brief({"map": "none", "markdown": edited})
    text = (demo / saved["path"]).read_text(encoding="utf-8") if not Path(
        saved["path"]
    ).is_absolute() else Path(saved["path"]).read_text(encoding="utf-8")
    assert "## 1. Моя правка" in text
    assert json.loads(
        (demo / ".studio" / "last-brief.json").read_text(encoding="utf-8")
    )["brief_id"] == saved["brief_id"]


def test_сохранение_без_маркера_отклоняется(demo: Path) -> None:
    state = _state(demo)
    generated = state.brief({"map": "none"})["markdown"]
    stripped = generated.split("<!-- studio-brief:")[0]
    with pytest.raises(MetadataError) as info:
        state.save_brief({"map": "none", "markdown": stripped})
    assert info.value.code == "invalid_input" and "маркер" in info.value.message
    with pytest.raises(MetadataError):
        state.save_brief({"map": "none", "markdown": "   "})
