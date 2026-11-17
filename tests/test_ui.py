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
    assert 'data-tab="ideas"' not in HTML and 'data-tab="idea"' not in HTML  # прежние вкладки убраны
    assert 'data-tab="scenario"' not in HTML
    assert "Бэкенд" in HTML
    assert 'data-map-mode="fact"' in HTML
    assert 'data-map-mode="plan"' in HTML


def test_разделы_в_порядке_цепочки():
    """Конвейер → Система → Настройки; в «Системе» — обзор, карты."""
    top = re.findall(r'<button class="tabbtn[^"]*" id="tab-[a-z]+-button" data-tab="([a-z]+)"', HTML)
    assert top == ["pipeline", "system", "settings"]
    sub = re.findall(r'<button class="subtab[^"]*" id="tab-[a-z]+-button" data-tab="([a-z]+)"', HTML)
    assert sub == ["overview", "map", "frontend"]
    for title in ("Конвейер", "Система", "Настройки", "Обзор", "Бэкенд", "Фронтенд"):
        assert title in HTML
    for section in ("pipeline", "overview", "map", "frontend", "settings"):
        assert f'id="tab-{section}"' in HTML
    for gone in ("idea", "ideas", "agent"):
        assert f'id="tab-{gone}"' not in HTML


def test_настройки_проекта_отдельно_от_конвейера():
    """«Настройки» — серверы, тесты и откат проекта; настройки конвейера — на его экране."""
    assert 'id="projectBody"' in HTML
    project = (JS_DIR / "projectview.js").read_text(encoding="utf-8")
    for route in ("/api/devservers/config", "/api/tests/config", "/api/git/rollback"):
        assert route in project
    assert "showView" in project and "settings" in project


def test_верхняя_панель_без_лишнего():
    assert 'id="zIn"' not in HTML and 'id="zOut"' not in HTML  # есть колесо мыши
    assert 'id="editorChoice"' not in HTML  # поддерживается только VS Code
    header = HTML[HTML.index("<header>"):HTML.index("</header>")]
    assert 'id="searchBox"' not in header and 'id="frontSearch"' not in header
    for gone in ("briefBtn", "agentBtn", "agentReportsBtn", "todoMenuPanel"):
        assert f'id="{gone}"' not in header  # агентской цепочки в шапке больше нет


def test_поиск_узлов_в_левых_панелях_карт():
    tree = HTML[HTML.index('id="treePanel"'):HTML.index('id="treeBody"')]
    assert 'id="searchBox"' in tree
    front = HTML[HTML.index('class="front-tree"'):HTML.index('id="frontTreeBody"')]
    assert 'id="frontSearch"' in front


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
    assert (JS_DIR / "projectview.js").is_file()
    assert (JS_DIR / "pipeline" / "main.js").is_file()
    assert not (JS_DIR / "scenarioview.js").exists()
    assert not (JS_DIR / "createview.js").exists()
    assert not (JS_DIR / "chainmodel.js").exists()
    assert not (JS_DIR / "legacy.js").exists()
    for gone in ("ideaview.js", "roadmapview.js", "agentview.js", "briefview.js", "syncview.js", "idea"):
        assert not (JS_DIR / gone).exists(), gone
    assert not (STATIC / "idea.css").exists() and not (STATIC / "roadmap.css").exists()
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


def test_редактор_только_vscode():
    editor = (JS_DIR / "editor.js").read_text(encoding="utf-8")
    assert "vscode://" in editor
    assert "cursor://" not in editor
    assert "pycharm://" not in editor


def test_перерисовка_не_сбрасывает_прокрутку_и_фокус():
    """Выбор в выпадающем списке настроек не должен кидать экран наверх."""
    main = (JS_DIR / "pipeline" / "main.js").read_text(encoding="utf-8")
    for part in ("ctx.track", "scrollSnapshot", "restoreScroll", "data-focus", "minHeight"):
        assert part in main, part
    settings = (JS_DIR / "pipeline" / "settings.js").read_text(encoding="utf-8")
    assert 'ctx.go("settings")' not in settings.split("async function save")[1].split("function modeBlock")[0]
    assert 'data-focus' in settings
    assert "keepScroll" in (JS_DIR / "util.js").read_text(encoding="utf-8")
    for name in ("inspectorview.js", "frontendview.js"):
        assert "keepScroll" in (JS_DIR / name).read_text(encoding="utf-8"), name
    assert "scrollTop" in (JS_DIR / "projectview.js").read_text(encoding="utf-8")
