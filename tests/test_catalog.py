"""Каталог моделей: бесплатные и подписочные против платных по токенам, уровни усилий моделей."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_runner import _runner

from studio.agent import adapters
from studio.server import runner


def _model(provider: str, name: str, cost_in: float, variants: dict | None) -> str:
    body = {
        "id": name, "providerID": provider, "name": name.title(),
        "cost": {"input": cost_in, "output": cost_in}, "capabilities": {"reasoning": True},
    }
    if variants is not None:
        body["variants"] = variants
    return f"{provider}/{name}\n{json.dumps(body, indent=2)}\n"


VERBOSE = (
    "\x1b[0m" + _model("opencode-go", "kimi-k3", 0.5, {"max": {}})
    + _model("opencode-go", "deepseek", 0.2, {"low": {}, "high": {}, "max": {}})
    + _model("opencode", "big-pickle", 0, {})
    + _model("opencode", "claude-opus", 5, {"low": {}, "high": {}})
    + _model("openai", "gpt-5", 1, {"low": {}, "medium": {}})
    + _model("anthropic", "claude-x", 3, None)
)
CREDENTIALS = [
    {"name": "OpenCode", "type": "api"},
    {"name": "OpenCode Go", "type": "api"},
    {"name": "OpenAI", "type": "oauth"},
    {"name": "Anthropic", "type": "api"},
]


def test_вывод_models_verbose_разбирается_в_объекты() -> None:
    models = adapters.parse_models_verbose(VERBOSE)
    assert [m["id"] for m in models] == ["kimi-k3", "deepseek", "big-pickle", "claude-opus", "gpt-5", "claude-x"]
    assert adapters.parse_models_verbose("") == [] and adapters.parse_models_verbose("{не json") == []


def test_тариф_бесплатная_подписка_или_оплата_по_токенам() -> None:
    cost = {"input": 1, "output": 2}
    free = {"input": 0, "output": 0}
    assert adapters.classify("opencode", free, CREDENTIALS) == "free"
    assert adapters.classify("opencode-go", cost, CREDENTIALS) == "subscription"
    assert adapters.classify("openai", cost, CREDENTIALS) == "subscription"  # вход через аккаунт
    assert adapters.classify("opencode", cost, CREDENTIALS) == "paid"  # Zen: баланс, кредиты
    assert adapters.classify("anthropic", cost, CREDENTIALS) == "paid"  # ключ API, а не подписка
    assert adapters.classify("github-copilot", cost, [{"name": "GitHub Copilot", "type": "oauth"}]) == "subscription"
    assert adapters.classify("x", None, []) == "paid"


def test_каталог_opencode_с_уровнями_и_порядком(monkeypatch) -> None:
    adapter = adapters.get("opencode")
    monkeypatch.setattr(adapters, "_run", lambda argv, timeout: VERBOSE if "--verbose" in argv else "")
    monkeypatch.setattr(adapters.OpenCodeAdapter, "credentials", lambda self, exe: CREDENTIALS)
    items = adapter.catalog("opencode")
    by_id = {item["id"]: item for item in items}
    assert by_id["opencode-go/deepseek"]["variants"] == ["low", "high", "max"]
    assert by_id["opencode-go/kimi-k3"]["variants"] == ["max"]
    assert by_id["opencode/big-pickle"]["variants"] == [] and by_id["opencode/big-pickle"]["tier"] == "free"
    assert by_id["opencode/claude-opus"]["tier"] == "paid"
    assert by_id["anthropic/claude-x"]["variants"] == []  # у модели нет уровней
    tiers = [item["tier"] for item in items]
    assert tiers == sorted(tiers, key={"subscription": 0, "free": 1, "paid": 2}.get)  # платные в конце


def test_каталог_opencode_без_verbose_падает_обратно_на_список_имён(monkeypatch) -> None:
    adapter = adapters.get("opencode")
    monkeypatch.setattr(adapters, "_run", lambda argv, timeout: "a/b\nc/d\n" if argv[-1] == "models" else "")
    items = adapter.catalog("opencode")
    assert [item["id"] for item in items] == ["a/b", "c/d"] and items[0]["variants"] == []


def test_каталог_claude_fable_платный_остальные_по_подписке() -> None:
    items = adapters.get("claude").catalog("claude")
    tiers = {item["id"]: item["tier"] for item in items}
    assert tiers == {"opus": "subscription", "sonnet": "subscription", "haiku": "subscription", "fable": "paid"}
    assert all(item["variants"] == list(adapters.CLAUDE_EFFORTS) for item in items)


def test_уровень_усилий_claude_и_opencode_проверяются_по_агенту() -> None:
    assert adapters.clean_variant("xhigh", "claude") == "xhigh" and adapters.clean_variant("", "claude") == ""
    with pytest.raises(ValueError):
        adapters.clean_variant("turbo", "claude")
    assert adapters.clean_variant("thinking", "opencode") == "thinking"
    with pytest.raises(ValueError):
        adapters.clean_variant("a b", "opencode")


def test_claude_получает_effort_а_opencode_variant() -> None:
    argv = adapters.get("claude").extend(["claude", "-p", "q"], Path("p"), "", "opus", "high")
    assert argv[argv.index("--effort") + 1] == "high" and argv[argv.index("--model") + 1] == "opus"
    assert "--effort" not in adapters.get("claude").extend(["claude", "-p", "q"], Path("p"), "", "", "")
    own = adapters.get("claude").extend(["claude", "--effort", "low"], Path("p"), "", "", "max")
    assert own.count("--effort") == 1  # явно заданный в команде не трогаем
    oc = adapters.get("opencode").extend(["opencode", "run", "q"], Path("p"), "", "a/b", "max")
    assert oc[oc.index("--variant") + 1] == "max"


def test_настройка_уровня_у_роли_проверяется_для_её_агента(tmp_path: Path) -> None:
    from studio.metadata import MetadataError

    run, _ = _runner(tmp_path)
    saved = run.save_config({"roles": {"plan": {"model": "opus", "variant": "xhigh"}}})
    assert saved["roles"]["plan"] == {"agent": "claude", "model": "opus", "variant": "xhigh"}
    with pytest.raises(MetadataError):
        run.save_config({"roles": {"plan": {"variant": "turbo"}}})
    run.save_config({"roles": {"implement": {"agent": "opencode", "variant": "thinking"}}})
    assert run.config()["roles"]["implement"]["variant"] == "thinking"
    # смена агента роли сбрасывает уровень усилий вместе с моделью
    again = run.save_config({"roles": {"plan": {"agent": "opencode"}}})
    assert again["roles"]["plan"]["variant"] == ""


def test_старый_уровень_claude_вне_набора_читается_как_пустой(tmp_path: Path) -> None:
    run, _ = _runner(tmp_path)
    run.config_path.parent.mkdir(parents=True)
    run.config_path.write_text(json.dumps({"roles": {"plan": {"agent": "claude", "variant": "turbo"}}}),
                               encoding="utf-8")
    assert run.config()["roles"]["plan"]["variant"] == ""


def test_каталог_моделей_кэшируется_и_обновляется(tmp_path: Path, monkeypatch) -> None:
    run, _ = _runner(tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/bin/{name}")
    calls: list = []

    def fake(self, exe):
        calls.append(exe)
        return [{"id": "p/m", "name": "M", "provider": "p", "tier": "subscription", "variants": ["high"]}]

    monkeypatch.setattr(adapters.OpenCodeAdapter, "catalog", fake)
    first = run.models("opencode")
    assert first["models"] == ["p/m"] and first["items"][0]["variants"] == ["high"]
    run.models("opencode")
    assert len(calls) == 1  # повторный запрос взят из кэша
    run.models("opencode", refresh=True)
    assert len(calls) == 2
    assert run.models("claude")["items"][3]["id"] == "fable"
