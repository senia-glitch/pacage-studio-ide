import json

import pytest
from fake_llm import agents_with

from studio.pipeline import Pipeline, PipelineError
from studio.pipeline import llm as llm_mod
from studio.pipeline.llm import ClaudeAgent, OpenCodeAgent, Reply, extract_json


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Вот ответ: {"a": [1, 2]} — готово') == {"a": [1, 2]}
    assert extract_json("[1, 2]") == [1, 2]
    with pytest.raises(PipelineError) as err:
        extract_json("нет json")
    assert err.value.code == "bad_reply"


def test_ask_json_retries_once_and_logs_without_texts(tmp_path):
    registry, agent = agents_with([("исправ", {"ok": True})])
    pl = Pipeline(tmp_path, registry)
    answers = iter(["не json", '{"ok": true}'])
    agent.run = lambda *a, **k: Reply(text=next(answers))
    assert pl.llm.ask_json("analyst", "s", "подсказка") == {"ok": True}
    lines = (tmp_path / ".studio" / "agents.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record["role"] == "analyst" and "подсказка" not in lines[0] and record["ok"] is True


def test_ask_raises_agent_error(tmp_path):
    registry, agent = agents_with([])
    pl = Pipeline(tmp_path, registry)
    with pytest.raises(PipelineError) as err:
        pl.llm.ask("analyst", "s", "нет правила")
    assert err.value.code == "agent_failed"


def run_with(monkeypatch, agent_cls, out, err="", code=0):
    seen = {}

    def fake_execute(argv, cwd, stdin_text, env=None, timeout=0, fatal=None):
        seen.update(argv=argv, cwd=cwd, stdin=stdin_text, env=env)
        return out, err, code

    agent = agent_cls()
    monkeypatch.setattr(llm_mod, "execute", fake_execute)
    monkeypatch.setattr(agent_cls, "found", lambda self: "C:/bin/tool.exe")
    return agent, seen


def test_claude_agent_has_no_tools_and_reads_json_result(tmp_path, monkeypatch):
    out = json.dumps({"result": '{"x": 1}', "total_cost_usd": 0.02, "session_id": "s"})
    agent, seen = run_with(monkeypatch, ClaudeAgent, out)
    reply = agent.run("система", "задание", workdir=tmp_path, model="opus")
    assert reply.text == '{"x": 1}' and reply.cost == 0.02 and not reply.error
    argv = seen["argv"]
    assert argv[argv.index("--tools") + 1] == ""  # ни файлов, ни терминала, ни поиска
    assert "--mcp-config" not in argv and "--allowedTools" not in argv
    assert argv[argv.index("--model") + 1] == "opus"
    assert seen["stdin"] == "задание"
    assert (tmp_path / "system.md").read_text(encoding="utf-8") == "система"


def test_claude_agent_opens_web_only_when_asked(tmp_path, monkeypatch):
    agent, seen = run_with(monkeypatch, ClaudeAgent, json.dumps({"result": "ок"}))
    agent.run("s", "p", workdir=tmp_path, web=True)
    argv = seen["argv"]
    assert argv[argv.index("--tools") + 1] == "WebSearch,WebFetch"
    assert "--allowedTools" in argv


def test_claude_agent_errors(tmp_path, monkeypatch):
    agent, _ = run_with(
        monkeypatch, ClaudeAgent, json.dumps({"is_error": True, "result": "лимит"}), code=1
    )
    assert agent.run("s", "p", workdir=tmp_path).error == "лимит"
    agent, _ = run_with(monkeypatch, ClaudeAgent, "", err="\x1b[31mсбой\x1b[0m", code=2)
    assert agent.run("s", "p", workdir=tmp_path).error == "сбой"
    monkeypatch.setattr(ClaudeAgent, "found", lambda self: "")
    assert "не найдена" in ClaudeAgent().run("s", "p", workdir=tmp_path).error


def test_opencode_agent_denies_tools_and_collects_text(tmp_path, monkeypatch):
    lines = [
        {"type": "text", "part": {"text": '{"a":'}},
        {"type": "text", "part": {"text": " 1}"}},
    ]
    out = "\n".join(json.dumps(line) for line in lines)
    agent, seen = run_with(monkeypatch, OpenCodeAgent, out)
    reply = agent.run("система", "задание", workdir=tmp_path, model="opencode-go/x")
    assert reply.text == '{"a":\n 1}' and not reply.error
    config = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    assert config["permission"]["edit"] == "deny" and config["permission"]["bash"] == "deny"
    assert config["tools"]["bash"] is False and config["tools"]["websearch"] is False
    assert "задание" in (tmp_path / "system.md").read_text(encoding="utf-8")
    assert seen["argv"][seen["argv"].index("--model") + 1] == "opencode-go/x"
    assert seen["env"]["OPENCODE_CONFIG"].endswith("opencode.json")


def test_opencode_agent_errors_and_web(tmp_path, monkeypatch):
    err_line = json.dumps({"type": "error", "error": {"data": {"message": "нет денег"}}})
    agent, _ = run_with(monkeypatch, OpenCodeAgent, err_line, code=1)
    assert agent.run("s", "p", workdir=tmp_path).error == "нет денег"
    agent, _ = run_with(monkeypatch, OpenCodeAgent, "", err="упало", code=3)
    assert agent.run("s", "p", workdir=tmp_path, web=True).error == "упало"
    config = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    assert config["tools"]["websearch"] is True and config["permission"]["webfetch"] == "allow"
    monkeypatch.setattr(OpenCodeAgent, "found", lambda self: "")
    assert "не найдена" in OpenCodeAgent().run("s", "p", workdir=tmp_path).error


def test_opencode_model_list_parsing(monkeypatch):
    agent = OpenCodeAgent()
    monkeypatch.setattr(OpenCodeAgent, "found", lambda self: "x")
    verbose = (
        'opencode-go/glm\n{"name": "GLM", "status": "active", "capabilities": {"toolcall": true}}\n'
        'opencode-go/old\n{"name": "Old", "status": "deprecated"}\n'
        'opencode-go/dumb\n{"name": "Dumb", "capabilities": {"toolcall": false}}\n'
    )
    monkeypatch.setattr(OpenCodeAgent, "_list", staticmethod(lambda exe, *a: verbose))
    assert [m["id"] for m in agent.models()] == ["opencode-go/glm"]
    agent2 = OpenCodeAgent()
    monkeypatch.setattr(
        OpenCodeAgent,
        "_list",
        staticmethod(lambda exe, *a: "" if a else "opencode-go/a\nother/b\n"),
    )
    assert [m["id"] for m in agent2.models()] == ["opencode-go/a"]
    monkeypatch.setattr(OpenCodeAgent, "found", lambda self: "")
    assert OpenCodeAgent().models() == []
    assert ClaudeAgent().models()[1]["id"] == "opus"


def test_agents_registry_describe(monkeypatch):
    monkeypatch.setattr(ClaudeAgent, "found", lambda self: "C:/claude.exe")
    monkeypatch.setattr(OpenCodeAgent, "found", lambda self: "")
    info = {a["id"]: a for a in llm_mod.Agents().describe()}
    assert info["claude"]["found"] and not info["opencode"]["found"]
    assert info["claude"]["name"] == "Claude Code Pro"
