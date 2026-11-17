"""Жёсткие проверки стека: то, что получилось в коде, сверяется сканерами студии.

Сканеры читают код по AST и ничего не запускают. Бэкенд (fastbase → core-package → event-infra):
каждый эндпоинт банка есть в коде, роут зовёт ``run("сценарий", dto)``, сценарии дописаны, таблицы
схемы есть в ``models.py``, сканер не нашёл ошибок. Фронтенд: структура и слои по стандарту стека,
ни одной ошибки правил (DAG, hex вне tokens.css, сеть в store), на этапе бэкенда — каждый вызов
страниц находит свой эндпоинт.

Проверка возвращает список понятных замечаний; пустой список — всё в порядке. В тестах конвейера
вместо неё подставляют заглушку: настоящие сканеры нужны настоящему проекту.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from studio.agent import projectenv
from studio.frontend.graph import build_front_graph
from studio.frontend.links import call_keys, check_links, endpoint_index
from studio.scanner import scan

from . import stack, waves

SCAFFOLD_MARK = "допишите сценарий по ТЗ"
BACK_WARNINGS = {
    "W_TABLE_UNKNOWN", "W_NO_EXECUTE", "W_DISPATCH_DYNAMIC", "W_IMPORT_UNRESOLVED",
    "W_ROUTE_SHADOW", "W_NO_MODELS", "W_NO_SCENARIOS", "W_NO_ROUTES",
}  # fmt: skip
FRONT_WARNINGS = {"structure_missing", "package_missing", "index_missing", "stack_mismatch"}
MAX_FINDINGS = 25


def _cap(items: list[str]) -> list[str]:
    if len(items) <= MAX_FINDINGS:
        return items
    return [*items[:MAX_FINDINGS], f"…и ещё замечаний: {len(items) - MAX_FINDINGS}"]


def _env_problems(project: Path) -> list[str]:
    path = project / ".env.fastbase"
    if not path.is_file():
        return [".env.fastbase нет: выполните fb init (шаг «Модели и каркас по стеку»)"]
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^FAT_INTEGRATIONS=(.*)$", text, re.M)
    parts = {p.strip() for p in (match.group(1) if match else "").split(",")}
    if not {"core", "event-infra"} <= parts:
        return [".env.fastbase: FAT_INTEGRATIONS должен быть «core,event-infra», иначе ошибки уйдут 500"]
    return []


def _unfinished(project: Path) -> list[str]:
    out = []
    for path in sorted((project / "app" / "scenarios").rglob("*.py")):
        if SCAFFOLD_MARK in path.read_text(encoding="utf-8", errors="replace"):
            out.append(f"Сценарий {path.relative_to(project).as_posix()} не дописан: остался каркас")
    return out


def fb_check(project: Path) -> list[str]:
    """``fb check`` — проверка самого стека: файлы на месте, настройки валидны, app.main:app и роуты импортируются.

    Запускается только когда стек установлен в окружении проекта; иначе проверку пропускаем.
    """
    exe = stack._tool(project, "fb")
    if exe is None:
        return []
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    projectenv.activate(env, project)
    try:
        done = subprocess.run(  # noqa: S603 — собственная проверка стека, без оболочки
            [exe, "check"], cwd=str(project), capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, env=env,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as error:
        return [f"fb check не запустился: {error}"]
    text = ((done.stdout or "") + (done.stderr or "")).strip()
    if done.returncode == 0 or "No module named" in text:
        return []
    return [f"fb check (проверка самого стека) нашёл проблему: {text[-500:]}"]


class StackChecker:
    """Настоящие проверки стека для проекта конвейера."""

    def backend(self, pl) -> list[str]:
        project = Path(pl.project)
        out: list[str] = []
        absent = stack.missing_files(project)
        if absent:
            return [f"Проект не на стеке: нет {', '.join(absent)}"]
        out += _env_problems(project)
        graph = scan(project)
        prefix = stack.api_prefix(project)
        index = endpoint_index(graph, prefix)
        dispatched = {e.from_node for e in graph.edges if e.kind == "dispatch"}
        for card in waves.records(pl, "endpoint"):
            attrs = card.get("attrs") or {}
            method, path = str(attrs.get("method") or "").upper(), str(attrs.get("path") or "")
            keys = call_keys(method, stack.route_path(path, prefix), prefix)
            node = next((index[k] for k in keys if k in index), "")
            if not node:
                out.append(f"Эндпоинт {method} {path} из контракта не найден в коде (роут fastbase)")
            elif node not in dispatched:
                out.append(f'Роут {method} {path} не вызывает сценарий: нужен return await run("имя", dto)')
            name = str(attrs.get("scenario") or "")
            domain = stack.split_path(path, prefix)[0]
            init = project / "app" / "scenarios" / domain / "__init__.py"
            if name and (init.parent / f"{name}.py").is_file():
                text = init.read_text(encoding="utf-8", errors="replace") if init.is_file() else ""
                if not re.search(rf"\b{re.escape(name)}\b", text):
                    out.append(
                        f"Сценарий {name} не подключён в app/scenarios/{domain}/__init__.py: "
                        "core обходит только пакеты первого уровня"
                    )
        models = {str((n.details or {}).get("tablename")) for n in graph.nodes if n.kind == "model"}
        for table in pl.bank.records("table", status="accepted"):
            name = (table.get("attrs") or {}).get("name") or table["title"]
            if name not in models:
                out.append(f"Таблицы «{name}» нет в models.py (SQLModel с __tablename__)")
        for d in graph.diagnostics:
            if d.level == "error" or d.code in BACK_WARNINGS:
                where = f" ({d.file}:{d.line})" if d.file else ""
                out.append(f"{d.code}: {d.message}{where}")
        out += _unfinished(project)
        front = build_front_graph(project)
        if front.get("present"):
            links = check_links(front, graph, prefix)
            for row in links["rows"]:
                if row["status"] == "missing":
                    out.append(f"«{row['name']}» вызывает {row['call']}, а такого эндпоинта в бэкенде нет")
        if not out:  # статика чистая — дальше проверка самого стека: код импортируется
            out += fb_check(project)
        return _cap(out)

    def frontend(self, pl) -> list[str]:
        project = Path(pl.project)
        graph = build_front_graph(project)
        if not graph.get("present"):
            return ["Нет каталога frontend/ (шаг 3.1 «Стиль и каркас» создаёт его)"]
        out = []
        for item in graph.get("violations") or []:
            if item["severity"] == "error" or item["code"] in FRONT_WARNINGS:
                out.append(f"{item['code']} {item['file']}:{item.get('line') or 1} — {item['message']}")
        return _cap(out)


__all__ = ["BACK_WARNINGS", "FRONT_WARNINGS", "SCAFFOLD_MARK", "StackChecker", "fb_check"]
