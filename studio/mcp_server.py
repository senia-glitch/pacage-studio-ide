"""MCP-сервер студии: инструменты для AI-агента вместо «сырого» HTTP.

Запуск: ``studio mcp`` (stdio). Сервер ничего не решает сам: он находит уже запущенную
студию проекта (``studio_connect`` без параметров читает ``.studio/server.json``) и
пересылает вызовы в её HTTP API с токеном нужного режима. Права, этапы дорожной карты и
журнал остаются на стороне студии, поэтому запрещённое действие вернёт отказ студии.

Нужен пакет ``mcp`` (``pip install "pacage-studio[mcp]"``); поддерживаются версии с
``mcp.server.mcpserver.MCPServer`` (2.x) и ``mcp.server.fastmcp.FastMCP`` (1.x).
"""

from __future__ import annotations

import functools
from typing import Any

from studio.agent.client import StudioClient, StudioError, parse_connection
from studio.agent.guide import load_guide


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
        self.rev = 0  # ревизия дорожной карты, которую агент видел в последний раз

    def need(self) -> StudioClient:
        if self.client is None:
            raise StudioError(
                "not_connected",
                "Нет подключения к студии: вызовите studio_connect (без параметров).",
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
    server = _server_class()("pacage-studio", instructions=load_guide())

    registry: dict[str, Any] = {}

    def tool(func):
        wrapped = _guarded(func)
        registry[func.__name__] = wrapped  # для тестов и встраивания: вызов без протокола
        return server.tool()(wrapped)

    # ------------------------------------------------------------ подключение

    @tool
    def studio_connect(connection: str = "", url: str = "", token: str = "") -> dict:
        """Подключиться к запущенной студии. Обычно — без параметров.

        Без параметров студия находится сама (файл .studio/server.json в проекте): ничего
        копировать человеку не нужно. Запасной путь для особых случаев — строка из окна
        «AI-агент» целиком (connection) либо url и token.
        """
        fields = parse_connection(connection)
        url = url or fields.get("url", "")
        token = token or fields.get("token", "")
        if url or token:
            if not url or not token:
                raise StudioError(
                    "bad_connection",
                    "Передайте строку подключения целиком (connection) или url и token.",
                )
            client = StudioClient(url, token)
            client.mode = fields.get("mode") or "plan"
        else:
            client = StudioClient.discover()
        info = client.get("/api/agent")
        flow = client.get("/api/workflow")
        session.client, session.info, session.rev = client, info, 0
        return {
            "connected": True,
            "studio_version": info.get("studio_version"),
            "project_root": info.get("project_root"),
            "stage": flow.get("stage"),
            "next": "Вызови studio_workflow: он скажет, чего ждёт человек и что делать тебе.",
        }

    @tool
    def studio_workflow(wait_seconds: int = 20) -> dict:
        """Дорожная карта: на каком этапе человек и что делать агенту прямо сейчас.

        Главный инструмент цикла. wait_seconds (0–25) — подождать смены этапа, если делать
        пока нечего. Поле action: wait (ждём человека — вызови снова), propose (присылай
        идеи), implement (реализуй выбранное ТЗ). Если несколько минут подряд action=wait,
        скажи человеку, чего ждёшь, и остановись.
        """
        client = session.need()
        wait = max(0, min(int(wait_seconds), 25))
        path = "/api/workflow"
        if wait and session.rev:
            path += f"?rev={session.rev}&wait={wait}"
        flow = client.get(path)
        changed = flow.get("revision") != session.rev
        session.rev = flow.get("revision") or 0
        hint = flow.get("hint") or {}
        cards = flow.get("cards") or {}
        return {
            "stage": flow.get("stage"),
            "stage_title": next(
                (s["title"] for s in flow.get("steps") or [] if s["id"] == flow.get("stage")), ""
            ),
            "path": flow.get("mode") or None,
            "cycle": flow.get("cycle"),
            "task": flow.get("task") or "",
            "action": hint.get("action"),
            "instruction": hint.get("text"),
            "cards": {k: cards.get(k, 0) for k in ("pending", "accepted", "rejected")},
            "selected": flow.get("selected"),
            "briefs": flow.get("briefs"),
            "changed": changed,
        }

    @tool
    def studio_ideas_ready(note: str = "") -> dict:
        """Сообщить, что идеи готовы: человек переходит к валидации карточек.

        Вызывай после последней studio_idea_add (этап «Идеи агента»). note не используется
        студией — только для следа в журнале.
        """
        result = session.need().post("/api/workflow/ideas-ready", {}, mode="plan")
        return {**result, "next": "Человек валидирует карточки. Вернись к studio_workflow."}

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

    # ------------------------------------------------------- этап «Идеи агента»

    @tool
    def studio_idea_add(
        title: str,
        description: str = "",
        scenarios: list[dict] | None = None,
        pages: list[dict] | None = None,
    ) -> dict:
        """Добавить идею с готовыми сценариями: каждый станет карточкой для свайпа человека.

        scenarios — сценарии бэкенда: {title, slug (snake_case, латиница — обязателен как
        имя будущего кода), description, acceptance[], domain, http_method, path, uses_db,
        with_test}. pages — страницы фронтенда: {title, name (kebab-case), description,
        acceptance[], domain, route, calls[«GET /path»], blocks[{name, description,
        acceptance[], calls[]}]}. Студия проверяет slug и конфликты сразу: при ошибке
        исправь и отправь снова. Можно вызывать несколько раз; в конце — studio_ideas_ready.
        """
        body = {
            "title": title,
            "description": description,
            "scenarios": scenarios or [],
            "pages": pages or [],
        }
        result = session.need().post("/api/workflow/propose", body, mode="plan")
        return {
            **result,
            "next": "Когда идей достаточно, вызови studio_ideas_ready.",
        }

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
        summary: str,
        part: str = "backend",
        updates: list[dict] | None = None,
        not_synced: list[dict] | None = None,
        report: dict | None = None,
    ) -> dict:
        """Отправить человеку итоговый отчёт: завершает реализацию (этап «Отчёт»).

        summary — понятный человеку текст: что сделано, что проверено (тесты, studio_verify),
        что осталось или потребует решения. updates — необязательные описания реализованных
        узлов [{id, description, acceptance[]}] (для планов бэкенда {plan, layer, ...});
        not_synced — [{id, reason}]; report — {outside_changes[], tests, questions[],
        decisions[]}. В ответе rejected перечисляет отклонённые записи updates.
        """
        part = _part(part)
        body = {
            "part": part,
            "summary": summary,
            "json": {
                "updates": updates or [],
                "not_synced": not_synced or [],
                "report": report or {},
            },
        }
        result = session.need().post("/api/agent/report", body, mode="implement")
        return {
            "report_id": result.get("id"),
            "status": result.get("status"),
            "counts": result.get("counts"),
            "rejected": result.get("rejected") or [],
            "warnings": result.get("warnings") or [],
            "next": "Человек читает отчёт в студии. Вернись к studio_workflow.",
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


__all__ = ["McpUnavailable", "Session", "create_server"]
