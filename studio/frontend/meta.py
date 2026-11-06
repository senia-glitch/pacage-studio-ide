"""Метаданные фронтенда: хранилище `.studio/frontend.json` и операции.

Фронтенд-часть проекта описывается независимо от бэкенда: домены (роли),
страницы, блоки страниц и правила. Фактические узлы (страницы, блоки)
сканер берёт из кода; здесь живут только пользовательские данные:
описания, критерии, плановые узлы, пометки на удаление сформированных.

Файл не перезаписывается частями: любое изменение — полный атомарный write.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from studio.frontend.links import normalize_calls
from studio.store import atomic_write

FRONTEND_META = "frontend.json"
META_VERSION = 1


def _meta_path(root: Path) -> Path:
    return Path(root) / ".studio" / FRONTEND_META


def empty_meta() -> dict:
    return {"version": META_VERSION, "domains": [], "pages": {}}


def load_meta(root: Path) -> dict:
    """Прочитать метаданные фронтенда; файла нет — пустая структура."""
    path = _meta_path(Path(root))
    if not path.is_file():
        return empty_meta()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_meta()
    if not isinstance(data, dict):
        return empty_meta()
    meta = empty_meta()
    if isinstance(data.get("domains"), list):
        meta["domains"] = [d for d in data["domains"] if isinstance(d, dict) and d.get("name")]
    pages = data.get("pages")
    if isinstance(pages, dict):
        for key, page in pages.items():
            if isinstance(page, dict):
                meta["pages"][str(key)] = page
    return meta


def save_meta(root: Path, meta: dict) -> None:
    """Полный атомарный write метаданных (создаёт .studio при необходимости)."""
    root = Path(root)
    store_dir = root / ".studio"
    store_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
    if not atomic_write(_meta_path(root), payload):
        raise RuntimeError("Не удалось сохранить .studio/frontend.json")


def new_id(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4()}"


def find_domain(meta: dict, name: str) -> dict | None:
    return next((d for d in meta["domains"] if str(d.get("name")) == str(name)), None)


def ensure_domain(meta: dict, name: str) -> dict:
    """Вернуть домен по имени; создать при отсутствии."""
    existing = find_domain(meta, name)
    if existing:
        return existing
    domain = {"id": new_id("domain"), "name": name}
    meta["domains"].append(domain)
    return domain


def _page_of(meta: dict, page_id: str) -> dict | None:
    return meta["pages"].get(str(page_id))


def ensure_page(meta: dict, page_id: str) -> dict:
    """Контейнер страницы (для факт-страниц создаётся при первом редактировании)."""
    page = _page_of(meta, page_id)
    if page is None:
        page = {"id": page_id, "blocks": {}, "rules": []}
        meta["pages"][page_id] = page
    page.setdefault("blocks", {})
    page.setdefault("rules", [])
    return page


def save_page_meta(root: Path, data: dict) -> dict:
    """Описания/критерии/домен/маршрут страницы (факт или план).

    Плановая страница создаётся, если id начинается с page-plan и отсутствует.
    """
    meta = load_meta(root)
    page_id = str(data.get("id") or "")
    if not page_id:
        raise ValueError("id страницы обязателен")
    planned_new = page_id.startswith("page-plan:")
    page = meta["pages"].get(page_id)
    if page is None and planned_new:
        page = {
            "id": page_id,
            "planned": True,
            "name": str(data.get("name") or "").strip(),
            "blocks": {},
            "rules": [],
        }
        meta["pages"][page_id] = page
    if page is None:
        page = ensure_page(meta, page_id)
    if "name" in data and (planned_new or str(meta["pages"].get(page_id, {}).get("planned") or "")):
        page["name"] = str(data.get("name") or "").strip()
    if "description" in data:
        page["description"] = str(data.get("description") or "")
    if "acceptance" in data:
        raw = data.get("acceptance")
        page["acceptance"] = [str(item) for item in raw] if isinstance(raw, list) else []
    if "domain" in data:
        domain_name = str(data.get("domain") or "").strip()
        if domain_name:
            ensure_domain(meta, domain_name)
            page["domain"] = domain_name
        else:
            page["domain"] = ""
    if "route" in data:
        page["route"] = str(data.get("route") or "").strip()
    if "calls" in data:
        page["calls"] = normalize_calls(data.get("calls"))
    if "plannedNew" in data and data.get("plannedNew") and page_id.startswith("page-plan:"):
        page["planned"] = True
        page["name"] = str(data.get("name") or "").strip()
    save_meta(root, meta)
    return {"meta": meta}


def save_block_meta(root: Path, data: dict) -> dict:
    """Описания/критерии блока (факт или плановый блок страницы).

    data: {pageId, id, name?, description?, acceptance?, planned?}
    """
    meta = load_meta(root)
    page_id = str(data.get("pageId") or "")
    block_id = str(data.get("id") or "")
    if not page_id or not block_id:
        raise ValueError("pageId и id блока обязательны")
    page = meta["pages"].get(page_id)
    if page is None and page_id.startswith("page-plan:"):
        page = {
            "id": page_id,
            "planned": True,
            "name": str(data.get("pageName") or "").strip(),
            "blocks": {},
            "rules": [],
        }
        meta["pages"][page_id] = page
    if page is None:
        page = ensure_page(meta, page_id)
    blocks = page.setdefault("blocks", {})
    block = blocks.get(block_id)
    if block is None:
        block = {"id": block_id, "rules": []}
        blocks[block_id] = block
        if block_id.startswith("block-plan:"):
            block["planned"] = True
            block["name"] = str(data.get("name") or "").strip()
    if "name" in data and block.get("planned"):
        block["name"] = str(data.get("name") or "").strip()
    if "description" in data:
        block["description"] = str(data.get("description") or "")
    if "acceptance" in data:
        raw = data.get("acceptance")
        block["acceptance"] = [str(item) for item in raw] if isinstance(raw, list) else []
    if "calls" in data:
        block["calls"] = normalize_calls(data.get("calls"))
    save_meta(root, meta)
    return {"meta": meta}


def save_rule(root: Path, data: dict) -> dict:
    """Создать/обновить правило страницы или блока (правила всегда плановые).

    data: {pageId, blockId?, id?, name, description, acceptance, note}
    id опционален: без id создаётся новое правило.
    """
    meta = load_meta(root)
    page_id = str(data.get("pageId") or "")
    if not page_id:
        raise ValueError("pageId обязателен")
    page = meta["pages"].get(page_id)
    if page is None:
        page = {
            "id": page_id,
            "planned": page_id.startswith("page-plan:"),
            "blocks": {},
            "rules": [],
        }
        meta["pages"][page_id] = page
    page.setdefault("blocks", {})
    rules = page.setdefault("rules", [])
    rule_id = str(data.get("id") or "")
    target = None
    if data.get("blockId"):
        block_id = str(data.get("blockId"))
        block = page["blocks"].get(block_id)
        if block is None:
            block = {"id": block_id, "rules": []}
            page["blocks"][block_id] = block
        block.setdefault("rules", [])
        rules = block["rules"]
    if rule_id:
        target = next((item for item in rules if str(item.get("id")) == rule_id), None)
    if target is None:
        target = {"id": rule_id or new_id("rule")}
        rules.append(target)
    if "name" in data:
        target["name"] = str(data.get("name") or "").strip()
    if "description" in data:
        target["description"] = str(data.get("description") or "")
    if "acceptance" in data:
        raw = data.get("acceptance")
        target["acceptance"] = [str(item) for item in raw] if isinstance(raw, list) else []
    if "note" in data:
        target["note"] = str(data.get("note") or "")
    if "done" in data:
        target["done"] = bool(data.get("done"))
    save_meta(root, meta)
    return {"meta": meta}


def move_block(root: Path, data: dict) -> dict:
    """Перенос блока на другую страницу.

    data: {blockId, fromPageId, toPageId, name?}
    Плановый блок переносится как контейнер; фактический блок кода не
    трогается: на старой странице ставится пометка на удаление, на новой
    создаётся плановый блок с тем же именем и правилами.
    """
    meta = load_meta(root)
    block_id = str(data.get("blockId") or "")
    from_page_id = str(data.get("fromPageId") or "")
    to_page_id = str(data.get("toPageId") or "")
    if not block_id or not from_page_id:
        raise ValueError("blockId и fromPageId обязательны")
    if not to_page_id:
        raise ValueError("Страница-цель обязательна")
    if from_page_id == to_page_id:
        raise ValueError("Источник и цель совпадают")
    from_page = ensure_page(meta, from_page_id)
    blocks = from_page.setdefault("blocks", {})
    block = blocks.get(block_id)
    if block is None:
        blocks[block_id] = {
            "id": block_id,
            "name": str(data.get("name") or "").strip(),
            "description": "",
            "acceptance": [],
            "rules": [],
        }
        block = blocks[block_id]
    to_page = ensure_page(meta, to_page_id)
    if to_page.get("domain"):
        pass
    if block.get("planned"):
        blocks.pop(block_id, None)
        to_page.setdefault("blocks", {})[str(block_id)] = block
    else:
        name = block.get("name") or str(data.get("name") or "")
        from_name = from_page.get("name") or from_page_id
        block["deletion"] = {
            "note": f"Перенести блок «{name}» на страницу «{to_page.get('name') or to_page_id}»",
            "acceptance": [f"Блок «{name}» больше не используется на «{from_name}»"],
        }
        plan_id = new_id("block-plan")
        moved = {
            "id": plan_id,
            "planned": True,
            "name": name,
            "description": block.get("description") or "",
            "acceptance": block.get("acceptance") or [],
            "calls": list(block.get("calls") or []),
            "rules": [dict(rule) for rule in block.get("rules") or []],
        }
        to_page.setdefault("blocks", {})[plan_id] = moved
    save_meta(root, meta)
    return {"meta": meta}


def save_delete_note(root: Path, data: dict) -> dict:
    """Пометка на удаление для фактической страницы/блока (код не меняется).

    data: {pageId, blockId?, note, acceptance}; пустая заметка снимает пометку.
    Хранится на узле в поле `deletion` и попадает в ТЗ/отчёт как задача.
    """
    meta = load_meta(root)
    page_id = str(data.get("pageId") or "")
    block_id = str(data.get("blockId") or "")
    note = str(data.get("note") or "")
    acceptance_raw = data.get("acceptance")
    acceptance = [str(item) for item in acceptance_raw] if isinstance(acceptance_raw, list) else []
    page = meta["pages"].get(page_id)
    target = (page or {}).get("blocks", {}).get(block_id) if block_id else page
    if target is None:
        raise ValueError("Узел не найден в метаданных — сначала сохраните описание")
    if note.strip() or acceptance:
        target["deletion"] = {"note": note, "acceptance": acceptance}
    else:
        target.pop("deletion", None)
    save_meta(root, meta)
    return {"meta": meta}


def delete_planned(root: Path, data: dict) -> dict:
    """Удалить плановый узел (kind: page|block|blockRule|pageRule) с undo.

    Фактические страницы/блоки удаляются пометкой (save_delete_note), а не здесь.
    """
    meta = load_meta(root)
    kind = str(data.get("kind") or "")
    page_id = str(data.get("pageId") or "")
    block_id = str(data.get("blockId") or "")
    rule_id = str(data.get("ruleId") or "")
    if kind == "page":
        page = meta["pages"].get(page_id)
        if page is None or not page.get("planned"):
            raise ValueError(f"Плановая страница {page_id} не найдена")
        undo = {"kind": "page", "page": page}
        meta["pages"].pop(page_id, None)
    elif kind == "block":
        page = meta["pages"].get(page_id)
        block = (page or {}).get("blocks", {}).get(block_id)
        if page is None or block is None:
            raise ValueError(f"Блок {block_id} не найден")
        if not block.get("planned"):
            raise ValueError("Фактический блок удаляется пометкой, а не удалением")
        undo = {"kind": "block", "pageId": page_id, "block": block}
        page["blocks"].pop(block_id, None)
    elif kind in ("blockRule", "pageRule"):
        page = meta["pages"].get(page_id)
        if page is None:
            raise ValueError(f"Страница {page_id} не найдена")
        rules = page["blocks"][block_id]["rules"] if kind == "blockRule" else page.get("rules", [])
        rule = next((item for item in rules if str(item.get("id")) == rule_id), None)
        if rule is None:
            raise ValueError(f"Правило {rule_id} не найдено")
        undo = {"kind": kind, "pageId": page_id, "blockId": block_id, "rule": rule}
        if kind == "blockRule":
            keep = [item for item in rules if str(item.get("id")) != rule_id]
            page["blocks"][block_id]["rules"] = keep
        else:
            page["rules"] = [item for item in rules if str(item.get("id")) != rule_id]
    else:
        raise ValueError("Неизвестный тип планового узла")
    save_meta(root, meta)
    return {"meta": meta, "undo": undo}


def restore_planned(root: Path, undo: dict) -> dict:
    """Вернуть удалённую плановую ветку (по undo записи delete_planned)."""
    meta = load_meta(root)
    kind = str(undo.get("kind") or "")
    rule = undo.get("rule")
    block = undo.get("block")
    page = undo.get("page")
    page_id = str(undo.get("pageId") or "")
    block_id = str(undo.get("blockId") or "")
    if kind == "page" and page:
        meta["pages"][str(page.get("id") or page_id)] = page
    elif kind == "block" and block and page is None:
        target = meta["pages"].setdefault(page_id, {"id": page_id, "blocks": {}, "rules": []})
        target.setdefault("blocks", {})[str(block["id"])] = block
    elif kind == "block" and block and page:
        stored = meta["pages"].setdefault(str(page.get("id") or page_id), page)
        stored.setdefault("blocks", {})[str(block["id"])] = block
    elif kind == "blockRule" and rule:
        container = meta["pages"].setdefault(page_id, {"id": page_id, "blocks": {}, "rules": []})
        blocks = container.setdefault("blocks", {})
        holder = blocks.setdefault(block_id, {"id": block_id, "rules": []})
        holder["rules"].append(rule)
    elif kind == "pageRule" and rule:
        item = meta["pages"].setdefault(page_id, {"id": page_id, "blocks": {}, "rules": []})
        item.setdefault("rules", []).append(rule)
    else:
        raise ValueError("Undo не читается")
    save_meta(root, meta)
    return {"meta": meta}


def save_domain(root: Path, data: dict) -> dict:
    """Создать/переименовать домен (роль фронтенда); delete: по id/name."""
    meta = load_meta(root)
    action = str(data.get("op") or "create")
    name = str(data.get("name") or "").strip()
    domain_id = str(data.get("id") or "")
    if action == "create":
        if not name:
            raise ValueError("Имя домена обязательно")
        ensure_domain(meta, name)
    elif action == "rename":
        domain = next((d for d in meta["domains"] if str(d.get("id")) == domain_id), None)
        old_name = domain.get("name") if domain else None
        if not domain or not name:
            raise ValueError("Домен и новое имя обязательны")
        domain["name"] = name
        for page in meta["pages"].values():
            if page.get("domain") == old_name:
                page["domain"] = name
    elif action == "delete":
        domain = next((d for d in meta["domains"] if str(d.get("id")) == domain_id), None)
        if not domain:
            raise ValueError("Домен не найден")
        meta["domains"] = [d for d in meta["domains"] if str(d.get("id")) != domain_id]
        for page in meta["pages"].values():
            if page.get("domain") == domain["name"]:
                page["domain"] = ""
    save_meta(root, meta)
    return {"meta": meta}


__all__ = [
    "FRONTEND_META",
    "delete_planned",
    "empty_meta",
    "ensure_domain",
    "ensure_page",
    "load_meta",
    "move_block",
    "restore_planned",
    "save_block_meta",
    "save_delete_note",
    "save_domain",
    "save_meta",
    "save_page_meta",
    "save_rule",
]
