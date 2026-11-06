"""Граф фронтенда: факты сканера + метаданные (.studio/frontend.json) → канвас.

Слои графа:
    домен → страница (факт/план) → блок на странице (факт/план) → правило (план)

Идентификаторы:
    страница-факт     page:<folder>
    страница-план     page-plan:<uuid>
    блок-факт         block:<pageFolder>:<Component>
    блок-план         block-plan:<uuid>
    правило           rule:<uuid>
"""

from __future__ import annotations

from pathlib import Path

from studio.frontend import scan_frontend
from studio.frontend.meta import load_meta


def _merge_page(graph_page: dict, meta_page: dict | None) -> None:
    if not meta_page:
        return
    for field in (
        "description", "acceptance", "domain", "note", "deletion", "name", "calls", "created_by",
    ):
        if field in meta_page:
            graph_page[field] = meta_page[field]
    if str(meta_page.get("route") or ""):
        graph_page["route"] = meta_page["route"]
    for rule in meta_page.get("rules") or []:
        graph_page.setdefault("rules", []).append(dict(rule))
    for block_id, block_meta in (meta_page.get("blocks") or {}).items():
        existing = next((b for b in graph_page["blocks"] if b["id"] == block_id), None)
        if existing is None:
            graph_page["blocks"].append({
                "id": block_id,
                "name": block_meta.get("name") or block_id,
                "planned": bool(block_meta.get("planned")),
                "description": block_meta.get("description") or "",
                "acceptance": block_meta.get("acceptance") or [],
                "deletion": block_meta.get("deletion"),
                "calls": list(block_meta.get("calls") or []),
                "created_by": block_meta.get("created_by") or "",
                "rules": [dict(rule) for rule in block_meta.get("rules") or []],
            })
            continue
        for field in ("description", "acceptance", "deletion", "note", "calls"):
            if field in block_meta:
                existing[field] = block_meta[field]
        for rule in block_meta.get("rules") or []:
            existing.setdefault("rules", []).append(dict(rule))


def build_front_graph(project_root: str | Path) -> dict:
    """Факты сканера + пользовательские метаданные → единая структура вкладки."""
    root = Path(project_root)
    scan = scan_frontend(root)
    meta = load_meta(root)
    domains: list[dict] = [dict(domain) for domain in meta["domains"]]
    pages: list[dict] = []

    for fact in scan.get("pagesGraph", []):
        page_id = "page:" + fact["folder"]
        page = {
            "id": page_id,
            "name": fact["folder"],
            "planned": False,
            "route": fact.get("route") or "",
            "file": fact.get("file") or "",
            "domain": "",
            "description": "",
            "acceptance": [],
            "deletion": None,
            "calls": [],
            "created_by": "",
            "blocks": [
                {
                    "id": f"block:{fact['folder']}:{component['name']}",
                    "name": component["name"],
                    "planned": False,
                    "component": component["name"],
                    "file": component["file"],
                    "description": "",
                    "acceptance": [],
                    "deletion": None,
                    "calls": [],
                    "created_by": "",
                    "rules": [],
                }
                for component in fact.get("components") or []
            ],
            "rules": [],
        }
        _merge_page(page, (meta["pages"] or {}).get(page_id))
        pages.append(page)

    for page_id, stored in (meta["pages"] or {}).items():
        if page_id.startswith("page-plan:"):
            page = {
                "id": page_id,
                "name": stored.get("name") or page_id.rsplit(":", 1)[-1],
                "planned": True,
                "route": stored.get("route") or "",
                "file": "",
                "domain": stored.get("domain") or "",
                "description": stored.get("description") or "",
                "acceptance": stored.get("acceptance") or [],
                "deletion": stored.get("deletion"),
                "component": stored.get("component") or "",
                "calls": list(stored.get("calls") or []),
                "created_by": stored.get("created_by") or "",
                "blocks": [
                    {
                        "id": block_id,
                        "name": block.get("name") or block_id,
                        "planned": True,
                        "component": block.get("name") or "",
                        "file": "",
                        "description": block.get("description") or "",
                        "acceptance": block.get("acceptance") or [],
                        "deletion": block.get("deletion"),
                        "calls": list(block.get("calls") or []),
                        "created_by": block.get("created_by") or "",
                        "rules": [dict(rule) for rule in block.get("rules") or []],
                    }
                    for block_id, block in (stored.get("blocks") or {}).items()
                ],
                "rules": [dict(rule) for rule in stored.get("rules") or []],
            }
            pages.append(page)

    # Плановые блоки, привязанные к фактической странице (страница уже в графе).
    for page_id, stored in (meta["pages"] or {}).items():
        if page_id.startswith("page-plan:"):
            continue
        planned_blocks = {
            bid: block
            for bid, block in (stored.get("blocks") or {}).items()
            if block.get("planned")
        }
        if not planned_blocks:
            continue
        existing_page = next((page for page in pages if page["id"] == page_id), None)
        if existing_page is None:
            existing_page = {
                "id": page_id,
                "name": stored.get("name") or page_id[len("page:") :],
                "planned": False,
                "route": stored.get("route") or "",
                "file": "",
                "domain": stored.get("domain") or "",
                "description": stored.get("description") or "",
                "acceptance": stored.get("acceptance") or [],
                "deletion": stored.get("deletion"),
                "calls": list(stored.get("calls") or []),
                "blocks": [],
                "rules": [dict(rule) for rule in stored.get("rules") or []],
            }
            pages.append(existing_page)
        known_ids = {block["id"] for block in existing_page["blocks"]}
        for block_id, block_data in (stored.get("blocks") or {}).items():
            if not block_data.get("planned") or block_id in known_ids:
                continue
            existing_page["blocks"].append({
                "id": block_id,
                "name": block_data.get("name") or block_id,
                "planned": True,
                "component": block_data.get("name") or "",
                "file": "",
                "description": block_data.get("description") or "",
                "acceptance": block_data.get("acceptance") or [],
                "deletion": block_data.get("deletion"),
                "calls": list(block_data.get("calls") or []),
                "created_by": block_data.get("created_by") or "",
                "rules": [dict(rule) for rule in block_data.get("rules") or []],
            })

    graph = {
        "present": scan.get("present", False),
        "root": scan.get("root", "frontend"),
        "domains": domains,
        "pages": pages,
        "stack": scan.get("stack", []),
        "violations": scan.get("violations", []),
        "edges": scan.get("edges", []),
        "stats": scan.get("stats", {}),
        "files": scan.get("files", []),
    }
    graph["stats"]["pages"] = sum(1 for page in pages)
    graph["stats"]["plannedPages"] = sum(1 for page in pages if page["planned"])
    rule_count = 0
    for page in pages:
        rule_count += len(page.get("rules") or [])
        for block in page.get("blocks") or []:
            rule_count += len(block.get("rules") or [])
    graph["stats"]["rules"] = rule_count
    deletions = sum(
        1
        for page in pages
        for item in [page, *(page.get("blocks") or [])]
        if item.get("deletion")
    )
    graph["stats"]["deletions"] = deletions
    return graph


def subtree_front_graph(graph: dict, page_id: str) -> dict | None:
    """Ветка узла: по id страницы/блока/правила — для ТЗ «узел и потомки»."""
    page = next((item for item in graph.get("pages") or [] if item["id"] == page_id), None)
    if page:
        return {"pages": [page]}
    block_id = page_id
    branch_pages = []
    for item in graph.get("pages") or []:
        block = next((b for b in item.get("blocks") or [] if b["id"] == block_id), None)
        if block:
            copied = dict(item)
            copied["blocks"] = [block]
            copied["rules"] = []
            branch_pages.append(copied)
            return {"pages": branch_pages}
    return None
