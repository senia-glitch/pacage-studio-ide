"""Разделы ТЗ и отчёта фронтенда: выбор, порядок, ошибки ввода, структурные данные."""

from __future__ import annotations

import pytest
from test_frontend_sync import _graph

from studio.frontend.brief import (
    BRIEF_SECTIONS,
    REPORT_SECTIONS,
    brief_frontend,
    pick_sections,
    report_frontend,
)
from studio.metadata import MetadataError
from studio.server.app import front_report_op, frontend_brief_op


def test_отчёт_по_умолчанию_содержит_все_разделы(tmp_path):
    root, _ = _graph(tmp_path)
    result = report_frontend(root)
    text = result["markdown"]
    for heading in ("Структура", "Покрытие правил описанием", "Нарушения", "Связи импортов"):
        assert f"## {heading}" in text
    assert text.startswith("# Отчёт о существующем фронтенде")
    assert result["data"]["sections"] == list(REPORT_SECTIONS)
    assert {"stack", "stats", "pages", "coverage", "violations", "edges"} <= set(result["data"])


def test_отчёт_только_выбранные_разделы_в_фиксированном_порядке(tmp_path):
    root, _ = _graph(tmp_path)
    result = report_frontend(root, sections=["violations", "coverage"])
    text = result["markdown"]
    assert "## Нарушения" in text and "## Покрытие правил описанием" in text
    assert "## Структура" not in text and "Связи импортов" not in text
    assert text.index("Покрытие") < text.index("## Нарушения")
    assert set(result["data"]) == {"root", "sections", "coverage", "violations"}
    assert (root / ".studio" / "frontend-report.md").read_text(encoding="utf-8") == text


def test_отчёт_ошибки_ввода(tmp_path):
    root, _ = _graph(tmp_path)
    with pytest.raises(ValueError, match="Выберите хотя бы один раздел"):
        report_frontend(root, sections=[])
    with pytest.raises(ValueError, match="Неизвестные разделы отчёта: ghost"):
        report_frontend(root, sections=["structure", "ghost"])
    with pytest.raises(ValueError, match="списком"):
        report_frontend(root, sections="structure")
    with pytest.raises(ValueError, match="отсутствует"):
        report_frontend(tmp_path / "nowhere")


def test_тз_разделы_включаются_по_одному(tmp_path):
    root, _ = _graph(tmp_path)
    full = brief_frontend(root)["markdown"]
    for heading in ("## 2. Правила работы", "## 3. Область работ", "## 4. Нарушения", "## 5. Проверка"):
        assert heading in full
    only_rules = brief_frontend(root, sections=[])["markdown"]
    assert "## 2. Правила работы" in only_rules and "## 1. Что нужно сделать" in only_rules
    assert "## 3." not in only_rules and "## 4." not in only_rules and "## 5." not in only_rules
    outline = brief_frontend(root, sections=["outline"])["markdown"]
    assert "## 3. Область работ" in outline and "## 4." not in outline
    # область «только нарушения» не показывает карту, даже если раздел выбран
    assert "## 3." not in brief_frontend(root, scope="violations", sections=list(BRIEF_SECTIONS))[
        "markdown"
    ]


def test_http_операции_превращают_ошибки_в_ввод(tmp_path):
    root, _ = _graph(tmp_path)
    assert frontend_brief_op(root, {"scope": "all", "sections": ["outline"]})["tasks"] >= 0
    assert "## Структура" in front_report_op(root, {"sections": ["structure"]})["markdown"]
    with pytest.raises(MetadataError) as info:
        front_report_op(root, {"sections": ["ghost"]})
    assert info.value.code == "invalid_input"
    with pytest.raises(MetadataError):
        frontend_brief_op(root, {"scope": "node", "nodeId": "page:ghost"})


def test_pick_sections_none_даёт_все():
    assert pick_sections(None, BRIEF_SECTIONS, "ТЗ") == set(BRIEF_SECTIONS)
