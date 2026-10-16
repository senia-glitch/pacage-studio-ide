"""Что проверяется: чтение и запись config.toml под стек.

Описание словами: дефолт — пять слоёв стека, доменов нет, несовпавшие файлы
уходят в домен app; конфиг пишется в TOML и читается обратно без потерь;
старый формат (path вместо paths) мигрирует, мусор чинится дефолтами.
"""

from studio import config as config_mod


def test_дефолт_это_пять_слоёв_стека():
    """Слои infrastructure/logic/representation/schema/config с ожидаемыми путями."""
    cfg = config_mod.default_config()["studio"]
    assert [layer["name"] for layer in cfg["layers"]] == [
        "infrastructure", "logic", "representation", "schema", "config"]
    by = {layer["name"]: layer for layer in cfg["layers"]}
    assert "models.py" in by["infrastructure"]["paths"]
    assert "scenarios" in by["logic"]["paths"]
    assert "app/api/v1/routes" in by["representation"]["paths"]
    assert by["schema"]["paths"] == ["app/schemas"]
    assert cfg["domains"] == []
    assert cfg["default_domain"] == "app"
    assert cfg["markers"]["logic"]["decorator"] == "register_scenario"
    assert cfg["markers"]["representation"]["module_attr"] == "router"
    assert ["representation", "logic"] in cfg["rules"]["allowed"]
    assert ["logic", "infrastructure"] in cfg["rules"]["allowed"]


def test_сохранить_и_прочитать_без_потерь(tmp_path):
    """Записал конфиг и прочитал: слои с paths, маркеры и правила на месте."""
    cfg = config_mod.default_config()
    p = tmp_path / "config.toml"
    p.write_text(config_mod.dumps(cfg), encoding="utf-8")
    text = p.read_text(encoding="utf-8")
    assert "[[studio.domains]]" not in text, "пустые domains не пишем"
    assert "[studio.markers.logic]" in text
    back = config_mod.load(p)
    assert back["studio"]["markers"]["representation"]["module_attr"] == "router"
    assert back["studio"]["layers"][0]["paths"] == ["infrastructure", "models.py", "run_infrastructure.py", "alembic"]
    assert back["studio"]["save"]["diff_mode"] == "on_danger"


def test_старый_формат_path_мигрирует_в_paths():
    """Конфиг от прошлой версии (path вместо paths): читается, path становится paths."""
    cfg = config_mod.loads('[[studio.layers]]\nname = "logic"\npath = "logic"\norder = 1\n')
    logic = next(layer for layer in cfg["studio"]["layers"] if layer["name"] == "logic")
    assert logic["paths"] == ["logic"]
    assert cfg["studio"]["default_domain"] == "app"
    assert cfg["studio"]["markers"]["logic"]["decorator"] == "register_scenario"


def test_битый_конфиг_чинится_дефолтами():
    """Мусор в TOML: пакет не падает, недостающее добирается из дефолта."""
    cfg = config_mod.loads("[studio]\nproject_root = \".\"\n")
    assert [layer["name"] for layer in cfg["studio"]["layers"]][0] == "infrastructure"
    assert cfg["studio"]["domains"] == []
    assert cfg["studio"]["save"]["show_diff"] is False
