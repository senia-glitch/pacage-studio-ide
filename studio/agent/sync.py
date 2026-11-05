"""Промпт синхронизации: AI-агент с доступом к папке проекта приводит
описания элементов в metadata.json к их реальному поведению в коде."""

from __future__ import annotations

from pathlib import Path

from studio.agent.sync_json import SYNC_FORMAT_LINES, check_sync_payload
from studio.metamodel import Graph
from studio.scanner.anchors import AnchorResolver

_KIND_LABELS = {"endpoint": "эндпоинт", "scenario": "сценарий", "io": "io", "model": "модель"}

_SCOPE_LABELS = {"all": "весь проект", "domain": "выбранный домен", "unready": "узлы без описания"}


def _domains_of(node) -> list[str]:
    return [part.strip() for part in str(node.domain or "").split(",") if part.strip()]


def _cell(text) -> str:
    """Значение для ячейки markdown-таблицы: одна строка, `|` экранирован."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _in_scope(node, scope: str, domain: str) -> bool:
    if node.planned or node.kind not in _KIND_LABELS:
        return False
    if scope == "domain":
        return bool(domain) and domain in _domains_of(node)
    if scope == "unready":
        return not str(node.description or "").strip()
    return True


def build_sync_prompt(graph: Graph, scope: str = "all", domain: str = "") -> str:
    """Промпт для AI-агента: по коду предложить description/acceptance узлов JSON-ответом."""
    scope = scope if scope in _SCOPE_LABELS else "all"
    project_root = project_root_name(graph)
    rows: list[str] = []
    total = 0
    for node in sorted(
        (n for n in graph.nodes if _in_scope(n, scope, domain)),
        key=lambda n: (n.kind, str(n.file_path), n.id),
    ):
        total += 1
        description = _cell(node.description or "") or "—"
        acceptance = "; ".join(
            _cell(item) for item in node.acceptance or [] if str(item).strip()
        ) or "—"
        file_ref = f"{node.file_path}:{node.line}" if node.file_path else "—"
        kind_label = _KIND_LABELS[node.kind]
        rows.append(
            f"| {kind_label} | `{node.id}` | {node.label} | {file_ref} "
            f"| {node.domain or '—'} | {description} | {acceptance} |"
        )
    scope_label = _SCOPE_LABELS[scope] + (f": {domain}" if scope == "domain" and domain else "")
    lines = [
        "# Синхронизация описаний пакета с реальным кодом",
        "",
        f"Ты — AI-агент с полным доступом к папке проекта `{project_root}`.",
        "В папке лежит инструмент pacage-studio: его данные — файл `.studio/metadata.json`.",
        "",
        "## Цель",
        "Привести описания элементов пакета в соответствие с их РЕАЛЬНЫМ поведением в коде.",
        "Описание должно отвечать на вопрос «что этот элемент делает сейчас»,",
        "а не «что задумывалось на этапе планирования».",
        "",
        "## Что делать",
        "1. Для каждого узла из таблицы открой указанный файл и найди реализацию.",
        "2. Сформулируй `description` — фактическое поведение элемента сейчас, сжато и точно.",
        "3. Сформулируй `acceptance` — фактические проверяемые утверждения о поведении",
        "   (по одному на элемент массива).",
        "",
        "## Жёсткие правила",
        "- Файл `.studio/metadata.json` и код проекта НЕ редактируй: результат — только JSON.",
        "- В ответе допустимы ТОЛЬКО поля `description` и `acceptance` узлов из таблицы.",
        "- Плановые узлы (planned: true, id вида studio:…) в списке нет и трогать их нельзя.",
        "- Язык и стиль описаний сохранять, как в текущих описаниях проекта.",
        "",
        f"## Узлы (область: {scope_label}; всего {total})",
        "",
        "| вид | id | название | файл:строка | домен | описание сейчас | критерии сейчас |",
        "|---|---|---|---|---|---|---|",
        *rows,
        "",
        *SYNC_FORMAT_LINES,
        "",
    ]
    if not total:
        lines += [
            "В выбранной области нет узлов — синхронизировать нечего.",
            'Ответь JSON: {"updates": [], "not_synced": []}.',
            "",
        ]
    return "\n".join(lines)


def sync_known_nodes(graph: Graph) -> dict[str, dict]:
    """Узлы, описания которых можно обновить JSON-ответом (факт, виды из промпта)."""
    return {
        node.id: {
            "label": node.label,
            "kind": node.kind,
            "description": str(node.description or ""),
            "acceptance": list(node.acceptance or []),
        }
        for node in graph.nodes
        if not node.planned and node.kind in _KIND_LABELS
    }


def _resolver(graph: Graph, known: dict[str, dict]):
    """Запись ответа → узел: по id/якорю или по паре plan + layer (слой плана в коде)."""
    states = {state.slug: state for state in graph.plans}
    anchors = AnchorResolver(graph)
    by_id = graph.by_id()

    def pick(node_id: str | None, reason: str) -> tuple[str | None, str]:
        node = by_id.get(node_id or "")
        if node is None:
            return None, reason
        if node.planned:
            return None, "узел ещё не реализован в коде (studio verify: не сделано)"
        if node.id not in known:
            return None, f"у узлов вида {node.kind} описания не хранятся"
        return node.id, ""

    def resolve(item: dict) -> tuple[str | None, str]:
        node_id, plan, layer = item.get("id"), item.get("plan"), item.get("layer")
        has_id = isinstance(node_id, str) and bool(node_id.strip())
        has_plan = isinstance(plan, str) and bool(plan.strip())
        if has_id and (has_plan or layer is not None):
            return None, "укажите либо id, либо plan и layer"
        if has_id:
            text = node_id.strip()
            resolved = text if text in by_id else anchors.resolve(text)[0]
            return pick(resolved, "id не найден среди узлов кода")
        if not has_plan:
            return None, "нет id узла или пары plan и layer"
        state = states.get(plan.strip())
        if state is None:
            return None, f"план «{plan.strip()}» не найден"
        if not isinstance(layer, str) or not layer.strip():
            return None, "для плана нужен layer"
        item_layer = next((entry for entry in state.layers if entry.layer == layer.strip()), None)
        if item_layer is None:
            return None, f"в плане «{state.slug}» нет слоя «{layer.strip()}»"
        ids = [i for i in item_layer.node_ids if i in by_id and not by_id[i].planned]
        if item_layer.status != "done" or not ids:
            return None, "слой не найден в коде (studio verify: не сделано)"
        if len(ids) > 1:
            return None, "слой содержит несколько узлов — укажите id узла: " + ", ".join(ids)
        return pick(ids[0], "")

    return resolve


def check_sync_response(graph: Graph, payload) -> dict:
    """Разбор JSON-ответа агента против узлов графа; ничего не записывает.

    Узел указывается по id (якорю) или по паре plan + layer — вторая форма нужна
    итоговому отчёту по ТЗ-плану.
    """
    known = sync_known_nodes(graph)
    return check_sync_payload(
        payload, known, resolve=_resolver(graph, known), extra_keys=("plan", "layer")
    )


def project_root_name(graph: Graph) -> str:
    return Path(graph.project_root).name or str(graph.project_root)
