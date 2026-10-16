"""Что проверяется: чтение и запись config.toml — домены, слои, правила.

Описание словами: конфиг по умолчанию содержит нужные домены и слои,
его можно сохранить в текст и прочитать обратно без потерь,
а кривой конфиг не роняет пакет, а чинится дефолтами.
"""

from studio import config as config_mod


def test_дефолт_содержит_домены_слои_и_правила():
    """Свежий конфиг: 3 домена, 5 слоёв, разрешённые направления, save выключен."""
    cfg = config_mod.default_config()["studio"]
    assert [d["name"] for d in cfg["domains"]] == ["client", "order", "shared"]
    assert [layer["name"] for layer in cfg["layers"]] == ["domain", "logic", "io", "representation", "validation"]
    assert ["representation", "logic"] in cfg["rules"]["allowed"]
    assert ["logic", "domain"] in cfg["rules"]["allowed"]
    assert cfg["save"] == {"show_diff": False, "diff_mode": "on_danger"}


def test_сохранить_и_прочитать_без_потерь(tmp_path):
    """Записал конфиг в файл и прочитал — домены, слои и правила на месте."""
    cfg = config_mod.default_config()
    cfg["studio"]["domains"].append({"name": "billing", "path": "billing"})
    p = tmp_path / "config.toml"
    p.write_text(config_mod.dumps(cfg), encoding="utf-8")
    back = config_mod.load(p)
    assert "billing" in [d["name"] for d in back["studio"]["domains"]]
    assert back["studio"]["save"]["diff_mode"] == "on_danger"


def test_битый_конфиг_чинится_дефолтами():
    """Мусор в TOML: пакет не падает, недостающее добирается из дефолта."""
    cfg = config_mod.loads("[studio]\nproject_root = \".\"\n")
    assert cfg["studio"]["domains"], "домены должны подставиться из дефолта"
    assert cfg["studio"]["save"]["show_diff"] is False
