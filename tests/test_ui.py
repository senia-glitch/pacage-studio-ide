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


def test_две_рабочие_области_одного_окна():
    assert 'data-tab="map"' in HTML
    assert 'data-tab="ideas"' in HTML
    assert 'data-tab="scenario"' not in HTML
    assert "Карта backend" in HTML
    assert "Генератор идей" in HTML
    assert 'data-map-mode="fact"' in HTML
    assert 'data-map-mode="plan"' in HTML


def test_пять_страниц_в_согласованном_порядке():
    tabs = re.findall(r'<button class="tabbtn[^"]*" id="tab-[a-z]+-button" data-tab="([a-z]+)"', HTML)
    assert tabs == ["map", "frontend", "ideas", "settings", "agent"]
    for title in ("Карта backend", "Карта frontend", "Генератор идей",
                  "Настройки промптов", "Настройки агента"):
        assert title in HTML


def test_верхняя_панель_без_лишнего():
    assert 'id="zIn"' not in HTML and 'id="zOut"' not in HTML  # есть колесо мыши
    assert 'id="editorChoice"' not in HTML  # поддерживается только VS Code
    header = HTML[HTML.index("<header>"):HTML.index("</header>")]
    assert 'id="searchBox"' not in header and 'id="frontSearch"' not in header
    assert 'data-ui-mode="agent"' in header and 'data-ui-mode="manual"' in header


def test_поиск_узлов_в_левых_панелях_карт():
    tree = HTML[HTML.index('id="treePanel"'):HTML.index('id="treeBody"')]
    assert 'id="searchBox"' in tree
    front = HTML[HTML.index('class="front-tree"'):HTML.index('id="frontTreeBody"')]
    assert 'id="frontSearch"' in front
    assert 'id="ideaSearch"' in HTML


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
    assert (JS_DIR / "inspectorview.js").is_file()
    assert (JS_DIR / "drag.js").is_file()
    assert (JS_DIR / "geometry.js").is_file()
    assert (JS_DIR / "diagnosticsview.js").is_file()
    assert (JS_DIR / "briefview.js").is_file()
    assert not (JS_DIR / "scenarioview.js").exists()
    assert not (JS_DIR / "createview.js").exists()
    assert not (JS_DIR / "chainmodel.js").exists()
    assert not (JS_DIR / "legacy.js").exists()
    assert '<meta name="studio-token" content="">' in HTML


def test_есть_ключевые_возможности():
    assert "/api/metadata/graph-node" in JS
    assert "/api/metadata/graph-node/delete" in JS
    assert "/api/metadata/planned-change" in JS
    assert "/api/metadata/domain-plan" in JS
    assert "X-Studio-Token" in JS
    assert "buildEditorUrl" in JS
    assert "localStorage" in JS
    assert "enableNodeDrag" in JS
    assert "clampGroupDelta" in JS
    assert "requestAnimationFrame" in JS
    assert "@branch:" in (JS_DIR / "mapview.js").read_text(encoding="utf-8")
    assert 'id="inspectorResize"' in HTML
    assert 'id="inspectorToggle"' in HTML


def test_есть_тз_для_агента():
    assert 'id="briefBtn"' in HTML
    assert 'id="todoMenuPanel"' in HTML
    assert 'id="todoBriefAction"' in HTML
    assert "openBriefDialog" in JS
    assert "/api/brief" in JS
    assert "Выполнено" in JS


def test_планирование_и_бриф_для_агента():
    assert 'id="briefBtn"' in HTML
    assert "К реализации" in (JS_DIR / "briefmodel.js").read_text(encoding="utf-8")
    assert "openBriefDialog" in (JS_DIR / "briefview.js").read_text(encoding="utf-8")


def test_редактор_только_vscode():
    editor = (JS_DIR / "editor.js").read_text(encoding="utf-8")
    assert "vscode://" in editor
    assert "cursor://" not in editor
    assert "pycharm://" not in editor
