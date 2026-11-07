"""Git-чекпоинты агента и настройки студии: на временном репозитории, без сети."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from studio.metadata import MetadataError
from studio.server import gitops
from studio.server.settings import Settings

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="нужен git")


def _run(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _run(tmp_path, "init", "-q")
    _run(tmp_path, "config", "user.email", "t@example.com")
    _run(tmp_path, "config", "user.name", "Tester")
    _run(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    _run(tmp_path, "add", "-A")
    _run(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_вне_репозитория_откат_недоступен(tmp_path: Path) -> None:
    assert gitops.status(tmp_path) == {"repo": False}
    result = gitops.begin(tmp_path, 1)
    assert result["ok"] is False and "git" in result["reason"]


def test_чекпоинт_и_откат(repo: Path) -> None:
    started = gitops.begin(repo, 3)
    assert started["ok"] is True
    assert gitops.branch(repo) == "studio/c3"
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    (repo / "new.txt").write_text("n\n", encoding="utf-8")
    (repo / ".studio").mkdir(exist_ok=True)
    (repo / ".studio" / "x.json").write_text("{}", encoding="utf-8")  # состояние студии не коммитим
    done = gitops.checkpoint(repo, "studio: реализация (цикл 3)")
    assert done["ok"] and done["committed"] is True
    shown = gitops.status(repo)
    assert shown["clean"] is True and shown["can_rollback"] is True
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.split()
    assert "new.txt" in tracked and ".studio/x.json" not in tracked
    back = gitops.rollback(repo)
    assert back["ok"] is True
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\n"
    assert not (repo / "new.txt").exists() and (repo / ".studio" / "x.json").exists()


def test_несохранённые_изменения_отключают_чекпоинт(repo: Path) -> None:
    (repo / "a.txt").write_text("мои правки\n", encoding="utf-8")
    result = gitops.begin(repo, 1)
    assert result["ok"] is False and "несохранённые" in result["reason"]
    assert gitops.branch(repo) != "studio/c1"  # чужую работу в ветку агента не смешиваем
    assert gitops.checkpoint(repo, "msg")["committed"] is False


def test_откат_запрещён_вне_ветки_студии(repo: Path) -> None:
    gitops.begin(repo, 1)
    _run(repo, "checkout", "-q", "-")  # вернулись в основную ветку
    refused = gitops.rollback(repo)
    assert refused["ok"] is False and "studio" in refused["reason"]


def test_пуш_без_remote_сообщает_ошибку_но_коммит_остаётся(repo: Path) -> None:
    gitops.begin(repo, 2)
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    result = gitops.checkpoint(repo, "msg", push=True, remote="origin")
    assert result["committed"] is True and "пуш не удался" in result["reason"]


def test_настройки_по_умолчанию_и_проверка(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    assert settings.get() == {
        "ui": {"mode": "agent"},
        "git": {"checkpoints": True, "push": False, "remote": "origin"},
    }
    saved = settings.save({"ui": {"mode": "manual"}, "git": {"push": True, "remote": "gh"}})
    assert saved["ui"]["mode"] == "manual" and saved["git"] == {
        "checkpoints": True, "push": True, "remote": "gh"
    }
    assert Settings(tmp_path).get() == saved  # переживает перезапуск
    for bad in (
        {"ui": {"mode": "x"}},
        {"git": {"push": "да"}},
        {"git": {"remote": "a b"}},
        {"git": {"remote": "x;rm"}},
        {"ui": []},
        [],
    ):
        with pytest.raises(MetadataError):
            settings.save(bad)
    assert settings.get() == saved
    settings.path.write_text("не json", encoding="utf-8")
    assert settings.get()["ui"]["mode"] == "agent"
