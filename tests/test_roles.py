"""Два независимых исполнителя: агент для плана (идеи) и агент для реализации, у каждого своя модель."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_opencode import EVENTS, MODEL, _opencode_popen
from test_runner import _runner

from studio.agent import adapters
from studio.metadata import MetadataError
from studio.server import runner
from studio.server.runner import NO_MODEL

CLAUDE_OUT = json.dumps({"session_id": "claude-1", "result": "ok"})


def _mixed(tmp_path: Path):
    """План строит Claude Code (opus), код пишет OpenCode (модель из подписки)."""
    run, wf = _runner(tmp_path)
    run.save_config({"roles": {
        "plan": {"agent": "claude", "model": "opus"},
        "implement": {"agent": "opencode", "model": MODEL},
    }, "enabled": True})
    run._cancel = False
    run.settings.save({"git": {"checkpoints": False}})  # здесь проверяем запуск, а не git
    wf.start("plan")
    return run, wf


# ------------------------------------------------------------------ настройка


def test_роли_настраиваются_независимо(tmp_path: Path) -> None:
    run, _ = _runner(tmp_path)
    saved = run.save_config({"roles": {"implement": {"agent": "opencode", "model": MODEL}}})
    assert saved["roles"]["plan"] == {"agent": "claude", "model": "", "variant": ""}
    assert saved["roles"]["implement"]["agent"] == "opencode"
    assert saved["roles"]["implement"]["model"] == MODEL
    saved = run.save_config({"roles": {"plan": {"model": "sonnet"}}})
    assert saved["roles"]["plan"]["model"] == "sonnet"
    assert saved["roles"]["implement"]["model"] == MODEL  # соседняя роль не тронута
    assert run.config() == saved
    assert run.used_agents() == ["claude", "opencode"]


def test_смена_агента_одной_роли_сбрасывает_только_её_модель(tmp_path: Path) -> None:
    run, _ = _runner(tmp_path)
    run.save_config({"roles": {"plan": {"model": "opus"},
                               "implement": {"agent": "opencode", "model": MODEL}}, "enabled": True})
    saved = run.save_config({"roles": {"implement": {"agent": "claude"}}})
    assert saved["roles"]["implement"] == {"agent": "claude", "model": "", "variant": ""}
    assert saved["roles"]["plan"]["model"] == "opus"
    assert saved["enabled"] is False  # смена агента — подключить заново


def test_плохие_роли_и_модели_отклоняются(tmp_path: Path) -> None:
    run, _ = _runner(tmp_path)
    bad_values = (
        {"roles": {"review": {"agent": "claude"}}},
        {"roles": "plan"},
        {"roles": {"plan": "claude"}},
        {"roles": {"plan": {"agent": "gemini"}}},
        {"roles": {"implement": {"agent": "opencode", "model": "без-слеша"}}},
        {"roles": {"plan": {"model": "a b"}}},
        {"commands": {"gemini": ["x", "{prompt}"]}},
        {"commands": {"claude": ["x"]}},
    )
    for bad in bad_values:
        with pytest.raises(MetadataError) as error:
            run.save_config(bad)
        assert error.value.code == "invalid_input", bad


def test_команды_хранятся_по_агентам(tmp_path: Path) -> None:
    run, _ = _runner(tmp_path)
    saved = run.save_config({"commands": {"opencode": "oc\nrun\n{prompt}"}})
    assert saved["commands"]["opencode"] == ["oc", "run", "{prompt}"]
    assert saved["commands"]["claude"] == runner.DEFAULT_COMMAND  # чужая команда не изменилась


def test_модель_claude_проверяется_иначе_чем_у_opencode() -> None:
    assert adapters.clean_model("opus", "claude") == "opus"
    assert adapters.clean_model("claude-opus-4-5", "claude") == "claude-opus-4-5"
    with pytest.raises(ValueError):
        adapters.clean_model("opus", "opencode")  # у OpenCode нужен провайдер/модель
    with pytest.raises(ValueError):
        adapters.clean_model("op us", "claude")


def test_claude_получает_модель_только_если_она_выбрана() -> None:
    adapter = adapters.get("claude")
    argv = adapter.extend(["claude", "-p", "q"], Path("p"), "", "opus", "")
    assert argv[argv.index("--model") + 1] == "opus"
    assert "--model" not in adapter.extend(["claude", "-p", "q"], Path("p"), "", "", "")
    own = adapter.extend(["claude", "--model", "haiku"], Path("p"), "", "opus", "")
    assert own.count("--model") == 1  # явно заданную в команде не трогаем
    assert adapter.models("claude") == adapters.CLAUDE_MODELS


# ------------------------------------------------------------------ запуск по ролям


def test_каждый_этап_идёт_к_агенту_своей_роли(tmp_path: Path, monkeypatch) -> None:
    run, wf = _mixed(tmp_path)
    seen: list = []
    _opencode_popen(monkeypatch, [(0, CLAUDE_OUT), (0, EVENTS)], seen)
    run._run("ideas")
    plan_argv = seen[0][0]
    assert plan_argv[0] == "/bin/claude" and plan_argv[plan_argv.index("--model") + 1] == "opus"
    assert "OPENCODE_CONFIG" not in seen[0][1]["env"]
    run._run("implement")
    impl_argv = seen[1][0]
    assert impl_argv[0] == "/bin/opencode" and impl_argv[1] == "run"
    assert impl_argv[impl_argv.index("--model") + 1] == MODEL
    assert "--resume" not in impl_argv and "--session" not in impl_argv  # чужую сессию не берём


def test_сессии_у_каждого_агента_свои(tmp_path: Path, monkeypatch) -> None:
    run, wf = _mixed(tmp_path)
    seen: list = []
    _opencode_popen(monkeypatch, [(0, CLAUDE_OUT), (0, EVENTS), (0, EVENTS), (0, CLAUDE_OUT)], seen)
    run._run("ideas")
    run._run("implement")
    assert run.session_id("claude") == "claude-1" and run.session_id("opencode") == "ses_abc"
    run._run("implement")  # доработка: тот же агент продолжает свою сессию
    again = seen[2][0]
    assert again[again.index("--session") + 1] == "ses_abc"
    run._run("ideas")
    plan_again = seen[3][0]
    assert plan_again[plan_again.index("--resume") + 1] == "claude-1"


def test_один_агент_на_обе_роли_продолжает_общую_сессию(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"enabled": True})
    run._cancel = False
    run.settings.save({"git": {"checkpoints": False}})
    wf.start("plan")
    seen: list = []
    _opencode_popen(monkeypatch, [(0, CLAUDE_OUT), (0, CLAUDE_OUT)], seen)
    run._run("ideas")
    run._run("implement")
    second = seen[1][0]
    assert second[second.index("--resume") + 1] == "claude-1"  # контекст плана сохранён


def test_потеря_сессии_забывает_только_этого_агента(tmp_path: Path, monkeypatch) -> None:
    run, _ = _mixed(tmp_path)
    run._save_session("c-old", "claude")
    run._save_session("o-old", "opencode")
    seen: list = []
    _opencode_popen(monkeypatch, [(1, "Session not found"), (0, EVENTS)], seen)
    run._run("implement")
    assert "--session" in seen[0][0] and "--session" not in seen[1][0]
    assert run.session_id("claude") == "c-old" and run.session_id("opencode") == "ses_abc"


def test_сброс_цикла_забывает_все_сессии(tmp_path: Path) -> None:
    run, _ = _mixed(tmp_path)
    run._save_session("a", "claude")
    run._save_session("b", "opencode")
    run._save_session("")
    assert run._sessions() == {}


def test_старый_файл_сессии_читается_как_claude(tmp_path: Path) -> None:
    run, _ = _runner(tmp_path)
    target = tmp_path / ".studio" / "agent-session.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"id": "legacy-1", "updated_at": "x"}), encoding="utf-8")
    assert run.session_id("claude") == "legacy-1" and run.session_id("opencode") == ""


def test_без_модели_у_роли_opencode_этап_не_стартует(tmp_path: Path, monkeypatch) -> None:
    run, wf = _runner(tmp_path)
    run.save_config({"roles": {"implement": {"agent": "opencode"}}, "enabled": True})
    run._cancel = False
    run.settings.save({"git": {"checkpoints": False}})
    wf.start("plan")
    seen: list = []
    _opencode_popen(monkeypatch, [(0, CLAUDE_OUT)], seen)
    run._run("ideas")  # у плана Claude: модель не нужна, запуск идёт
    assert len(seen) == 1
    run._run("implement")
    assert len(seen) == 1 and run.public()["message"] == NO_MODEL


# ------------------------------------------------------------------ готовность и «Начать»


def test_готовность_по_ролям_и_блок_начала(tmp_path: Path, monkeypatch) -> None:
    run, _ = _runner(tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    run.save_config({"roles": {"implement": {"agent": "opencode"}}, "enabled": True})
    ready = run.readiness()
    assert ready["roles"]["plan"] == "" and ready["roles"]["implement"] == NO_MODEL
    assert ready["ok"] is False and ready["problems"] == [f"Реализация: {NO_MODEL}"]
    assert run.start_blockers("plan") == [f"Реализация: {NO_MODEL}"]
    assert run.start_blockers("implement") == [f"Реализация: {NO_MODEL}"]
    run.save_config({"roles": {"implement": {"model": MODEL}}})
    assert run.start_blockers("plan") == []
    monkeypatch.setattr(runner.shutil, "which", lambda name: None if name == "opencode" else "/bin/x")
    assert "OpenCode не найден" in run.start_blockers("plan")[0]
    # режим «сразу реализация» план не требует: его агент может быть неготов
    run.save_config({"roles": {"plan": {"agent": "opencode"}, "implement": {"agent": "claude"}},
                     "enabled": True})
    monkeypatch.setattr(runner.shutil, "which", lambda name: None if name == "opencode" else "/bin/x")
    assert run.start_blockers("implement") == []
    assert run.start_blockers("plan") != []


def test_без_автозапуска_ничто_не_мешает_начать(tmp_path: Path, monkeypatch) -> None:
    run, _ = _runner(tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    assert run.start_blockers("plan") == []


def test_падение_mcp_блокирует_обе_роли(tmp_path: Path, monkeypatch) -> None:
    run, _ = _runner(tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    run.save_config({"enabled": True})
    run._mcp = {"ok": False, "code": "no_mcp_module", "reason": "нет mcp"}
    assert all(run.readiness()["roles"].values())
    run._mcp = {"ok": False, "code": "no_config"}  # конфиг ещё не записан — не сбой
    assert not any(run.readiness()["roles"].values())


def test_агентский_вид_содержит_обе_роли(tmp_path: Path, monkeypatch) -> None:
    run, _ = _mixed(tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    view = run.agent_view()
    assert view["roles"]["plan"]["label"] == "Claude Code" and view["roles"]["plan"]["model"] == "opus"
    assert view["roles"]["implement"]["label"] == "OpenCode"
    assert view["roles"]["implement"]["needs_model"] is True
    assert view["readiness"]["ok"] is True


def test_проверка_связи_по_ролям(tmp_path: Path, monkeypatch) -> None:
    run, _ = _mixed(tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(adapters.OpenCodeAdapter, "providers", lambda self, exe: ["OpenCode Go"])
    monkeypatch.setattr(run, "probe", lambda: {"ok": True})
    calls: list = []

    def fake_run(argv, **kwargs):
        calls.append(argv[0])
        return type("Done", (), {"stdout": "1.0\n", "stderr": "", "returncode": 0})()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    result = run.check()
    assert result["ok"] is True
    assert result["roles"]["plan"]["label"] == "Claude Code" and result["roles"]["plan"]["ok"]
    assert result["roles"]["implement"]["providers"] == ["OpenCode Go"]
    assert result["agent"] == "opencode"  # плоские поля — по роли «реализация»
    assert sorted(calls) == ["/bin/claude", "/bin/opencode"]  # каждый агент спрошен один раз
    run.save_config({"roles": {"implement": {"model": ""}}})
    broken = run.check()
    assert broken["ok"] is False and broken["error"] == f"Реализация: {NO_MODEL}"
    assert broken["roles"]["plan"]["ok"] is True
