"""Что проверяется: чтение и запись config.toml под стек.

Описание словами: дефолт — три домена-пакета, шесть слоёв-типов файлов,
бесхозные файлы уходят в shared; конфиг пишется в TOML и читается обратно
без потерь; старый формат (path вместо paths) мигрирует, мусор чинится дефолтами.
"""

from studio import config as config_mod


def test_дефолт_это_три_пакета_и_шесть_слоёв():
    """Домены infra/core/presentation, слои models/scenarios/interfaces/utils/routes/schemas."""
    cfg = config_mod.default_config()["studio"]
    assert [(d["name"], d["paths"]) for d in cfg["domains"]] == [
        ("infra", ["models.py"]),
        ("core", ["scenarios", "interfaces", "utils"]),
        ("presentation", ["routes", "schemas"])]
    assert [layer["name"] for layer in cfg["layers"]] == [
        "models", "scenarios", "interfaces", "utils", "routes", "schemas"]
    assert cfg["default_domain"] == "shared"
    assert cfg["markers"]["scenarios"]["decorator"] == "register_scenario"
    assert cfg["markers"]["routes"]["module_attr"] == "router"
    assert ["routes", "scenarios"] in cfg["rules"]["allowed"]
    assert ["scenarios", "utils"] in cfg["rules"]["allowed"]
    assert ["utils", "schemas"] in cfg["rules"]["allowed"]


def test_сохранить_и_прочитать_без_потерь(tmp_path):
    """Записал конфиг и прочитал — домены с paths, маркеры и правила на месте."""
    cfg = config_mod.default_config()
    p = tmp_path / "config.toml"
    p.write_text(config_mod.dumps(cfg), encoding="utf-8")
    text = p.read_text(encoding="utf-8")
    assert "[studio.markers.routes]" in text
    assert "default_domain" in text
    back = config_mod.load(p)
    assert back["studio"]["domains"][0] == {"name": "infra", "paths": ["models.py"]}
    assert back["studio"]["markers"]["scenarios"]["base_class"] == "BaseScenario"
    assert back["studio"]["save"]["diff_mode"] == "on_danger"


def test_старый_формат_path_мигрирует_в_paths():
    """Конфиг прошлой версии (path строкой): читается, path становится paths."""
    cfg = config_mod.loads('[[studio.domains]]\nname = "client"\npath = "client"\n'
                           '[[studio.layers]]\nname = "logic"\npath = "logic"\norder = 1\n')
    assert cfg["studio"]["domains"] == [{"name": "client", "paths": ["client"]}]
    logic = next(layer for layer in cfg["studio"]["layers"] if layer["name"] == "logic")
    assert logic["paths"] == ["logic"]


def test_битый_конфиг_чинится_дефолтами():
    """Мусор в TOML: пакет не падает, недостающее добирается из дефолта."""
    cfg = config_mod.loads("[studio]\nproject_root = \".\"\n")
    assert [layer["name"] for layer in cfg["studio"]["layers"]][0] == "models"
    assert cfg["studio"]["default_domain"] == "shared"
    assert cfg["studio"]["save"]["show_diff"] is False
