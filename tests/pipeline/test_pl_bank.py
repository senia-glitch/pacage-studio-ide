import pytest
from pl_helpers import idea, make

from studio.pipeline import PipelineError, ProjectBank
from studio.pipeline.graph import ACCEPTED, PROPOSED, REJECTED


def test_new_records_are_drafts_and_chain_is_built(tmp_path):
    bank, *_ = make(tmp_path)
    r = idea(bank)
    assert all(c["status"] == PROPOSED for c in r.values())
    assert [c["kind"] for c in bank.chain(r["crit"]["id"])] == [
        "criterion",
        "requirement",
        "problem",
        "boundary",
        "area",
    ]
    assert r["crit"]["attrs"]["version"] == 1


def test_required_parent_and_allowed_parent(tmp_path):
    bank, *_ = make(tmp_path)
    r = idea(bank)
    with pytest.raises(PipelineError) as err:
        bank.add("problem", "Висящая проблема")
    assert err.value.code == "parent_required"
    with pytest.raises(PipelineError) as err:
        bank.add("requirement", "Не из проблемы", parents=[r["area"]["id"]])
    assert err.value.code == "bad_parent"
    with pytest.raises(PipelineError):
        bank.add("нет_такого", "x")


def test_descendants_ancestors_and_find(tmp_path):
    bank, *_ = make(tmp_path)
    r = idea(bank)
    kinds = {c["kind"] for c in bank.descendants(r["problem"]["id"])}
    assert kinds == {"requirement", "criterion"}
    assert {c["kind"] for c in bank.ancestors(r["req"]["id"])} == {"problem", "boundary", "area"}
    assert bank.find("problem", "  не ДОЗВОНИТЬСЯ   в регистратуру ")["id"] == r["problem"]["id"]
    assert bank.find("problem", "что-то другое") is None


def test_rejected_is_not_live_and_cannot_be_parent(tmp_path):
    bank, *_ = make(tmp_path)
    r = idea(bank)
    bank.reject(r["problem"]["id"])
    assert r["problem"]["id"] not in [c["id"] for c in bank.records("problem")]
    assert bank.records("problem", status=REJECTED)
    with pytest.raises(PipelineError) as err:
        bank.add("requirement", "ещё", parents=[r["problem"]["id"]])
    assert err.value.code == "rejected_parent"


def test_link_integrates_dangling_record(tmp_path):
    bank, *_ = make(tmp_path)
    r = idea(bank)
    second = bank.add("problem", "Ещё проблема", parents=[r["boundary"]["id"]])
    req2 = bank.add("requirement", "Второе", parents=[second["id"]])
    bank.link(req2["id"], r["problem"]["id"])
    assert {p["id"] for p in bank.parents(req2["id"])} == {second["id"], r["problem"]["id"]}
    bank.link(req2["id"], r["problem"]["id"])  # повтор не ломает
    with pytest.raises(PipelineError):
        bank.link(req2["id"], r["area"]["id"])


def test_revise_bumps_version_and_keeps_history(tmp_path):
    bank, *_ = make(tmp_path)
    r = idea(bank)
    bank.set_status([r["req"]["id"]], ACCEPTED)
    new = bank.revise(r["req"]["id"], "human", title="Система должна записывать быстро")
    assert new["attrs"]["version"] == 2 and new["status"] == ACCEPTED
    assert len(bank.history(r["req"]["id"])) >= 2


def test_constitution_counts_and_reopen(tmp_path):
    bank, *_ = make(tmp_path)
    r = idea(bank)
    assert bank.constitution() == []
    bank.set_status([r["boundary"]["id"]], ACCEPTED)
    assert [c["kind"] for c in bank.constitution()] == ["boundary"]
    assert bank.counts()[1][PROPOSED] == 4
    again = ProjectBank.at(tmp_path)
    assert len(again.records()) == 5
    assert not ProjectBank.exists(tmp_path / "нет")
    with pytest.raises(PipelineError):
        ProjectBank.at(tmp_path / "нет", create=False)
