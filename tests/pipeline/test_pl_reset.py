"""Снести проект: что удаляется и что остаётся, подтверждение, защита от беды."""

from pathlib import Path

import pytest
from fake_llm import agents_with, full_rules
from pl_helpers import idea

from studio.pipeline import Pipeline, PipelineError, reset


def project(tmp_path: Path) -> Path:
    root = tmp_path / "blog"
    root.mkdir()
    pl = Pipeline(root, agents_with(full_rules())[0])
    idea(pl.bank)
    pl.settings.set_mode("autopilot")
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (root / "frontend").mkdir()
    (root / "frontend" / "node_modules").mkdir()
    (root / "models.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "lib.py").write_text("keep\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref\n", encoding="utf-8")
    (root / ".studio" / "servers.json").write_text("{}", encoding="utf-8")
    (root / ".studio" / "studio.log").write_text("log\n", encoding="utf-8")
    return root


def test_plan_lists_what_goes_and_what_stays(tmp_path):
    root = project(tmp_path)
    plan = reset.plan(root)
    assert plan["name"] == "blog"
    assert {"app/", "frontend/", "models.py", ".studio/bank/"} <= set(plan["remove"])
    assert {".venv/", ".git/", "node_modules/", ".studio/pipeline.json", ".studio/servers.json"} <= set(plan["keep"])
    with_settings = reset.plan(root, settings=True)
    assert ".studio/pipeline.json" in with_settings["remove"]


def test_wipe_needs_the_folder_name_and_changes_nothing_without_it(tmp_path):
    root = project(tmp_path)
    for wrong in ("", "Blog", "blog2", None):
        with pytest.raises(PipelineError) as err:
            reset.wipe(root, wrong)
        assert err.value.code == "confirm_mismatch"
    assert (root / "app" / "main.py").exists() and (root / ".studio" / "bank").exists()


def test_wipe_removes_the_project_but_not_the_environment(tmp_path):
    root = project(tmp_path)
    done = reset.wipe(root, "blog")
    assert done["failed"] == [] and done["removed"] >= 4
    assert not (root / "app").exists() and not (root / "frontend").exists() and not (root / "models.py").exists()
    assert not (root / ".studio" / "bank").exists()
    assert (root / ".venv" / "lib.py").exists() and (root / "node_modules").is_dir() and (root / ".git" / "HEAD").exists()
    assert (root / ".studio" / "pipeline.json").exists() and (root / ".studio" / "studio.log").exists()
    assert Pipeline(root, agents_with([])[0]).settings.mode == "autopilot"  # настройки пережили снос
    assert not Pipeline(root, agents_with([])[0]).bank.records()  # банк пуст: чистый лист


def test_wipe_can_reset_the_settings_too(tmp_path):
    root = project(tmp_path)
    reset.wipe(root, "blog", settings=True)
    assert not (root / ".studio" / "pipeline.json").exists()
    assert Pipeline(root, agents_with([])[0]).settings.mode == "gates"


def test_guards_refuse_unstarted_home_and_studio_folders(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PipelineError) as err:
        reset.plan(empty)
    assert err.value.code == "not_started"
    repo = tmp_path / "pacage-studio-ide"
    (repo / ".studio").mkdir(parents=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "pacage-studio"\n', encoding="utf-8")
    with pytest.raises(PipelineError) as err:
        reset.wipe(repo, "pacage-studio-ide")
    assert err.value.code == "studio_repo"
    home = tmp_path / "home"
    (home / ".studio").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    with pytest.raises(PipelineError) as err:
        reset.wipe(home, "home")
    assert err.value.code == "unsafe_root"
    assert (home / ".studio").is_dir()


def test_a_locked_file_is_reported_and_does_not_stop_the_rest(tmp_path, monkeypatch):
    root = project(tmp_path)
    real = reset._delete

    def flaky(path, failed, base):
        if path.name == "models.py":
            failed.append("models.py: занято другой программой")
            return
        real(path, failed, base)

    monkeypatch.setattr(reset, "_delete", flaky)
    done = reset.wipe(root, "blog")
    assert done["failed"] == ["models.py: занято другой программой"]
    assert (root / "models.py").exists() and not (root / "app").exists()
