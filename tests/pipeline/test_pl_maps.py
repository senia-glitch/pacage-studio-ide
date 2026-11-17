import pytest
from fake_llm import agents_with
from pl_helpers import idea

from studio.pipeline import Pipeline, maps
from studio.pipeline.graph import ACCEPTED, STALE


def pipeline(tmp_path):
    registry, _ = agents_with([])
    return Pipeline(tmp_path, registry)


def fill(pl):
    """Идея из помощника тестов лежит в банке конвейера: проблема → требование → критерий."""
    return idea(pl.bank)


def by_label(data, label):
    return next(n for n in data["nodes"] if n["label"] == label)


def test_unknown_kind_falls_back_to_system_map(tmp_path):
    pl = pipeline(tmp_path)
    data = maps.build(pl, "нет-такой")
    assert data["title"] == "Карта системы" and data["nodes"] == [] and data["edges"] == []
    assert len(data["columns"]) == 7 and data["columns"][-1] == "Критерий · Тест"


def test_system_map_colors_and_edges(tmp_path):
    pl = pipeline(tmp_path)
    ids = fill(pl)
    data = maps.build(pl, "trace")
    problem = by_label(data, "Не дозвониться в регистратуру")
    req = by_label(data, "Система должна записывать пациента")
    # проблема без принятого требования-потомка не разрыв: потомок есть, но это черновик
    assert problem["state"] == "plan" and req["state"] == "gap"
    assert {"from": ids["problem"]["id"], "to": ids["req"]["id"]} in data["edges"]
    # критерий — дополнительный узел в колонке требований, он уже в карте
    crit = next(n for n in data["nodes"] if n["kind"] == "criterion")
    assert crit["extra"] is True and crit["col"] == len(data["columns"]) - 1
    assert data["counts"]["plan"] >= 1


def test_accepted_and_stale_states(tmp_path):
    pl = pipeline(tmp_path)
    ids = fill(pl)
    pl.bank.set_status([ids["problem"]["id"]], ACCEPTED, "human")
    pl.bank.set_status([ids["req"]["id"]], ACCEPTED, "human")
    data = maps.build(pl, "trace")
    assert by_label(data, "Не дозвониться в регистратуру")["state"] == "ok"
    pl.bank.set_status([ids["req"]["id"]], STALE, "human")
    data = maps.build(pl, "trace")
    assert by_label(data, "Система должна записывать пациента")["state"] == "stale"


def test_code_without_description_is_orphan(tmp_path):
    pl = pipeline(tmp_path)
    pl.bank.add_from_code("endpoint", "GET /api/ping", attrs={"method": "GET", "path": "/api/ping"})
    data = maps.build(pl, "trace")
    node = by_label(data, "GET /api/ping")
    assert node["state"] == "orphan" and data["counts"]["orphan"] == 1


def test_er_and_back_maps_link_tables(tmp_path):
    pl = pipeline(tmp_path)
    ids = fill(pl)
    scenario = pl.bank.add("scenario", "Пациент записывается", parents=[ids["req"]["id"]])
    cols = [{"name": "id", "type": "int", "key": "pk", "ref": "", "null": False}]
    doctors = pl.bank.add(
        "table", "doctors", parents=[scenario["id"]], attrs={"name": "doctors", "columns": cols}
    )
    visits_cols = [
        *cols,
        {"name": "doctor_id", "type": "int", "key": "fk", "ref": "doctors.id", "null": False},
    ]
    pl.bank.add(
        "table",
        "visits",
        parents=[scenario["id"]],
        attrs={"name": "visits", "columns": visits_cols},
    )
    er = maps.build(pl, "er")
    assert len(er["columns"]) == 3 and sorted(n["col"] for n in er["nodes"]) == [0, 1]
    assert {n["label"] for n in er["nodes"]} == {"doctors", "visits"}
    assert any(e.get("label") == "doctor_id" for e in er["edges"])
    page = pl.bank.add("page", "Запись", parents=[scenario["id"]])
    call = pl.bank.add("call", "POST /api/visits", parents=[page["id"]])
    pl.bank.add(
        "endpoint",
        "POST /api/visits",
        parents=[call["id"]],
        attrs={"method": "POST", "path": "/api/visits", "tables": ["visits", "нет-такой"]},
    )
    back = maps.build(pl, "back")
    endpoint = next(n for n in back["nodes"] if n["kind"] == "endpoint")
    table_ids = {n["id"] for n in back["nodes"] if n["kind"] == "table"}
    reached = {e["to"] for e in back["edges"] if e["from"] == endpoint["id"]}
    assert len(reached) == 1 and reached <= table_ids
    assert doctors["id"] in table_ids


@pytest.mark.parametrize("kind", list(maps.MAPS))
def test_every_map_builds_on_empty_bank(tmp_path, kind):
    data = maps.build(pipeline(tmp_path), kind)
    assert data["nodes"] == [] and data["counts"] == {}
