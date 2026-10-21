from __future__ import annotations

from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "studio/ui/index.html").read_text(encoding="utf-8")


def test_три_вкладки_одного_окна():
    assert 'data-tab="map"' in HTML
    assert 'data-tab="scenario"' in HTML
    assert 'data-tab="create"' in HTML
    assert "Карта" in HTML
    assert "Сценарий" in HTML
    assert "Создание" in HTML


def test_интерфейс_на_русском():
    for text in ("без домена", "эндпоинт", "сценарий", "модель", "критерии"):
        assert text in HTML


def test_карта_только_fastbase():
    assert "fastbase" in HTML
    assert "показаны только эндпоинты" in HTML


def test_скрытие_метаданных():
    assert "скрыть метаданные" in HTML
    assert 'id="hideMeta"' in HTML


def test_автосейв_при_потере_фокуса():
    assert "потере фокуса" in HTML
    assert 'addEventListener("blur"' in HTML
    assert "/api/metadata/node" in HTML


def test_создание_только_метаданных():
    assert "только метаданные" in HTML
    assert "Таблицы БД не выбираются" in HTML


def test_старые_режимы_убраны():
    for text in ("plan.json", "scaffold", "helper", "Конструктор областей"):
        assert text not in HTML


def test_создание_графическое():
    assert 'id="createStage"' in HTML
    assert 'id="createEdgeList"' in HTML
    assert "Потяните за точку" in HTML
    assert "inferEdgeKind" in HTML
    assert "createCardElement" in HTML


def test_сценарии_группируются_метод_домен():
    assert "methodsOfScenario" in HTML
    assert "domainsOfScenario" in HTML
    assert 'data-level="method"' in HTML
    assert 'data-level="domain"' in HTML


def test_узлы_перетаскиваются():
    assert "enableNodeDrag" in HTML
    assert "scenPos" in HTML
    assert "mapPos" in HTML


def test_области_и_периферийные_узлы():
    assert "map-fastbase" in HTML
    assert "map-method" in HTML
    assert "scen-zone" in HTML
    assert "create-zone" in HTML
    assert "periph-node" in HTML
    assert "scenPeripheralEdges" in HTML
    assert "enableZoneDrag" in HTML


def test_пустой_клик_и_esc_снимают_выделение():
    assert 'ev.key==="Escape"' in HTML
    assert "selectedNode=null" in HTML


def test_добавление_узлов_из_областей():
    assert 'class="zone-add"' in HTML
    assert "addCreateNode(kind)" in HTML
    assert 'id="createKind"' not in HTML
    assert 'id="addCreateNode"' not in HTML


def test_подсказка_к_имени_якорю():
    assert "tip-wrap" in HTML
    assert "Имя элемента кода" in HTML


def test_раздел_неполных():
    assert "incompleteNodes" in HTML
    assert "Неполные" in HTML
    assert 'data-layer="incomplete"' in HTML or '"incomplete","неполные"' in HTML


def test_плановые_узлы_и_зум_областей():
    assert "planned" in HTML
    assert "chip plan" in HTML
    assert "updateCreateZoneScales" in HTML
    assert "revealElement" in HTML



