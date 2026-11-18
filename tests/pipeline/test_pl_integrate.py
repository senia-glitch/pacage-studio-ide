"""Ничего не висит: запись без родителя агент встраивает в цепочку или отклоняет."""

from fake_llm import agents_with, drive
from pl_helpers import idea

from studio.pipeline import Pipeline, flow, integrate

KEY = "Внедри висящие записи"


def make(tmp_path, decisions=None):
    rules = [] if decisions is None else [(KEY, lambda prompt: {"decisions": decisions(prompt)})]
    registry, agent = agents_with(rules)
    return Pipeline(tmp_path, registry), agent


def hanging_criterion(pl):
    """Критерий, у которого требование отклонили (потомки не тронуты): он висит."""
    ids = idea(pl.bank)
    second = pl.bank.add("requirement", "Система должна напоминать", parents=[ids["problem"]["id"]])
    stray = pl.bank.add("criterion", "Когда сутки до приёма, пришло напоминание", parents=[second["id"]])
    pl.bank.reject(second["id"], "human", cascade=False)
    return ids, stray


def test_orphans_lists_only_live_records_without_a_parent(tmp_path):
    pl, _ = make(tmp_path)
    ids, stray = hanging_criterion(pl)
    assert [c["id"] for c in integrate.orphans(pl)] == [stray["id"]]
    assert integrate.orphans(pl, ["нет-такого"]) == []
    pl.bank.reject(stray["id"], "human")
    assert integrate.orphans(pl) == []
    assert integrate.run(pl) == {"linked": [], "created": [], "rejected": [], "stale": []}


def test_agent_links_the_record_to_a_fitting_parent(tmp_path):
    pl, agent = make(tmp_path, lambda prompt: [{"id": stray_id[0], "action": "link", "parent": req_id[0]}])
    ids, stray = hanging_criterion(pl)
    stray_id, req_id = [stray["id"]], [ids["req"]["id"]]
    done = integrate.run(pl)
    assert done["linked"] and not done["rejected"]
    assert [p["id"] for p in pl.bank.parents(stray["id"])] == [ids["req"]["id"]]
    assert pl.bank.get(stray["id"])["status"] != "rejected"
    assert KEY in agent.calls[0]["prompt"] and ids["req"]["id"] in agent.calls[0]["prompt"]
    assert integrate.orphans(pl) == []


def test_agent_creates_the_missing_parent_by_the_same_chain(tmp_path):
    pl, _ = make(
        tmp_path,
        lambda prompt: [
            {
                "id": stray_id[0],
                "action": "create_parent",
                "parent_kind": "requirement",
                "title": "Система должна напоминать о приёме",
                "under": problem_id[0],
            }
        ],
    )
    ids, stray = hanging_criterion(pl)
    stray_id, problem_id = [stray["id"]], [ids["problem"]["id"]]
    done = integrate.run(pl)
    assert done["created"] == ["Система должна напоминать о приёме"]
    (parent,) = pl.bank.parents(stray["id"])
    assert parent["kind"] == "requirement" and parent["status"] == "proposed"
    assert [p["id"] for p in pl.bank.parents(parent["id"])] == [ids["problem"]["id"]]


def test_agent_may_reject_and_silence_means_rejection(tmp_path):
    pl, _ = make(tmp_path, lambda prompt: [{"id": stray_id[0], "action": "reject", "why": "дубль"}])
    ids, stray = hanging_criterion(pl)
    stray_id = [stray["id"]]
    assert integrate.run(pl)["rejected"] == [stray["title"]]
    assert pl.bank.get(stray["id"])["status"] == "rejected"
    pl2, _ = make(tmp_path / "b", lambda prompt: [])  # агент промолчал
    ids, stray = hanging_criterion(pl2)
    assert integrate.run(pl2)["rejected"] == [stray["title"]]


def test_wrong_answers_do_not_hang_the_record(tmp_path):
    bad = [
        {"id": "c999", "action": "link", "parent": "c1"},  # чужого кода нет
        {"id": "x", "action": "link"},
    ]
    pl, _ = make(tmp_path, lambda prompt: bad + [{"id": stray_id[0], "action": "link", "parent": area_id[0]}])
    ids, stray = hanging_criterion(pl)
    stray_id, area_id = [stray["id"]], [ids["area"]["id"]]  # область — недопустимый родитель критерия
    done = integrate.run(pl)
    assert done["linked"] == [] and done["rejected"] == [stray["title"]]
    pl3, _ = make(
        tmp_path / "c",
        lambda prompt: [
            {"id": sid[0], "action": "create_parent", "parent_kind": "boundary", "title": "Не то", "under": "c1"},
            {"id": sid[0], "action": "create_parent", "parent_kind": "requirement", "title": "", "under": "c1"},
            {"id": sid[0], "action": "create_parent", "parent_kind": "requirement", "title": "Т", "under": "c404"},
            {"id": sid[0], "action": "link", "parent": "нет"},
        ],
    )
    ids, stray = hanging_criterion(pl3)
    sid = [stray["id"]]
    assert integrate.run(pl3)["rejected"] == [stray["title"]]


def test_agent_failure_rejects_everything_not_integrated(tmp_path):
    registry, agent = agents_with([(KEY, "это не JSON")])
    pl = Pipeline(tmp_path, registry)
    ids, stray = hanging_criterion(pl)
    assert integrate.run(pl)["rejected"] == [stray["title"]]
    assert pl.bank.get(stray["id"])["status"] == "rejected"


def test_linking_marks_accepted_records_below_as_stale(tmp_path):
    pl, _ = make(tmp_path, lambda prompt: [{"id": stray_id[0], "action": "link", "parent": req_id[0]}])
    ids, stray = hanging_criterion(pl)
    scenario = pl.bank.add("scenario", "Запись по звонку", parents=[ids["req"]["id"]])
    pl.bank.set_status([scenario["id"]], "accepted", "human")
    stray_id, req_id = [stray["id"]], [ids["req"]["id"]]
    done = integrate.run(pl)
    assert done["stale"] == [scenario["id"]]
    assert pl.bank.get(scenario["id"])["status"] == "stale"


def test_rework_integrates_hanging_records_after_filling_gaps(tmp_path, monkeypatch):
    pl, _ = make(tmp_path, lambda prompt: [{"id": stray_id[0], "action": "link", "parent": req_id[0]}])
    ids, stray = hanging_criterion(pl)
    stray_id, req_id = [stray["id"]], [ids["req"]["id"]]
    monkeypatch.setitem(flow.REWORK, "requirements", ("analyst", lambda pl: []))
    drafts = [c for c in pl.bank.records("requirement", status="proposed")]
    real = pl.llm.ask_json
    pl.llm.ask_json = lambda role, system, prompt, *a, **k: real(role, system, prompt, *a, **k) if KEY in prompt else {}
    out = flow.rework(pl, "requirements", {"code": [], "failing": []})
    assert drafts and out["integrated"]["linked"]
    assert integrate.orphans(pl) == []


def test_autopilot_integrates_when_the_gate_reports_a_hanging_record(tmp_path, monkeypatch):
    pl, _ = make(tmp_path, lambda prompt: [{"id": stray_id[0], "action": "link", "parent": req_id[0]}])
    ids, stray = hanging_criterion(pl)
    stray_id, req_id = [stray["id"]], [ids["req"]["id"]]
    pl.settings.set_mode("autopilot")
    item = {"key": "gate_requirements", "stage": 1, "num": "gate", "state": "available"}
    queue = [item]
    monkeypatch.setattr(flow, "next_step", lambda pl, skip=frozenset(): queue.pop(0) if queue else None)
    monkeypatch.setattr(flow, "run_step", lambda pl, stage, key: pl.keeper.open("requirements"))
    log = drive(pl, limit=3)
    assert log[0]["gate"] == "returned" and log[0]["integrated"]["linked"]
    assert integrate.orphans(pl) == []
