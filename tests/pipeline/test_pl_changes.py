import pytest
from pl_helpers import idea, make

from studio.pipeline import PipelineError
from studio.pipeline.graph import ACCEPTED, PROPOSED, STALE


def accepted_idea(bank):
    r = idea(bank)
    bank.set_status([c["id"] for c in r.values()], ACCEPTED)
    scenario = bank.add("scenario", "Записаться", parents=[r["req"]["id"]])
    page = bank.add("page", "Запись", parents=[scenario["id"]])
    bank.set_status([scenario["id"], page["id"]], ACCEPTED)
    r.update(scenario=scenario, page=page)
    return r


def test_edit_request_counts_impact_and_owner(tmp_path):
    bank, settings, _, changes = make(tmp_path)
    r = accepted_idea(bank)
    req = changes.open(
        "Запись нужна и по телефону",
        "human",
        patches={r["req"]["id"]: {"title": "Система должна записывать по телефону и онлайн"}},
    )
    assert req["owner_stage"] == 1 and req["type"] == "edit"
    assert set(req["impact"]) == {r["crit"]["id"], r["scenario"]["id"], r["page"]["id"]}
    assert req["size"] == "large"  # затронуто требование и сценарий


def test_accept_edit_makes_new_version_and_marks_stale_only_impact(tmp_path):
    bank, settings, _, changes = make(tmp_path)
    r = accepted_idea(bank)
    other = bank.add("problem", "Другая проблема", parents=[r["boundary"]["id"]])
    bank.set_status([other["id"]], ACCEPTED)
    req = changes.open("Уточнить", "human", patches={r["req"]["id"]: {"body": "в течение минуты"}})
    done = changes.decide(req["id"], "accept", "human", keep=[r["crit"]["id"]])
    assert done["status"] == "accepted"
    assert bank.get(r["req"]["id"])["attrs"]["version"] == 2
    assert bank.get(r["req"]["id"])["status"] == ACCEPTED
    assert bank.get(r["scenario"]["id"])["status"] == STALE
    assert bank.get(r["page"]["id"])["status"] == STALE
    assert bank.get(r["crit"]["id"])["status"] == ACCEPTED  # агент/человек снял отметку
    assert bank.get(other["id"])["status"] == ACCEPTED  # посторонняя запись не тронута


def test_reject_changes_nothing(tmp_path):
    bank, settings, _, changes = make(tmp_path)
    r = accepted_idea(bank)
    req = changes.open("Не надо", "agent:executor", patches={r["req"]["id"]: {"title": "Другое"}})
    changes.decide(req["id"], "reject", "human", "не согласен")
    assert bank.get(r["req"]["id"])["title"] == "Система должна записывать пациента"
    assert bank.get(r["req"]["id"])["attrs"]["version"] == 1
    assert changes.get(req["id"])["status"] == "rejected"
    with pytest.raises(PipelineError):
        changes.decide(req["id"], "accept", "human")


def test_small_fix_is_a_quick_path(tmp_path):
    bank, settings, _, changes = make(tmp_path)
    r = accepted_idea(bank)
    page = changes.open(
        "Поправить подпись", "human", patches={r["page"]["id"]: {"title": "Запись!"}}
    )
    assert page["size"] == "small"  # одна страница, без схемы, контракта и смысла
    settings.set_number("quick_fix_limit", 1)
    assert (
        changes.open("То же", "human", patches={r["page"]["id"]: {"body": "x"}})["size"] == "small"
    )


def test_new_feature_goes_to_stage_one_and_conflicts_are_caught(tmp_path):
    bank, settings, _, changes = make(tmp_path)
    r = accepted_idea(bank)
    spec = {"kind": "problem", "title": "Нет напоминаний", "parents": [r["boundary"]["id"]]}
    req = changes.open("Нужны напоминания", "human", new=spec)
    assert req["owner_stage"] == 1 and req["conflicts"] == []
    done = changes.decide(req["id"], "accept", "human")
    new = bank.get(done["result"])
    assert new["status"] == PROPOSED and new["kind"] == "problem"  # проходит ворота как все
    clash = changes.open("Дубль", "agent:analyst", new={**spec, "title": " нет НАПОМИНАНИЙ "})
    assert clash["conflicts"]
    with pytest.raises(PipelineError) as err:
        changes.decide(clash["id"], "accept", "human")
    assert err.value.code == "conflict"


def test_bad_requests(tmp_path):
    bank, settings, _, changes = make(tmp_path)
    r = accepted_idea(bank)
    with pytest.raises(PipelineError):
        changes.open("  ", "human", patches={r["req"]["id"]: {}})
    with pytest.raises(PipelineError):
        changes.open("x", "human")
    with pytest.raises(PipelineError):
        changes.open("x", "human", patches={"c999": {}})
    with pytest.raises(PipelineError):
        changes.open("x", "human", new={"kind": "нет", "title": "t"})
    with pytest.raises(PipelineError):
        changes.get("r99")
    request = changes.open("ок", "human", patches={r["req"]["id"]: {"body": "b"}})
    with pytest.raises(PipelineError) as err:
        changes.decide(request["id"], "maybe", "human")
    assert err.value.code == "bad_decision"
    with pytest.raises(PipelineError):
        changes.decide("r99", "accept", "human")


def test_who_decides_follows_the_mode_and_requests_persist(tmp_path):
    bank, settings, _, changes = make(tmp_path)
    r = accepted_idea(bank)
    changes.open("Правка", "human", patches={r["req"]["id"]: {"body": "b"}})
    assert changes.needs_human() and len(changes.open_requests()) == 1
    settings.set_mode("autopilot")
    assert not changes.needs_human()
    from studio.pipeline import ChangeLog

    again = ChangeLog(bank, settings, tmp_path)
    assert len(again.open_requests()) == 1
