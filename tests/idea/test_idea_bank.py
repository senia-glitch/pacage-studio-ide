import pytest

from studio.pipeline.graph import GraphError, Store, normalize_url

HUMAN = "human"
AGENT = "package:recon"


@pytest.fixture
def store(tmp_path):
    return Store.create(tmp_path, "Проект")


def test_put_list_get_and_persist(store, tmp_path):
    bank = store.bank("competitors")
    first = bank.put("competitor", {"name": "Докдок"}, AGENT, sources=["https://a.example", "https://a.example"])
    second = bank.put("competitor", {"name": "Клиника+"}, AGENT)
    assert first["id"] == "b1" and second["id"] == "b2" and first["sources"] == ["https://a.example"]
    again = Store.open(tmp_path).bank("competitors")
    assert [r["data"]["name"] for r in again.list()] == ["Докдок", "Клиника+"]
    assert again.get("b2")["author"] == AGENT and again.list("other") == []
    with pytest.raises(GraphError):
        again.get("b9")


def test_key_updates_instead_of_duplicating(store):
    bank = store.bank("competitors")
    key = normalize_url("https://www.Example.com/path/#top")
    assert key == "example.com/path"
    one = bank.put("competitor", {"name": "A"}, AGENT, sources=["u1"], key=key)
    two = bank.put("competitor", {"name": "A2"}, "human", sources=["u2"], key=key)
    assert one["id"] == two["id"] and len(bank.list()) == 1
    assert two["data"]["name"] == "A2" and two["sources"] == ["u1", "u2"] and two["author"] == "human"
    assert bank.find(key)["id"] == one["id"] and bank.find("нет") is None
    other = bank.put("analogue", {"name": "B"}, AGENT, key=key)               # ключ уникален в пределах типа
    assert other["id"] != one["id"]


def test_update_remove_clear_and_validation(store):
    bank = store.bank("roles")
    record = bank.put("role", {"name": "Врач"}, AGENT)
    updated = bank.update(record["id"], "human", data={"name": "Терапевт"}, sources=["s"])
    assert updated["data"] == {"name": "Терапевт"} and updated["sources"] == ["s"]
    bank.remove(record["id"])
    assert bank.list() == []
    with pytest.raises(GraphError):
        bank.remove("b1")
    bank.put("role", {"n": 1}, AGENT)
    bank.clear()
    assert bank.list() == []
    for bad in ("", "Большие", "1abc", "a b"):
        with pytest.raises(GraphError):
            store.bank(bad)
    with pytest.raises(GraphError):
        bank.put("", {}, AGENT)
    with pytest.raises(GraphError):
        bank.put("t", "не объект", AGENT)


def test_bank_does_not_touch_graph_revision(store):
    before = store.revision
    store.bank("x").put("t", {"a": 1}, AGENT)
    assert store.revision == before
