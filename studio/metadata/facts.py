"""План → факт: когда код плана найден, план закрывается сам.

Статус плана считает сканер (``resolve_plan``). Как только все слои найдены в коде и между
endpoint и сценарием есть связь, запись плана в ``metadata.json`` больше не нужна: описание,
критерии и домен переходят на реальные узлы, заглушки слоёв и сама запись плана убираются, а в
``finished_plans`` остаётся строка журнала (что и когда закрыто).

Правила:

* пользовательские поля реальных узлов не затираются: берётся то, что уже есть, иначе — из плана;
* неполный план (``partial``) не трогается;
* повторный вызов ничего не меняет (план уже закрыт).
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from studio.metadata.apply import _plan_slug_for_record
from studio.metadata.store import _clean_node, _mutate_metadata, _record_for_node, read_metadata
from studio.metamodel import Graph

JOURNAL_KEY = "finished_plans"
CARRY_KINDS = ("endpoint", "scenario")  # описание плана переносим только на эти слои


def finish_plans(root: Path, graph: Graph) -> list[dict]:
    """Закрыть выполненные планы. Возвращает журнал закрытых сейчас (пусто — менять нечего)."""
    done = [state for state in graph.plans if state.status == "done"]
    if not done:
        return []
    known = set(read_metadata(root).plans)
    done = [state for state in done if state.slug in known]
    if not done:
        return []
    by_id = graph.by_id()
    closed: list[dict] = []

    def mutate(metadata) -> None:
        closed.clear()  # повтор после конфликта записи не должен дублировать журнал
        for state in done:
            plan = metadata.plans.get(state.slug)
            if plan is None:
                continue
            real: list[str] = []
            layers: dict[str, list[str]] = {}
            for layer in state.layers:
                found = [i for i in layer.node_ids if i in by_id and not by_id[i].planned]
                if found:
                    layers[layer.layer] = found
                if layer.layer not in CARRY_KINDS:
                    continue
                for node_id in found:
                    node = by_id[node_id]
                    existing = metadata.nodes.get(node_id) or {}
                    description = str(existing.get("description") or "").strip() or plan.description
                    acceptance = list(existing.get("acceptance") or []) or list(plan.acceptance)
                    domain = str(existing.get("domain") or "") or plan.domain
                    metadata.nodes[node_id] = _clean_node(
                        _record_for_node(node, description, acceptance, domain, existing)
                    )
                    real.append(node_id)
            stubs = [
                key for key, record in metadata.nodes.items()
                if record.get("planned") and key not in real
                and _plan_slug_for_record(metadata, record) == state.slug
            ]
            for key in stubs:
                metadata.nodes.pop(key, None)
            if stubs:
                gone = set(stubs)
                metadata.edges = [
                    edge for edge in metadata.edges
                    if edge.get("from") not in gone and edge.get("to") not in gone
                ]
            metadata.plans.pop(state.slug, None)
            entry = {
                "slug": state.slug,
                "title": plan.title or state.slug,
                "at": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "nodes": real,
                "layers": layers,
            }
            journal = metadata.extra.get(JOURNAL_KEY)
            metadata.extra[JOURNAL_KEY] = [
                *(journal if isinstance(journal, list) else []), entry,
            ]
            closed.append(entry)

    _mutate_metadata(root, mutate)
    return closed


def journal(root: Path) -> dict[str, dict]:
    """Журнал закрытых планов: ``slug`` → запись (последняя, если план закрывали не раз)."""
    raw = read_metadata(root).extra.get(JOURNAL_KEY)
    out: dict[str, dict] = {}
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, dict) and entry.get("slug"):
            out[str(entry["slug"])] = entry
    return out


__all__ = ["CARRY_KINDS", "JOURNAL_KEY", "finish_plans", "journal"]
