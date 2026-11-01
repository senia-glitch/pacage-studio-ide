from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "studio/ui/static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS_DIR = STATIC / "js"
JS = "\n".join(
    path.read_text(encoding="utf-8")
    for path in sorted(JS_DIR.glob("*.js"))
)


def test_три_вкладки_одного_окна():
    assert 'data-tab="map"' in HTML
    assert 'data-tab="scenario"' in HTML
    assert 'data-tab="ideas"' in HTML
    assert "Карта проекта" in HTML
    assert "Конструктор сценариев" in HTML
    assert "Генератор идей" in HTML


def test_интерфейс_на_русском():
    for text in ("Без домена", "эндпоинт", "сценарий", "модель", "критерии"):
        assert text in (HTML + JS)


def test_index_компактный_и_без_inline_скриптов():
    assert len(HTML.splitlines()) < 160
    assert "<style>" not in HTML
    assert not re.search(r"<script(?![^>]*\bsrc=)", HTML)
    assert not re.search(r"\son(click|change|load|submit|input)=", HTML, re.I)
    assert "javascript:" not in HTML.lower()


def test_офлайн_без_cdn():
    assert "http://" not in HTML and "https://" not in HTML
    external = JS.replace("http://www.w3.org/2000/svg", "")
    assert "http://" not in external and "https://" not in external


def test_статика_на_месте():
    assert (STATIC / "app.css").is_file()
    assert (JS_DIR / "main.js").is_file()
    assert (JS_DIR / "app.js").is_file()
    assert (JS_DIR / "core.js").is_file()
    assert (JS_DIR / "mapview.js").is_file()
    assert (JS_DIR / "scenarioview.js").is_file()
    assert (JS_DIR / "inspectorview.js").is_file()
    assert (JS_DIR / "createview.js").is_file()
    assert (JS_DIR / "diagnosticsview.js").is_file()
    assert (JS_DIR / "briefview.js").is_file()
    assert not (JS_DIR / "legacy.js").exists()
    assert '<meta name="studio-token" content="">' in HTML


def test_есть_ключевые_возможности():
    assert "/api/metadata/node" in JS
    assert "/api/metadata/graph-node" in JS
    assert "/api/metadata/graph-node/delete" in JS
    assert "X-Studio-Token" in JS
    assert "buildEditorUrl" in JS
    assert "localStorage" in JS
    assert "incompleteNodes" in JS
    assert "Неполные" in JS or "неполные" in JS
    assert "enableNodeDrag" in JS
    assert "hideNested" in JS
    assert "nestedOf" in JS
    assert "Почему жёлтый" in JS
    assert "node--yellow" in JS


def test_есть_тз_для_агента():
    assert 'id="briefBtn"' in HTML
    assert 'id="todoMenuPanel"' in HTML
    assert 'id="todoBriefAction"' in HTML
    assert "openBriefDialog" in JS
    assert "/api/brief" in JS
    assert "Выполнено" in JS


def test_todo_узлы_явно_видны():
    todo_js = (JS_DIR / "todo.js").read_text(encoding="utf-8")
    assert "TODO · нет кода" in todo_js
    assert "TODO · нет описания" in todo_js
    assert "placeNode" in JS
    assert "groupOffPath" in JS
    assert "Вне путей запроса" in JS
    assert "в цепочке TODO" in JS
    assert "node--todo" in JS
    assert "Этот узел — TODO" in JS
    assert "Что сделать" in JS
    assert "todoHighlight" in JS
    assert 'id="hideNested"' in HTML
    assert "К реализации" in (JS_DIR / "briefmodel.js").read_text(encoding="utf-8")


def test_редактор_только_vscode():
    editor = (JS_DIR / "editor.js").read_text(encoding="utf-8")
    assert "vscode://" in editor
    assert "cursor://" not in editor
    assert "pycharm://" not in editor
