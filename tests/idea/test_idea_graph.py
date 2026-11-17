import pytest

from studio.idea.graph import (
    ACCEPTED,
    DETAIL,
    HUMAN,
    PROPOSED,
    ConflictError,
    GraphError,
    Store,
    views,
)

AGENT = "package:clarify"


@pytest.fixture
def store(tmp_path):
    created = Store.create(tmp_path, "Запись к врачу")
    created.apply([{"op": "add_card", "kind": "ветка", "title": "Запись к врачу без звонков",
                    "body": "Пациент выбирает время сам"}], HUMAN)
    return created


def add(store, title, parent=None, author=HUMAN, **extra):
    op = {"op": "add_card", "kind": extra.pop("kind", "решение"), "title": title, **extra}
    if parent:
        op["parent"] = parent
    return store.apply([op], author, base_revision=store.revision)["ids"][0]


def test_create_and_reopen(store, tmp_path):
    assert store.revision == 1 and store.title == "Запись к врачу"
    again = Store.open(tmp_path)
    assert again.title == "Запись к врачу"
    assert again.cards["c1"]["title"] == "Запись к врачу без звонков"
    assert again.cards["c1"]["status"] == ACCEPTED
    with pytest.raises(GraphError):
        Store.create(tmp_path, "ещё одна")


def test_new_graph_is_empty_and_has_no_root(tmp_path):
    empty = Store.create(tmp_path / "p", "Проект")
    assert empty.revision == 0 and empty.cards == {} and "root" not in empty.snapshot()
    empty.apply([{"op": "add_card", "kind": "домен", "title": "А"}, {"op": "add_card", "kind": "домен", "title": "Б"}], HUMAN)
    assert len(Store.open(tmp_path / "p").cards) == 2
    empty.apply([{"op": "delete_card", "id": "c1"}], HUMAN)      # удалить можно любую карточку
    assert list(empty.cards) == ["c2"]


def test_statuses_default_by_author(store):
    mine = add(store, "Ветка", "c1")
    agent = add(store, "Предложение", "c1", author=AGENT)
    assert store.cards[mine]["status"] == ACCEPTED
    assert store.cards[agent]["status"] == PROPOSED
    assert store.cards[agent]["author"] == AGENT


def test_batch_is_atomic_and_refs_work(store):
    result = store.apply(
        [
            {"op": "add_card", "kind": "ветка", "title": "Функции", "parent": "c1", "ref": "b"},
            {"op": "add_card", "kind": "решение", "title": "Онлайн-запись", "parent": "@b", "ref": "d"},
            {"op": "add_edge", "from": "@d", "to": "c1", "kind": "уточняет"},
        ],
        AGENT,
    )
    assert result["created"] == {"b": "c2", "d": "c3"} and store.revision == 2
    assert len(store.edges) == 3
    with pytest.raises(GraphError):
        store.apply(
            [{"op": "add_card", "kind": "x", "title": "будет откат"},
             {"op": "add_edge", "from": "c2", "to": "c99"}],
            AGENT,
        )
    assert store.revision == 2 and len(store.cards) == 3


def test_invariants(store):
    card = add(store, "А", "c1")
    with pytest.raises(GraphError, match="уже есть"):
        store.apply([{"op": "add_edge", "from": card, "to": "c1", "kind": DETAIL}], HUMAN)
    with pytest.raises(GraphError):
        store.apply([{"op": "add_edge", "from": card, "to": card}], HUMAN)


def test_ids_are_never_reused(store):
    first = add(store, "А", "c1")
    store.apply([{"op": "delete_card", "id": first}], HUMAN)
    second = add(store, "Б", "c1")
    assert first == "c2" and second == "c3"
    assert not store.edges or all(first not in (e["from"], e["to"]) for e in store.edges.values())


def test_human_edit_wins(store):
    card = add(store, "Оплата картой", "c1", author=AGENT)
    seen = store.revision                       # агент прочитал граф
    store.apply([{"op": "update_card", "id": card, "title": "Оплата по СБП"}], HUMAN)
    with pytest.raises(ConflictError) as info:
        store.apply([{"op": "update_card", "id": card, "body": "…"}], AGENT, base_revision=seen)
    assert info.value.code == "human_changed" and card in info.value.details["ids"]
    with pytest.raises(ConflictError):
        store.apply([{"op": "delete_card", "id": card}], AGENT, base_revision=seen)
    # после повторного чтения правка проходит
    store.apply([{"op": "update_card", "id": card, "body": "…"}], AGENT, base_revision=store.revision)
    assert store.cards[card]["title"] == "Оплата по СБП" and store.cards[card]["body"] == "…"


def test_journal_changes_and_deleted(store):
    start = store.revision
    card = add(store, "Мобильное приложение", "c1", author=AGENT)
    store.apply([{"op": "update_card", "id": card, "status": ACCEPTED}], HUMAN)
    other = add(store, "Напоминания", "c1")
    store.apply([{"op": "delete_card", "id": card}], HUMAN)
    changes = store.changes_since(start)
    assert {c["id"] for c in changes if c["type"] == "card"} == {other}   # созданное и удалённое гасится
    text = views.changes_text(store, start)
    assert "Напоминания" in text and "Мобильное" not in text
    deleted = store.deleted_cards()
    assert [d["card"]["id"] for d in deleted] == [card]
    assert "Мобильное приложение" in views.deleted_text(store)
    assert len(store.card_history(card)) == 3
    mine_hidden = views.changes_text(store, start, exclude_author=AGENT)   # что агент увидит о чужих правках
    assert "удалена [c2]" in mine_hidden and "Напоминания" in mine_hidden


def test_undo(store):
    card = add(store, "А", "c1")
    rev = store.apply([{"op": "delete_card", "id": card}], HUMAN)["revision"]
    assert card not in store.cards
    store.undo(rev)
    assert card in store.cards and len(store.edges) == 1
    assert store.deleted_cards() == [] and rev in store.undone_revisions()
    rev2 = store.apply([{"op": "update_card", "id": card, "title": "Б"}], HUMAN)["revision"]
    store.apply([{"op": "update_card", "id": card, "title": "В"}], HUMAN)
    with pytest.raises(GraphError, match="менялся"):
        store.undo(rev2)


def test_attrs_merge_and_no_empty_revision(store):
    card = add(store, "А", "c1")
    store.apply([{"op": "update_card", "id": card, "attrs": {"check.level": "мешает", "x": 1}}], AGENT)
    store.apply([{"op": "update_card", "id": card, "attrs": {"x": None}}], AGENT)
    assert store.cards[card]["attrs"] == {"check.level": "мешает"}
    rev = store.revision
    result = store.apply([{"op": "update_card", "id": card, "title": "А"}], HUMAN)
    assert result["changed"] is False and store.revision == rev


def test_views(store):
    branch = add(store, "Функции", "c1", kind="ветка")
    leaf = add(store, "Онлайн-запись", branch, author=AGENT, body="Пациент видит свободные окна")
    add(store, "Сирота")
    store.apply([{"op": "add_edge", "from": leaf, "to": "c1", "kind": "уточняет"}], HUMAN)
    text = views.outline(store)
    assert text.index("Функции") < text.index("Онлайн-запись")
    assert "Сирота" in text and "—уточняет→" in text and "предложена" in text and "Запись к врачу" in text.splitlines()[0]
    assert views.dependents(store, "c1") == {branch, leaf}
    assert "Связи:" in views.card_view(store, leaf)
    store.apply([{"op": "add_edge", "from": branch, "to": leaf, "kind": DETAIL}], HUMAN)
    assert views.detail_cycles(store)


def test_layout_is_separate(store):
    store.save_layout({"positions": {"c1": {"x": 10, "y": 20}}})
    assert store.layout()["positions"]["c1"]["x"] == 10
    assert store.revision == 1
