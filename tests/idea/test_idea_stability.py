"""Стабильность: один запуск работы за раз, журнал агентов, предупреждения, один снимок графа."""

import json
import threading

from idea_helpers import make, wait  # noqa: F401
from test_idea_check import build_state

from studio.idea import check
from studio.idea.errors import EngineError


def test_only_one_run_starts_under_parallel_requests(make):
    gate = threading.Event()

    def script(model, note, call, request):
        gate.wait(5)

    p = make(script)
    p.act("set_sphere", {"title": "парикмахерская"})
    started, busy = [], []

    def go():
        try:
            p.start("competitors")
            started.append(1)
        except EngineError as error:
            busy.append(error.code)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    gate.set()
    wait(p)
    assert len(started) == 1 and busy == ["busy"] * 7


def test_agent_journal_has_metadata_only(make):
    p = make(lambda *a: None)
    p.act("set_sphere", {"title": "секретная идея"})
    p.start("competitors")
    wait(p)
    lines = [json.loads(x) for x in (p.dir / "agents.log").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3 and {x["model"] for x in lines} == {"r1", "r2", "r3"}
    assert all(set(x) == {"time", "turn", "agent", "model", "seconds", "cost", "error"} for x in lines)
    assert "секретная идея" not in (p.dir / "agents.log").read_text(encoding="utf-8")


def test_warnings_empty_project_has_none(make):
    assert make(lambda *a: None).warnings() == []


def test_warnings_no_components(make):
    p = make(lambda *a: None)
    p.act("set_sphere", {"title": "x"})
    p.act("add_role", {"title": "Клиент", "body": "b"})
    p.act("add_scenario", {"title": "Запись", "body": "b", "roles": ["Клиент"]})
    for c in p.store.snapshot()["cards"]:
        if c["kind"] in ("роль", "сценарий"):
            p.act("accept", {"id": c["id"]})
    assert any("Нет компонентов" in w for w in p.warnings())


def test_warnings_untyped_role(make):
    p = make(lambda *a: None)
    p.act("set_sphere", {"title": "x"})
    rid = p._add_unique("роль", "Старая", "b", p.sphere()["id"])      # как в старых проектах: без attrs.type
    p.act("accept", {"id": rid})
    assert any("Роли без типа" in w and "Старая" in w for w in p.warnings())


def test_frozen_snapshot_is_single_and_thread_local(make, monkeypatch):
    p = make(lambda *a: None)
    build_state(p)
    calls = []
    real = p.store.snapshot
    monkeypatch.setattr(p.store, "snapshot", lambda: (calls.append(1), real())[1])
    check.issues(p)
    assert len(calls) == 1                                 # весь расчёт по одному снимку
    calls.clear()
    check.graph_text(p)
    assert len(calls) == 1
    with p.frozen():
        before = len(p.cards("роль"))
        seen = []
        t = threading.Thread(target=lambda: seen.append(len(p.cards("роль"))))
        p.act("add_role", {"title": "Новая", "body": "b"})
        t.start()
        t.join()
        assert len(p.cards("роль")) == before              # свой поток видит снимок
        assert seen == [before + 1]                        # чужой поток — актуальный граф
    assert len(p.cards("роль")) == before + 1
