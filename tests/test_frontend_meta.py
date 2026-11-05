"""Тесты хранилища метаданных фронтенда (.studio/frontend.json)."""

from __future__ import annotations

from pathlib import Path

import pytest

from studio.frontend import meta as fm


def test_пустые_метаданные(tmp_path: Path):
    data = fm.load_meta(tmp_path)
    assert data["version"] == fm.META_VERSION
    assert data["domains"] == [] and data["pages"] == {}


def test_сохранение_страницы_и_домена(tmp_path: Path):
    root = tmp_path / "p"
    root.mkdir()
    fm.save_page_meta(root, {"id": "page:bookings", "description": "Список броней", "domain": "клиент", "acceptance": ["Фильтр по датам"]})
    meta = fm.load_meta(root)
    page = meta["pages"]["page:bookings"]
    assert page["description"] == "Список броней"
    assert page["domain"] == "клиент"
    assert page["acceptance"] == ["Фильтр по датам"]
    assert [d["name"] for d in meta["domains"]] == ["клиент"]


def test_плановая_страница_создаётся(tmp_path: Path):
    root = tmp_path / "p"
    page_id = "page-plan:abc"
    fm.save_page_meta(root, {"id": page_id, "plannedNew": True, "name": "Отчёты", "description": "Отчёты по выручке"})
    meta = fm.load_meta(root)
    assert meta["pages"][page_id]["planned"] is True
    assert meta["pages"][page_id]["name"] == "Отчёты"


def test_правило_блока_crud(tmp_path: Path):
    root = tmp_path / "p"
    guy = fm.save_rule(root, {"pageId": "page:x", "blockId": "block:x:Card", "name": "Правило 1", "acceptance": ["к1"]})
    remove = guy["meta"]
    assert remove["pages"]["page:x"]["blocks"]["block:x:Card"]["rules"][0]["name"] == "Правило 1"
    guy = fm.save_rule(root, {"pageId": "page:x", "blockId": "block:x:Card", "name": "Правило 2 справки"})
    block = guy["meta"]["pages"]["page:x"]["blocks"]["block:x:Card"]
    assert len(block["rules"]) == 2


def test_удаление_планового_правила_и_undo(tmp_path: Path):
    root = tmp_path / "p"
    first = fm.save_rule(root, {"pageId": "page:x", "name": "П1"})["meta"]
    rule = first["pages"]["page:x"]["rules"][0]
    result = fm.delete_planned(root, {"kind": "pageRule", "pageId": "page:x", "ruleId": rule["id"]})
    assert result["meta"]["pages"]["page:x"]["rules"] == []
    fm.restore_planned(root, result["undo"])
    rules_back = fm.load_meta(root)["pages"]["page:x"]["rules"]
    assert rules_back and rules_back[0]["name"] == "П1"


def test_плановая_страница_удаляется_и_восстанавливается(tmp_path: Path):
    root = tmp_path / "p"
    page_id = "page-plan:abc"
    fm.save_page_meta(root, {"id": page_id, "plannedNew": True, "name": "Отчёты"})
    result = fm.delete_planned(root, {"kind": "page", "pageId": page_id})
    assert page_id not in result["meta"]["pages"]
    fm.restore_planned(root, result["undo"])
    assert fm.load_meta(root)["pages"][page_id]["name"] == "Отчёты"


def test_плановый_блок_удаление_восстановление(tmp_path: Path):
    root = tmp_path / "p"
    page_id = "page-plan:abc"
    fm.save_block_meta(root, {"pageId": page_id, "id": "block-plan:1", "planned": True, "name": "Блок"})
    fm.save_block_meta(root, {"pageId": page_id, "id": "block-plan:1", "planned": True, "name": "Блок"})
    result = fm.delete_planned(root, {"kind": "block", "pageId": page_id, "blockId": "block-plan:1"})
    assert "block-plan:1" not in result["meta"]["pages"][page_id]["blocks"]
    fm.restore_planned(root, result["undo"])
    block = fm.load_meta(root)["pages"][page_id]["blocks"]["block-plan:1"]
    assert block["name"] == "Блок" and block["planned"]


def test_удаление_фактического_блока_запрещено(tmp_path: Path):
    root = tmp_path / "p"
    fm.save_block_meta(root, {"pageId": "page:x", "id": "block:x:Card"})
    with pytest.raises(ValueError):
        fm.delete_planned(root, {"kind": "block", "pageId": "page:x", "blockId": "block:x:Card"})


def test_пометка_на_удаление_факта(tmp_path: Path):
    root = tmp_path / "p"
    fm.save_block_meta(root, {"pageId": "page:x", "id": "block:x:Card"})
    fm.save_delete_note(root, {"pageId": "page:x", "blockId": "block:x:Card", "note": "Убрать карту", "acceptance": ["нет карты"]})
    block = fm.load_meta(root)["pages"]["page:x"]["blocks"]["block:x:Card"]
    assert block["deletion"]["note"] == "Убрать карту"
    fm.save_delete_note(root, {"pageId": "page:x", "blockId": "block:x:Card", "note": ""})
    block = fm.load_meta(root)["pages"]["page:x"]["blocks"]["block:x:Card"]
    assert block.get("deletion") is None


def test_домены_create_rename_delete(tmp_path: Path):
    root = tmp_path / "p"
    fm.save_domain(root, {"op": "create", "name": "клиент"})
    meta = fm.load_meta(root)
    domain_id = meta["domains"][0]["id"]
    fm.save_page_meta(root, {"id": "page:x", "description": "x", "domain": "клиент"})
    fm.save_domain(root, {"op": "rename", "id": domain_id, "name": "админ"})
    meta = fm.load_meta(root)
    assert meta["pages"]["page:x"]["domain"] == "админ"
    fm.save_domain(root, {"op": "delete", "id": domain_id})
    meta = fm.load_meta(root)
    assert meta["domains"] == []
    assert meta["pages"]["page:x"]["domain"] == ""


def test_плановый_блок_переносится_на_другую_страницу(tmp_path: Path):
    root = tmp_path / "p"
    fm.save_block_meta(root, {"pageId": "page:a", "id": "block-plan:1", "planned": True, "name": "Блок"})
    result = fm.move_block(root, {"blockId": "block-plan:1", "fromPageId": "page:a", "toPageId": "page:b"})
    pages = result["meta"]["pages"]
    assert "block-plan:1" not in pages["page:a"]["blocks"]
    assert pages["page:b"]["blocks"]["block-plan:1"]["name"] == "Блок"


def test_факт_блок_переносится_пометкой_и_планом(tmp_path: Path):
    root = tmp_path / "p"
    fm.move_block(root, {"blockId": "block:a:Card", "fromPageId": "page:a", "toPageId": "page:b", "name": "Card"})
    pages = fm.load_meta(root)["pages"]
    old = pages["page:a"]["blocks"]["block:a:Card"]
    moved = pages["page:b"]["blocks"]
    assert old["deletion"]["note"].startswith("Перенести блок «Card»")
    planned = [b for b in moved.values() if b.get("planned")]
    assert planned and planned[0]["name"] == "Card"
