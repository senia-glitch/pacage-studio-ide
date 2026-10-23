from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "studio/ui/static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
LEGACY = (STATIC / "js/legacy.js").read_text(encoding="utf-8")


def test_три_вкладки_одного_окна():
    assert 'data-tab="map"' in HTML
    assert 'data-tab="scenario"' in HTML
    assert 'data-tab="create"' in HTML
    assert "Карта" in HTML and "Сценарий" in HTML and "Создание" in HTML


def test_интерфейс_на_русском():
    for text in ("Без домена", "эндпоинт", "сценарий", "модель", "критерии"):
        assert text in (HTML + LEGACY)


def test_index_компактный_и_без_inline_скриптов():
    assert len(HTML.splitlines()) < 150
    assert "<style>" not in HTML
    assert not re.search(r"<script(?![^>]*\bsrc=)", HTML)
    assert not re.search(r"\son(click|change|load|submit|input)=", HTML, re.I)
    assert "javascript:" not in HTML.lower()


def test_офлайн_без_cdn():
    assert "http://" not in HTML and "https://" not in HTML
    external = LEGACY.replace("http://www.w3.org/2000/svg", "")
    assert "http://" not in external and "https://" not in external


def test_статика_на_месте():
    assert (STATIC / "app.css").is_file()
    assert (STATIC / "js/main.js").is_file()
    assert (STATIC / "js/legacy.js").is_file()
    assert (STATIC / "js/briefmodel.js").is_file()
    assert '<meta name="studio-token" content="">' in HTML


def test_есть_ключевые_возможности():
    assert "/api/metadata/node" in LEGACY
    assert "X-Studio-Token" in LEGACY
    assert "buildEditorUrl" in LEGACY
    assert "localStorage" in LEGACY
    assert "incompleteNodes" in LEGACY
    assert "Неполные" in LEGACY or "неполные" in LEGACY
    assert "inferEdgeKind" in LEGACY
    assert "enableNodeDrag" in LEGACY


def test_есть_тз_для_агента():
    assert 'id="briefBtn"' in HTML
    assert 'id="todoMenuPanel"' in HTML
    assert 'id="todoBriefAction"' in HTML
    assert "openBriefDialog" in LEGACY
    assert "/api/brief" in LEGACY
    assert "Выполнено" in LEGACY


def test_todo_узлы_явно_видны():
    todo_js = (STATIC / "js/todo.js").read_text(encoding="utf-8")
    assert "TODO · нет кода" in todo_js
    assert "TODO · нет описания" in todo_js
    assert "placeNode" in LEGACY
    assert "groupOffPath" in LEGACY
    assert "Вне путей запроса" in LEGACY
    assert "в цепочке TODO" in LEGACY
    assert "node--todo" in LEGACY
    assert "Кода пока нет. Файл-план" in LEGACY
    assert "Что сделать" in LEGACY
    assert "todoHighlight" in LEGACY
