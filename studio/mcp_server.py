"""MCP-сервер студии: инструменты для AI-агента вместо «сырого» HTTP.

Запуск: ``studio mcp`` (stdio). Сервер ничего не решает сам: он подключается к уже
запущенной студии (``studio_connect`` со строкой из окна «AI-агент») и пересылает
вызовы в её HTTP API с токеном выбранного режима. Права, правило владения и журнал
остаются на стороне студии, поэтому запрещённое действие вернёт отказ студии.

Нужен пакет ``mcp`` (``pip install "pacage-studio[mcp]"``); поддерживаются версии с
``mcp.server.mcpserver.MCPServer`` (2.x) и ``mcp.server.fastmcp.FastMCP`` (1.x).
"""

from __future__ import annotations

import functools
import json
import uuid
from typing import Any

from studio.agent.client import StudioClient, StudioError, parse_connection

INSTRUCTIONS = """\
Студия (pacage-studio) — локальный сервис с картой проекта; человек работает в её
интерфейсе, ты — через эти инструменты. Решения всегда за человеком: ты предлагаешь и
реализуешь выбранное, но ничего не применяешь за него.

1. Попроси у человека строку подключения из окна «AI-агент» в студии (Studio, Token, Mode)
   и вызови studio_connect. Токен нельзя сохранять в файлы, коммиты и логи; после
   перезапуска студии нужна новая строка.
2. Режим planning: изучи проект (studio_overview, studio_graph, studio_frontend,
   studio_ideas), предложи идеи (studio_idea_add) и плановые узлы (studio_node_add,
   studio_page_add, studio_block_add). Код не пиши.
3. Режим implement: возьми ТЗ человека (studio_brief), реализуй только его задачи,
   проверь (studio_verify) до статуса «выполнено», отправь отчёт (studio_report).
4. Править и удалять можно только своё (бейдж «агент»); к чужому можно добавлять новое.
   Запрещённое студия отклонит (mode_forbidden) — не обходи, сообщи человеку.
"""


class McpUnavailable(ImportError):
    """Пакет mcp не установлен."""


def _server_class():
    try:
        from mcp.server.mcpserver import MCPServer  # mcp 2.x

        return MCPServer
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP  # mcp 1.x

        return FastMCP
    except ImportError as exc:
        raise McpUnavailable(
            'MCP-сервер требует пакет mcp: pip install "pacage-studio[mcp]"'
        ) from exc


class Session:
    """Подключение к студии: хранится только в памяти процесса."""

    def __init__(self, client: StudioClient | None = None):
        self.client = client
        self.info: dict = {}

    def need(self) -> StudioClient:
        if self.client is None:
            raise StudioError(
                "not_connected",
                "Нет подключения к студии: попросите у человека строку из окна «AI-агент» "
                "и вызовите studio_connect.",
            )
        return self.client


def _short(text: Any, limit: int = 160) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _links_for(response: dict, ids: set[str]) -> list[dict]:
    rows = ((response.get("graph") or {}).get("links") or {}).get("rows") or []
    return [
        {"call": row["call"], "status": row["status"]}
        for row in rows
        if row.get("node") in ids
    ]


def _part(part: str) -> str:
    if part not in ("backend", "frontend"):
        raise StudioError("invalid_input", "part: backend или frontend")
    return part


def _tool_error():
    """Класс «ожидаемой» ошибки инструмента: клиент получает сообщение без трассировки."""
    try:
        from mcp.server.mcpserver.exceptions import ToolError  # mcp 2.x

        return ToolError
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp.exceptions import ToolError  # mcp 1.x

        return ToolError
    except ImportError:
        return RuntimeError


def _guarded(func):
    """Ошибки студии — в понятное сообщение об ошибке инструмента."""
    tool_error = _tool_error()

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except StudioError as exc:
            raise tool_error(str(exc)) from exc

    return wrapper


def create_server(session: Session | None = None):
    """Собрать MCP-сервер; ``session`` можно подставить готовый (тесты, встраивание)."""
    session = session or Session()
    server = _server_class()("pacage-studio", instructions=INSTRUCTIONS)

    registry: dict[str, Any] = {}

    def tool(func):
        wrapped = _guarded(func)
        registry[func.__name__] = wrapped  # для тестов и встраивания: вызов без протокола
        return server.tool()(wrapped)

    # ------------------------------------------------------------ подключение

    @tool
    def studio_connect(connection: str = "", url: str = "", token: str = "") -> dict:
        """Подключиться к запущенной студии.

        Передайте строку из окна «AI-агент» целиком (connection) либо url и token.
        Возвращает режим токена (plan или implement), версию студии и корень проекта.
        """
        fields = parse_connection(connection)
        url = url or fields.get("url", "")
        token = token or fields.get("token", "")
        if not url or not token:
            raise StudioError(
                "bad_connection",
                "Передайте строку подключения целиком (connection) или url и token.",
            )
        client = StudioClient(url, token)
        info = client.get("/api/agent")
        session.client, session.info = client, info
        mode = info.get("mode", "")
        asked = fields.get("mode")
        note = (
            f" (в строке указан режим {asked}, но токен даёт {mode})"
            if asked and asked != mode
            else ""
        )
        steps = {
            "plan": "studio_overview → studio_graph/studio_frontend/studio_ideas → "
            "studio_idea_add, studio_node_add, studio_page_add",
            "implement": "studio_brief → реализовать задачи → studio_verify → studio_report",
        }
        return {
            "connected": True,
            "mode": mode,
            "studio_version": info.get("studio_version"),
            "project_root": info.get("project_root"),
            "next": steps.get(mode, "режим полного доступа: агенту нужен токен plan или implement")
            + note,
        }

    # ------------------------------------------------------------------ чтение

    @tool
    def studio_overview() -> dict:
        """Сводка по проекту: корень, счётчики узлов, ревизия, число ждущих отчётов."""
        client = session.need()
        state = client.get("/api/state")
        reports = client.get("/api/agent/reports")
        env = state.get("env") or {}
        return {
            "mode": session.info.get("mode"),
            "project_root": state.get("project_root"),
            "studio_version": state.get("version"),
            "revision": state.get("revision"),
            "api_prefix": env.get("api_prefix"),
            "counts": env.get("counts"),
            "reports": {
                "pending": reports.get("pending", 0),
                "total": len(reports.get("reports") or []),
            },
        }

    @tool
    def studio_graph(
        kind: str = "", query: str = "", planned_only: bool = False, limit: int = 80
    ) -> dict:
        """Узлы бэкенда (endpoint, scenario, io, model, test, nested) в сжатом виде.

        Фильтры: kind, query (подстрока в имени, id, домене, описании), planned_only.
        """
        graph = session.need().get("/api/graph")
        needle = query.strip().lower()
        nodes = []
        for node in graph.get("nodes") or []:
            if kind and node.get("kind") != kind:
                continue
            if planned_only and not node.get("planned"):
                continue
            haystack = " ".join(
                str(node.get(key) or "") for key in ("id", "label", "domain", "description")
            ).lower()
            if needle and needle not in haystack:
                continue
            nodes.append(
                {
                    "id": node["id"],
                    "kind": node.get("kind"),
                    "label": node.get("label"),
                    "planned": bool(node.get("planned")),
                    "domain": node.get("domain") or "",
                    "description": _short(node.get("description")),
                    "created_by": (node.get("details") or {}).get("created_by", ""),
                }
            )
        return {"total": len(nodes), "shown": min(len(nodes), limit), "nodes": nodes[:limit]}

    @tool
    def studio_node(node_id: str) -> dict:
        """Один узел бэкенда целиком (описание, критерии, файл) и его связи."""
        graph = session.need().get("/api/graph")
        node = next((n for n in graph.get("nodes") or [] if n.get("id") == node_id), None)
        if node is None:
            raise StudioError("node_not_found", f"Узел {node_id} не найден: см. studio_graph")
        edges = [
            {"from": e["from_node"], "to": e["to_node"], "kind": e.get("kind")}
            for e in graph.get("edges") or []
            if node_id in (e.get("from_node"), e.get("to_node"))
        ]
        keys = ("id", "kind", "label", "planned", "domain", "description", "acceptance",
                "file_path", "line")
        return {**{key: node.get(key) for key in keys}, "edges": edges}

    @tool
    def studio_frontend(page: str = "") -> dict:
        """Фронтенд: страницы, блоки, вызываемые эндпоинты и их статус в бэкенде."""
        graph = session.need().get("/api/frontend/graph")
        if not graph.get("present"):
            return {"present": False, "note": "каталога frontend/ в проекте нет"}
        needle = page.strip().lower()
        pages = []
        for item in graph.get("pages") or []:
            if needle and needle not in str(item.get("name") or "").lower():
                continue
            pages.append(
                {
                    "id": item["id"],
                    "name": item.get("name"),
                    "planned": bool(item.get("planned")),
                    "route": item.get("route") or "",
                    "domain": item.get("domain") or "",
                    "calls": item.get("calls") or [],
                    "created_by": item.get("created_by") or "",
                    "rules": len(item.get("rules") or []),
                    "blocks": [
                        {
                            "id": block["id"],
                            "name": block.get("name"),
                            "planned": bool(block.get("planned")),
                            "calls": block.get("calls") or [],
                        }
                        for block in item.get("blocks") or []
                    ],
                }
            )
        links = graph.get("links") or {}
        return {
            "present": True,
            "pages": pages,
            "links": {
                "ok": links.get("ok", 0),
                "missing": [
                    row["call"] for row in links.get("rows") or [] if row["status"] != "ok"
                ],
            },
            "violations": len(graph.get("violations") or []),
        }

    @tool
    def studio_ideas() -> dict:
        """Доска идей: идеи (кратко) и ревизия доски."""
        board = session.need().get("/api/ideas")
        return {
            "revision": board.get("revision", 0),
            "ideas": [
                {
                    "id": idea.get("id"),
                    "title": idea.get("title"),
                    "description": _short(idea.get("description")),
                    "created_by": idea.get("created_by") or "",
                    "scenarios": len(idea.get("scenarios") or []),
                    "pages": len(idea.get("pages") or []),
                }
                for idea in board.get("ideas") or []
                if isinstance(idea, dict)
            ],
        }

    # ------------------------------------------------- режим planning: запись

    @tool
    def studio_idea_add(
        title: str,
        description: str = "",
        scenarios: list[dict] | None = None,
        pages: list[dict] | None = None,
    ) -> dict:
        """Добавить идею на доску (режим planning). Идеи человека не меняются.

        scenarios — сценарии бэкенда: {title, slug, description, acceptance[], domain,
        http_method, path, uses_db, with_test}. pages — страницы фронтенда: {title, name,
        description, acceptance[], domain, route, calls[«GET /path»], blocks[{name,
        description, acceptance[], calls[]}]}. Человек сам разберёт ответ на вкладке «Идеи».
        """
        client = session.need()
        answer = {"scenarios": scenarios or [], "pages": pages or []}
        text = json.dumps(answer, ensure_ascii=False)
        parsed = client.post("/api/decompose/parse", {"text": text})
        idea_id = "agent-" + uuid.uuid4().hex[:10]
        idea = {
            "id": idea_id,
            "title": title,
            "description": description,
            "jsonText": json.dumps(
                {"scenarios": parsed["scenarios"], "pages": parsed["pages"]},
                ensure_ascii=False,
                indent=2,
            ),
        }
        board = client.get("/api/ideas")
        for attempt in range(2):
            body = {
                "version": 1,
                "activeId": board.get("activeId") or idea_id,
                "revision": board.get("revision", 0),
                "ideas": [*(board.get("ideas") or []), idea],
            }
            try:
                saved = client.post("/api/ideas", body)
                break
            except StudioError as exc:
                if exc.code != "ideas_conflict" or attempt:
                    raise
                board = client.get("/api/ideas")  # доску изменили: повторить со свежей
        return {
            "idea_id": idea_id,
            "revision": saved.get("revision"),
            "scenarios": len(parsed["scenarios"]),
            "pages": len(parsed["pages"]),
            "warnings": parsed.get("warnings") or [],
            "next": "Человек откроет вкладку «Идеи» и нажмёт «Разобрать ответ».",
        }

    @tool
    def studio_node_add(
        kind: str,
        name: str,
        description: str = "",
        acceptance: list[str] | None = None,
        domain: str = "",
        http_method: str = "",
        path: str = "",
        parent_id: str = "",
        edge_kind: str = "",
    ) -> dict:
        """Плановый узел бэкенда на карте (режим planning).

        Типичная цепочка: endpoint (http_method, path), затем scenario с parent_id эндпоинта и
        edge_kind=dispatch. Правка и удаление — только своих узлов.
        """
        body = {
            "kind": kind,
            "name": name,
            "description": description,
            "acceptance": acceptance or [],
            "domain": domain,
            "http_method": http_method,
            "path": path,
            "parent_id": parent_id,
            "edge_kind": edge_kind,
        }
        result = session.need().post("/api/metadata/graph-node", body)
        return {"node_id": result.get("node_id"), "kind": kind, "name": name}

    @tool
    def studio_node_delete(node_id: str) -> dict:
        """Удалить свой плановый узел вместе с веткой (чужие узлы студия не даст удалить)."""
        session.need().post("/api/metadata/graph-node/delete", {"node_id": node_id})
        return {"deleted": node_id}

    def _meta(payload: dict) -> dict:
        return session.need().post("/api/frontend/meta", payload)

    @tool
    def studio_page_add(
        name: str,
        description: str = "",
        route: str = "",
        domain: str = "",
        acceptance: list[str] | None = None,
        calls: list[str] | None = None,
        blocks: list[dict] | None = None,
    ) -> dict:
        """Плановая страница фронтенда с блоками (режим planning).

        name — папка страницы в kebab-case; calls — вызываемые эндпоинты «GET /path»;
        blocks — [{name (PascalCase), description, acceptance[], calls[]}]. В ответе —
        статус каждого вызова в бэкенде (ok или missing).
        """
        page_id = f"page-plan:{uuid.uuid4()}"
        response = _meta(
            {
                "action": "save_page",
                "id": page_id,
                "plannedNew": True,
                "name": name,
                "description": description,
                "acceptance": acceptance or [],
                "domain": domain,
                "route": route,
                "calls": calls or [],
            }
        )
        block_ids = []
        for block in blocks or []:
            block_id = f"block-plan:{uuid.uuid4()}"
            response = _meta(
                {
                    "action": "save_block",
                    "pageId": page_id,
                    "id": block_id,
                    "name": block.get("name", ""),
                    "description": block.get("description", ""),
                    "acceptance": block.get("acceptance") or [],
                    "calls": block.get("calls") or [],
                }
            )
            block_ids.append(block_id)
        return {
            "page_id": page_id,
            "block_ids": block_ids,
            "links": _links_for(response, {page_id, *block_ids}),
        }

    @tool
    def studio_block_add(
        page_id: str,
        name: str,
        description: str = "",
        acceptance: list[str] | None = None,
        calls: list[str] | None = None,
    ) -> dict:
        """Добавить плановый блок на страницу (в том числе созданную человеком)."""
        block_id = f"block-plan:{uuid.uuid4()}"
        response = _meta(
            {
                "action": "save_block",
                "pageId": page_id,
                "id": block_id,
                "name": name,
                "description": description,
                "acceptance": acceptance or [],
                "calls": calls or [],
            }
        )
        return {"block_id": block_id, "links": _links_for(response, {block_id})}

    @tool
    def studio_rule_add(
        page_id: str,
        name: str,
        description: str = "",
        acceptance: list[str] | None = None,
        block_id: str = "",
    ) -> dict:
        """Добавить новое правило странице или блоку (существующие правила не правятся)."""
        payload = {
            "action": "save_rule",
            "pageId": page_id,
            "name": name,
            "description": description,
            "acceptance": acceptance or [],
        }
        if block_id:
            payload["blockId"] = block_id
        _meta(payload)
        return {"added": name, "page_id": page_id, "block_id": block_id}

    @tool
    def studio_front_delete(page_id: str, block_id: str = "") -> dict:
        """Удалить свою плановую страницу или блок (page_id, и block_id для блока)."""
        _meta(
            {
                "action": "delete_planned",
                "kind": "block" if block_id else "page",
                "pageId": page_id,
                "blockId": block_id,
            }
        )
        return {"deleted": block_id or page_id}

    # ----------------------------------------------- режим implement: работа

    @tool
    def studio_brief(part: str = "backend") -> dict:
        """ТЗ, которое человек сохранил (backend) или собрал (frontend), и его задачи."""
        part = _part(part)
        path = "/api/brief/last" if part == "backend" else "/api/frontend/brief/last"
        brief = session.need().get(path)
        return {
            "part": part,
            "brief_id": brief.get("brief_id"),
            "tasks": brief.get("tasks") or [],
            "markdown": brief.get("markdown"),
        }

    @tool
    def studio_verify(part: str = "backend") -> dict:
        """Проверить по коду, выполнено ли последнее ТЗ: статус и что осталось сделать."""
        part = _part(part)
        path = "/api/verify?brief=last" if part == "backend" else "/api/frontend/verify?brief=last"
        report = session.need().get(path)
        return {
            "part": part,
            "status": report.get("status"),
            "done": report.get("done"),
            "total": report.get("total"),
            "items": [
                {
                    "number": item.get("number"),
                    "kind": item.get("kind"),
                    "name": item.get("anchor") or item.get("name") or item.get("slug"),
                    "status": item.get("status"),
                    "message": item.get("message", ""),
                }
                for item in report.get("items") or []
            ],
        }

    @tool
    def studio_report(
        part: str,
        updates: list[dict],
        not_synced: list[dict] | None = None,
        report: dict | None = None,
    ) -> dict:
        """Отправить итоговый отчёт человеку (режим implement); применяет его только он.

        updates — [{id, description, acceptance[]}] (id узла из ТЗ; для планов бэкенда
        {plan, layer, description, acceptance[]}); not_synced — [{id, reason}]; report —
        {outside_changes[], tests, questions[], decisions[]}. В ответе: что студия
        отклонила (rejected) и почему — исправьте и отправьте заново.
        """
        part = _part(part)
        body = {
            "part": part,
            "json": {"updates": updates, "not_synced": not_synced or [], "report": report or {}},
        }
        result = session.need().post("/api/agent/report", body)
        return {
            "report_id": result.get("id"),
            "status": result.get("status"),
            "counts": result.get("counts"),
            "rejected": result.get("rejected") or [],
            "warnings": result.get("warnings") or [],
            "next": "Отчёт ждёт человека в «Отчёты агента» (кнопка ✉); сам он не применяется.",
        }

    @tool
    def studio_reports() -> dict:
        """Ваши отчёты и решения человека (pending, accepted, rejected)."""
        data = session.need().get("/api/agent/reports")
        return {
            "pending": data.get("pending", 0),
            "reports": [
                {
                    "id": item.get("id"),
                    "part": item.get("part"),
                    "status": item.get("status"),
                    "counts": item.get("counts"),
                }
                for item in data.get("reports") or []
            ],
        }

    server.studio_tools = registry
    return server


__all__ = ["INSTRUCTIONS", "McpUnavailable", "Session", "create_server"]
