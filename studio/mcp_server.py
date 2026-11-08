"""MCP-сервер студии: инструменты для AI-агента вместо «сырого» HTTP.

Запуск: ``studio mcp`` (stdio). Сервер ничего не решает сам: он находит уже запущенную
студию проекта (``studio_connect`` без параметров читает ``.studio/server.json``) и
пересылает вызовы в её HTTP API с токеном нужного режима. Права, этапы дорожной карты и
журнал остаются на стороне студии, поэтому запрещённое действие вернёт отказ студии.

Пакет ``mcp`` входит в обязательные зависимости; поддерживаются версии с
``mcp.server.mcpserver.MCPServer`` (2.x) и ``mcp.server.fastmcp.FastMCP`` (1.x).
"""

from __future__ import annotations

import functools
import sys
from typing import Any

from studio.agent import brief_parts
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
            "Не установлен пакет mcp (обязательная зависимость pacage-studio). "
            f'Исправление: "{sys.executable}" -m pip install --upgrade "mcp>=1.2"'
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


def _front_node(graph: dict, node_id: str) -> dict:
    """Страница или блок фронтенда со связями (вызовами эндпоинтов) и описанием."""
    for page in graph.get("pages") or []:
        if page.get("id") == node_id:
            return {
                "id": node_id, "kind": "page", "name": page.get("name"),
                "route": page.get("route") or "", "planned": bool(page.get("planned")),
                "description": page.get("description") or "",
                "acceptance": page.get("acceptance") or [], "calls": page.get("calls") or [],
                "calls_in_code": page.get("inferred_calls") or [],
                "file": page.get("file") or "",
                "blocks": [
                    {"id": b.get("id"), "name": b.get("name"), "planned": bool(b.get("planned"))}
                    for b in page.get("blocks") or []
                ],
            }
        for block in page.get("blocks") or []:
            if block.get("id") == node_id:
                return {
                    "id": node_id, "kind": "block", "name": block.get("name"),
                    "page": page.get("name"), "planned": bool(block.get("planned")),
                    "description": block.get("description") or "",
                    "acceptance": block.get("acceptance") or [], "calls": block.get("calls") or [],
                    "calls_in_code": block.get("inferred_calls") or [],
                    "file": block.get("file") or "",
                }
    raise StudioError(
        "node_not_found", f"Страница или блок {node_id} не найдены: см. studio_frontend"
    )


def _scenario_view(card: dict) -> dict:
    source = card.get("source") or {}
    return {
        "id": card.get("id"),
        "slug": source.get("slug", ""),
        "title": card.get("title"),
        "route": f"{source.get('http_method', '')} {source.get('path', '')}".strip(),
        "domain": card.get("domain") or "",
        "description": card.get("description") or "",
        "acceptance": card.get("acceptance") or [],
        "decision": card.get("decision") or "",
        "plan": card.get("planSlug") or "",
    }


def _page_view(card: dict) -> dict:
    return {
        "id": card.get("id"),
        "name": card.get("name"),
        "extends": card.get("extends") or "",
        "title": card.get("title"),
        "route": card.get("route") or "",
        "calls": card.get("calls") or [],
        "blocks": [
            {
                "name": b.get("name"),
                "description": b.get("description") or "",
                "acceptance": b.get("acceptance") or [],
                "calls": b.get("calls") or [],
            }
            for b in card.get("blocks") or []
            if isinstance(b, dict)
        ],
        "decision": card.get("decision") or "",
    }


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
        mine: list[dict] = []
        if flow.get("stage") in ("ideas", "review", "confirm"):
            for idea in client.get("/api/ideas").get("ideas") or []:
                for card in [*(idea.get("scenarios") or []), *(idea.get("pages") or [])]:
                    if isinstance(card, dict) and card.get("decision"):
                        source = card.get("source") or {}
                        mine.append(
                            {
                                "id": card.get("id"),
                                "slug": source.get("slug") or card.get("name") or "",
                                "title": card.get("title"),
                                "decision": card.get("decision"),
                            }
                        )
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
            "my_cards": mine[:40],
            "changed": changed,
        }

    @tool
    def studio_idea_withdraw(idea_id: str) -> dict:
        """Отозвать СВОЮ идею (например, заменённую или присланную по ошибке).

        Возможно, пока по ней нет принятых и перенесённых на карту карточек; идеи человека и
        решённые карточки не отзываются. Потом можно прислать исправленную studio_idea_add.
        """
        return session.need().post("/api/workflow/withdraw", {"idea_id": idea_id}, mode="plan")

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
        """Узлы бэкенда в сжатом виде: эндпоинты и сценарии первыми, потом остальные.

        kind — endpoint, scenario, model, test, io или nested. query — подстрока в имени, id,
        домене или описании (для эндпоинта это и его путь: query="/news"). planned_only —
        только плановые. Чтобы увидеть существующие маршруты, начни с kind="endpoint".
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
        order = {"endpoint": 0, "scenario": 1, "model": 2, "test": 3, "io": 4}
        nodes.sort(key=lambda item: order.get(item["kind"], 9))
        return {"total": len(nodes), "shown": min(len(nodes), limit), "nodes": nodes[:limit]}

    @tool
    def studio_node(node_id: str) -> dict:
        """Один узел бэкенда целиком (описание, критерии, файл) и его связи."""
        client = session.need()
        if node_id.startswith(("page:", "page-plan:", "block:", "block-plan:")):
            return _front_node(client.get("/api/frontend/graph"), node_id)
        graph = client.get("/api/graph")
        node = next((n for n in graph.get("nodes") or [] if n.get("id") == node_id), None)
        if node is None:
            raise StudioError(
                "node_not_found",
                f"Узел {node_id} не найден: бэкенд — studio_graph, страницы и блоки — "
                "studio_frontend",
            )
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
    def studio_ideas(idea_id: str = "") -> dict:
        """Доска идей: идеи кратко (со slug сценариев) или одна идея целиком (idea_id).

        Для идеи целиком — все сценарии и страницы с критериями и решением человека
        (decision: pending, accepted, rejected). Так проверяют свои записи и не дублируют чужие.
        """
        board = session.need().get("/api/ideas")
        ideas = [idea for idea in board.get("ideas") or [] if isinstance(idea, dict)]
        if idea_id:
            idea = next((i for i in ideas if i.get("id") == idea_id), None)
            if idea is None:
                raise StudioError("idea_not_found", f"Идеи {idea_id} нет: см. studio_ideas()")
            return {
                "revision": board.get("revision", 0),
                "id": idea.get("id"),
                "title": idea.get("title"),
                "description": idea.get("description"),
                "scenarios": [
                    _scenario_view(c) for c in idea.get("scenarios") or [] if isinstance(c, dict)
                ],
                "pages": [_page_view(c) for c in idea.get("pages") or [] if isinstance(c, dict)],
            }
        return {
            "revision": board.get("revision", 0),
            "ideas": [
                {
                    "id": idea.get("id"),
                    "title": idea.get("title"),
                    "description": _short(idea.get("description")),
                    "created_by": idea.get("created_by") or "",
                    "cycle": idea.get("cycle"),
                    "scenarios": len(idea.get("scenarios") or []),
                    "pages": len(idea.get("pages") or []),
                    "decisions": sorted(
                        {
                            c.get("decision") or "—"
                            for c in [*(idea.get("scenarios") or []), *(idea.get("pages") or [])]
                            if isinstance(c, dict)
                        }
                    ),
                    "slugs": [
                        (c.get("source") or {}).get("slug", "")
                        for c in idea.get("scenarios") or []
                        if isinstance(c, dict)
                    ],
                }
                for idea in ideas
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
        acceptance[], domain, route (обязателен у новой страницы), calls[«GET /path», без
        префикса API], blocks[{name, description, acceptance[], calls[]}]}. Чтобы добавить
        блоки на СУЩЕСТВУЮЩУЮ страницу, вместо name укажи extends="<имя страницы>" и blocks.
        Студия проверяет slug, конфликты и дубли сразу: при ошибке исправь и отправь снова.
        Можно вызывать несколько раз; в конце — studio_ideas_ready.
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
    def studio_brief(
        part: str = "backend", task: int = 0, section: str = "", offset: int = 0
    ) -> dict:
        """ТЗ по частям (оно бывает на десятки тысяч символов и целиком не помещается).

        Без параметров — оглавление: задачи (номер, имя, размер) и разделы. Дальше читай
        нужное: task=N — одна задача целиком; section="2" — раздел (2 и 3 — правила
        работы, один раз в начале). offset — продолжение, если ответ обрезан
        (next_offset). part — backend или frontend.
        """
        part = _part(part)
        path = "/api/brief/last" if part == "backend" else "/api/frontend/brief/last"
        brief = session.need().get(path)
        markdown = str(brief.get("markdown") or "")
        base = {"part": part, "brief_id": brief.get("brief_id")}
        if not task and not section:
            return {
                **base,
                **brief_parts.outline(markdown, brief.get("tasks") or []),
                "next": "Читай по частям: studio_brief(task=N) — задача, "
                'studio_brief(section="2") — правила работы.',
            }
        parts = brief_parts.split_brief(markdown)
        if task:
            found = next((t for t in parts["tasks"] if t["number"] == int(task)), None)
            if found is None:
                raise StudioError("task_not_found", f"В ТЗ нет задачи T{task}: см. оглавление")
            return {**base, "task": int(task), **brief_parts.page(found["text"], offset)}
        found = next((s for s in parts["sections"] if s["key"] == str(section)), None)
        if found is None:
            raise StudioError("section_not_found", f"В ТЗ нет раздела {section}: см. оглавление")
        return {**base, "section": str(section), **brief_parts.page(found["text"], offset)}

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
            "note": "Проверка по структуре кода: есть ли слои, связи и тесты с нужными именами. "
            "Критерии приёмки и поведение она не проверяет — это делают только тесты проекта.",
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
    def studio_tests() -> dict:
        """Итог последнего прогона тестов проекта, который сделала студия (независимо от тебя).

        Студия запускает тесты по кнопке человека или на этапе «Отчёт»; state=none — прогонов ещё
        не было. failing_plans — планы, у которых упал тест. Это то, что видит человек в отчёте.
        """
        data = session.need().get("/api/tests")
        return {
            "state": data.get("state"),
            "finished_at": data.get("finished_at"),
            "total": data.get("total", 0),
            "passed": data.get("passed", 0),
            "failed": data.get("failed", 0),
            "errors": data.get("errors", 0),
            "skipped": data.get("skipped", 0),
            "failures": (data.get("failures") or [])[:20],
            "parts": data.get("parts") or [],
            "failing_plans": data.get("plans") or {},
            "message": data.get("message") or "",
        }

    @tool
    def studio_doctor(strict: bool = False, limit: int = 40) -> dict:
        """Диагностика проекта (как `studio doctor [--strict]`): ошибки и предупреждения.

        ok=false, если есть ошибки (а при strict=true — и предупреждения). Предупреждения,
        которые были в проекте ещё до передачи ТЗ, помечены new=false: ok_for_changes говорит,
        чисто ли после ТЗ, то есть нет ли НОВЫХ проблем — чужие давние исправлять не нужно.
        """
        client = session.need()
        found = client.get("/api/graph").get("diagnostics") or []
        old = {tuple(k) for k in (client.get("/api/baseline").get("keys") or [])}

        def fresh(item: dict) -> bool:
            return (item.get("code"), item.get("file") or "", item.get("message")) not in old

        errors = [d for d in found if d.get("level") == "error"]
        warnings = [d for d in found if d.get("level") == "warning"]
        new_errors = [d for d in errors if fresh(d)]
        new_warnings = [d for d in warnings if fresh(d)]
        shown = [*new_errors, *new_warnings, *[d for d in errors + warnings if not fresh(d)]]
        old_count = len(errors) + len(warnings) - len(new_errors) - len(new_warnings)
        if new_errors or new_warnings:
            verdict = f"новых проблем: {len(new_errors)} ошибок, {len(new_warnings)} предупреждений"
        else:
            verdict = "новых проблем нет" + (f"; давних предупреждений и ошибок: {old_count}"
                                            if old_count else "")
        return {
            "verdict": verdict,
            "ok": not errors and not (strict and warnings),
            "ok_for_changes": not new_errors and not (strict and new_warnings),
            "errors": len(errors),
            "warnings": len(warnings),
            "new_errors": len(new_errors),
            "new_warnings": len(new_warnings),
            "info": len(found) - len(errors) - len(warnings),
            "items": [
                {
                    "level": d.get("level"),
                    "code": d.get("code"),
                    "message": d.get("message"),
                    "file": d.get("file") or "",
                    "line": d.get("line") or 0,
                    "new": fresh(d),
                }
                for d in shown[: max(0, int(limit))]
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
            "next": "Отчёт у человека. Если тебя запустила студия — заверши работу; "
            "иначе вернись к studio_workflow.",
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

    @tool
    def studio_cards_decide(decisions: list[dict]) -> dict:
        """Контролёр: принять или отклонить карточки идей (если студия запустила тебя для этого).

        decisions — [{id, decision: "accepted"|"rejected", reason}]; id берётся из запроса
        студии или studio_ideas. Можно вызывать частями. Карточки с решением человека не меняются.
        """
        result = session.need().post("/api/control/decide", {"decisions": decisions}, mode="plan")
        return {**result, "next": "Когда решения по всем карточкам переданы, заверши работу."}

    @tool
    def studio_check_result(ok: bool, remarks: list[str] | None = None) -> dict:
        """Контролёр: итог проверки или тестов сценария (только если тебя запустила студия).

        ok=true — сценарий проверен и замечаний нет; ok=false — в remarks перечисли
        конкретные замечания (что не так и где): реализующий агент исправит их. Вызови один
        раз в конце работы, затем заверши её.
        """
        result = session.need().post(
            "/api/control/result", {"ok": ok, "remarks": remarks or []}, mode="implement"
        )
        return {**result, "next": "Итог записан. Заверши работу."}

    @tool
    def studio_ask(question: str, options: list[str] | None = None) -> dict:
        """Спросить человека, когда без его ответа продолжать нельзя.

        Если тебя запустила студия: после вызова заверши работу. Студия покажет вопрос
        человеку и сама запустит тебя снова (с тем же контекстом) с его ответом. Не задавай
        вопросов, на которые можно ответить, изучив проект. options — готовые варианты ответа.
        """
        client = session.need()
        stage = (client.get("/api/workflow") or {}).get("stage")
        mode = "implement" if stage in ("select", "implement", "report") else "plan"
        client.post("/api/agent/ask", {"question": question, "options": options or []}, mode=mode)
        return {
            "asked": True,
            "next": "Вопрос у человека. Заверши работу: студия вернёт тебя с ответом.",
        }

    server.studio_tools = registry
    return server


__all__ = ["McpUnavailable", "Session", "create_server"]
