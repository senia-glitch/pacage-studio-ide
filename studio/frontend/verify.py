"""Проверка выполнения ТЗ фронтенда: только факты из кода, как ``studio verify`` бэкенда.

Задача ТЗ считается выполненной по тому, что сканер видит в ``frontend/`` сейчас:

- плановая страница — в коде есть страница-папка с тем же именем (и совпадает
  маршрут, если он задан);
- плановый блок — на нужной странице используется компонент с тем же именем;
- убрать блок или страницу — их больше нет в коде;
- правило — отмечено «Реализовано» (код правило проверить не может);
- нарушение — в сканере больше нет такой пары «код + файл».

Объявленные вызовы эндпоинтов (``calls``) должны находить эндпоинт в бэкенде,
иначе задача «частично».
"""

from __future__ import annotations

from studio.agent.verify import STATUS_DONE, STATUS_NOT_DONE, STATUS_PARTIAL

KIND_RU = {
    "page": "страница",
    "block": "блок",
    "page_deletion": "удаление страницы",
    "block_deletion": "удаление блока",
    "rule": "правило",
    "violation": "нарушение",
}


def _fact_pages(front: dict) -> list[dict]:
    """Страницы, которые сканер реально нашёл в коде (есть файл)."""
    pages = front.get("pages") or []
    return [page for page in pages if not page.get("planned") and page.get("file")]


def _fact_blocks(page: dict) -> list[dict]:
    blocks = page.get("blocks") or []
    return [block for block in blocks if not block.get("planned") and block.get("file")]


def _link_problems(task: dict, links: dict | None) -> list[str]:
    """Вызовы узла, для которых в бэкенде нет эндпоинта."""
    if not links:
        return []
    return [
        f"эндпоинта {row['call']} нет в бэкенде"
        for row in links.get("rows") or []
        if row["node"] == task.get("id") and row["status"] != "ok"
    ]


def _code_note(task: dict, item: dict | None) -> str:
    """Заметка: объявленный вызов не найден в коде узла (поиск по тексту, не приговор)."""
    declared = list(task.get("calls") or [])
    if item is None or not declared:
        return ""
    found = set(item.get("inferred_calls") or [])
    absent = [call for call in declared if call not in found]
    if not absent:
        return ""
    return f"; вызов {', '.join(absent)} в коде не найден (поиск по тексту)"


def _result(task: dict, status: str, message: str) -> dict:
    return {
        "number": task.get("number"),
        "kind": task.get("kind"),
        "name": task.get("name") or task.get("code") or "",
        "id": task.get("id") or "",
        "status": status,
        "message": message,
    }


def _verify_page(task: dict, front: dict, links: dict | None) -> dict:
    page = next((p for p in _fact_pages(front) if p.get("name") == task.get("name")), None)
    if page is None:
        return _result(task, STATUS_NOT_DONE, "страницы с таким именем в коде нет")
    problems = []
    route = str(task.get("route") or "")
    if route:
        actual = str(page.get("route") or "")
        if not actual:
            problems.append(f"маршрут {route} не найден в router.js")
        elif actual != route:
            problems.append(f"маршрут {actual}, ожидался {route}")
    problems += _link_problems(task, links)
    if problems:
        return _result(task, STATUS_PARTIAL, "; ".join(problems))
    return _result(task, STATUS_DONE, "страница есть в коде" + _code_note(task, page))


def _page_of_block(task: dict, front: dict) -> dict | None:
    """Страница кода, на которой должен появиться плановый блок."""
    pages = _fact_pages(front)
    if task.get("page_planned"):
        return next((p for p in pages if p.get("name") == task.get("page_name")), None)
    return next((p for p in pages if p.get("id") == task.get("page_id")), None)


def _verify_block(task: dict, front: dict, links: dict | None) -> dict:
    page = _page_of_block(task, front)
    if page is None:
        return _result(task, STATUS_NOT_DONE, "страницы блока в коде нет")
    block = next(
        (b for b in _fact_blocks(page) if b.get("name") == task.get("name")), None
    )
    if block is None:
        return _result(
            task, STATUS_NOT_DONE, f"компонент {task.get('name')} не используется на странице"
        )
    problems = _link_problems(task, links)
    if problems:
        return _result(task, STATUS_PARTIAL, "; ".join(problems))
    return _result(task, STATUS_DONE, "блок есть на странице" + _code_note(task, block))


def _verify_block_deletion(task: dict, front: dict) -> dict:
    for page in _fact_pages(front):
        if page.get("id") == task.get("page_id") and any(
            block.get("id") == task.get("id") for block in _fact_blocks(page)
        ):
            return _result(task, STATUS_NOT_DONE, "блок всё ещё используется на странице")
    return _result(task, STATUS_DONE, "блока на странице больше нет")


def _verify_page_deletion(task: dict, front: dict) -> dict:
    if any(page.get("id") == task.get("id") for page in _fact_pages(front)):
        return _result(task, STATUS_NOT_DONE, "страница всё ещё есть в коде")
    return _result(task, STATUS_DONE, "страницы в коде больше нет")


def _verify_rule(task: dict, front: dict) -> dict:
    for page in front.get("pages") or []:
        rules = list(page.get("rules") or [])
        for block in page.get("blocks") or []:
            rules += block.get("rules") or []
        for rule in rules:
            if str(rule.get("id")) == task.get("id"):
                if rule.get("done"):
                    return _result(task, STATUS_DONE, "правило отмечено «Реализовано»")
                return _result(
                    task,
                    STATUS_NOT_DONE,
                    "код правило не проверяет: отметьте «Реализовано» в свойствах правила",
                )
    return _result(task, STATUS_DONE, "правила в плане больше нет")


def _verify_violation(task: dict, front: dict) -> dict:
    still = any(
        item.get("code") == task.get("code") and item.get("file") == task.get("file")
        for item in front.get("violations") or []
    )
    if still:
        return _result(task, STATUS_NOT_DONE, "нарушение всё ещё есть")
    return _result(task, STATUS_DONE, "нарушения больше нет")


def verify_front_task(task: dict, front: dict, links: dict | None = None) -> dict:
    kind = task.get("kind")
    if kind == "page":
        return _verify_page(task, front, links)
    if kind == "block":
        return _verify_block(task, front, links)
    if kind == "block_deletion":
        return _verify_block_deletion(task, front)
    if kind == "page_deletion":
        return _verify_page_deletion(task, front)
    if kind == "rule":
        return _verify_rule(task, front)
    if kind == "violation":
        return _verify_violation(task, front)
    return _result(task, STATUS_NOT_DONE, f"неизвестный вид задачи: {kind}")


def verify_front_brief(front: dict, brief: dict, links: dict | None = None) -> dict:
    """Отчёт того же вида, что у ``verify_brief`` бэкенда."""
    items = [verify_front_task(task, front, links) for task in brief.get("tasks") or []]
    done = sum(1 for item in items if item["status"] == STATUS_DONE)
    partial = sum(1 for item in items if item["status"] == STATUS_PARTIAL)
    not_done = sum(1 for item in items if item["status"] == STATUS_NOT_DONE)
    total = len(items)
    if total == 0:
        status = "нет задач"
    elif done == total:
        status = "выполнено"
    elif done == 0 and partial == 0:
        status = "не сделано"
    else:
        status = "частично"
    return {
        "brief_id": brief.get("brief_id", ""),
        "total": total,
        "done": done,
        "partial": partial,
        "not_done": not_done,
        "status": status,
        "items": items,
        "code": 0 if done == total else 1,
    }


def render_front_report(report: dict) -> str:
    lines = [
        f"ТЗ фронтенда {report.get('brief_id') or '?'}: выполнено "
        f"{report['done']} из {report['total']} · {report['status']}"
    ]
    for item in report.get("items") or []:
        kind = KIND_RU.get(item.get("kind"), item.get("kind"))
        lines.append(
            f"  T{item.get('number')} {kind} `{item.get('name')}` — "
            f"{item['status']}: {item.get('message')}"
        )
    return "\n".join(lines)


__all__ = [
    "KIND_RU",
    "render_front_report",
    "verify_front_brief",
    "verify_front_task",
]
