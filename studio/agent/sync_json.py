"""Контракт JSON-ответа на промпт синхронизации описаний.

Промпт просит у AI-агента один JSON-блок; студия принимает его, проверяет
каждую запись, показывает отличия и только по явному действию пользователя
записывает `description` и `acceptance` в метаданные. Модуль чистый: ни диска,
ни графа — известные узлы ему передаёт вызывающий код (бэкенд или фронтенд).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n?(.*?)```", re.S)

SYNC_FORMAT_LINES = [
    "## Формат ответа (строго)",
    "Файлы метаданных и код НЕ редактируй. Ответь ОДНИМ JSON-блоком — студия сама",
    "проверит его и покажет пользователю отличия перед записью:",
    "",
    "```json",
    "{",
    '  "updates": [',
    '    {"id": "<id из таблицы>", "description": "<фактическое описание>",',
    '     "acceptance": ["<проверяемый критерий>"]}',
    "  ],",
    '  "not_synced": [',
    '    {"id": "<id из таблицы>", "reason": "<почему не обновлено>"}',
    "  ]",
    "}",
    "```",
    "",
    "- `updates` — только узлы, в поведении которых ты уверен; `id` строго из таблицы.",
    "- `description` — непустая строка; `acceptance` — массив строк (может быть пустым).",
    "- Не уверен в узле — не включай его в `updates`, а перечисли в `not_synced`.",
    "- Других полей и комментариев вне JSON-блока не добавляй.",
]


_REPORT_TAIL = [
    '  "report": {',
    '    "outside_changes": ["<путь: что изменено вне целевых файлов>"],',
    '    "tests": "<какие тесты запущены и результат>",',
    '    "questions": ["<допущение или вопрос>"],',
    '    "decisions": ["<принятое решение: имена, поля, SQL>"]',
    "  }",
    "}",
    "```",
    "",
]

BRIEF_REPORT_BACKEND_LINES = [
    "Итоговый отчёт — ОДИН JSON-блок, после него ничего. Студия сверит его с кодом",
    "и по кнопке «Применить» запишет описания и критерии в метаданные:",
    "",
    "```json",
    "{",
    '  "updates": [',
    '    {"plan": "<slug плана>", "layer": "endpoint | scenario | io | model",',
    '     "description": "<что узел делает фактически>", "acceptance": ["<критерий>"]},',
    '    {"id": "<якорь узла>", "description": "…", "acceptance": ["…"]}',
    "  ],",
    '  "not_synced": [{"plan": "<slug>", "layer": "<слой>", "reason": "<почему не сделано>"}],',
    *_REPORT_TAIL,
    "- Задача-план: `plan` + `layer`; для слоя с несколькими узлами (например io) — `id` узла.",
    "- Задача-узел: `id` — якорь из задачи. Слой `test` в `updates` не указывается.",
    "- Статус «выполнено» определяет студия (`studio verify`): запись для узла, которого нет",
    "  в коде, будет отклонена. Не сделанное перечисли в `not_synced`.",
    "- `report` только показывается пользователю; в метаданные пишутся лишь",
    "  `description` и `acceptance`.",
]

BRIEF_REPORT_FRONTEND_LINES = [
    "Итоговый отчёт — ОДИН JSON-блок, после него ничего. Студия сверит его с кодом",
    "и по кнопке «Применить» запишет описания и критерии в `.studio/frontend.json`:",
    "",
    "```json",
    "{",
    '  "updates": [',
    '    {"id": "page:<папка страницы>", "description": "<что делает страница>",',
    '     "acceptance": ["<критерий>"]},',
    '    {"id": "block:<папка страницы>:<Компонент>", "description": "…", "acceptance": ["…"]}',
    "  ],",
    '  "not_synced": [{"id": "<id>", "reason": "<почему не сделано>"}],',
    *_REPORT_TAIL,
    "- `id` — фактические страницы и блоки, созданные или изменённые в `frontend/`;",
    "  запись для узла, которого нет в коде, будет отклонена.",
    "- `report` только показывается пользователю; в метаданные пишутся лишь",
    "  `description` и `acceptance`.",
]


class SyncJsonError(ValueError):
    """Ответ нельзя разобрать как контракт синхронизации (файл не затронут)."""

    code = "invalid_input"


def extract_json(text) -> object:
    """JSON из ответа агента: чистый JSON, блок ```json или объект внутри текста."""
    if isinstance(text, (dict, list)):
        return text
    raw = str(text or "").strip()
    if not raw:
        raise SyncJsonError("Ответ пуст — вставьте JSON, который вернул агент")
    candidates = [raw]
    candidates += [m.group(1).strip() for m in FENCE_RE.finditer(raw)]
    start, end = raw.find("{"), raw.rfind("}")
    if 0 <= start < end:
        candidates.append(raw[start : end + 1])
    parsed = []
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        parsed.append(value)
    for value in reversed(parsed):
        if isinstance(value, dict) and "updates" in value:
            return value
    if parsed:
        return parsed[-1]
    raise SyncJsonError("Не удалось найти корректный JSON в ответе")


def _ref(item: dict) -> str:
    """Как запись названа в ответе: id узла или «план / слой»."""
    node_id = item.get("id")
    if isinstance(node_id, str) and node_id.strip():
        return node_id.strip()
    plan = item.get("plan")
    if isinstance(plan, str) and plan.strip():
        layer = item.get("layer")
        layer = layer.strip() if isinstance(layer, str) else ""
        return f"{plan.strip()} / {layer}" if layer else plan.strip()
    return ""


def _same(before_description: str, before_acceptance: list, description: str, acceptance: list):
    return before_description.strip() == description and list(before_acceptance) == acceptance


REPORT_KEYS = ("outside_changes", "tests", "questions", "decisions")
REPORT_LISTS = ("outside_changes", "questions", "decisions")

# Resolver: запись updates → (id узла, "") или (None, причина отказа).
Resolver = Callable[[dict], tuple[str | None, str]]


def _clean_report(raw, warnings: list[str]) -> dict | None:
    """Итоговый отчёт агента: показывается пользователю, в метаданные не пишется."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        warnings.append("\"report\" должен быть объектом — поле пропущено")
        return None
    extra = sorted(set(raw) - set(REPORT_KEYS))
    if extra:
        warnings.append("В \"report\" лишние поля игнорируются: " + ", ".join(extra))
    report: dict = {}
    for key in REPORT_KEYS:
        value = raw.get(key)
        if key in REPORT_LISTS:
            if isinstance(value, list):
                report[key] = [str(v).strip() for v in value if str(v).strip()]
            else:
                report[key] = []
                if value not in (None, ""):
                    warnings.append(f"\"report.{key}\" должен быть массивом строк")
        else:
            report[key] = str(value).strip() if isinstance(value, str) else ""
    return report


def check_sync_payload(
    payload,
    known: dict[str, dict],
    resolve: Resolver | None = None,
    extra_keys: tuple[str, ...] = (),
) -> dict:
    """Проверить ответ против известных узлов; ничего не записывает.

    known: id → {"label", "kind", "description", "acceptance"} — фактические узлы,
    которые можно синхронизировать. resolve (необязательно) определяет узел по
    записи иначе, чем по полю id (например, по плану и слою); extra_keys — поля
    записи, которые resolve использует. Результат: rows (change/same/rejected),
    not_synced, report, warnings, counts. Невалидная запись отклоняется с
    причиной, остальные принимаются; молча ничего не отбрасывается.
    """
    if not isinstance(payload, dict):
        raise SyncJsonError("Ответ должен быть JSON-объектом вида {\"updates\": […]}")
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise SyncJsonError("В ответе нет массива \"updates\"")

    warnings: list[str] = []
    extra_top = sorted(set(payload) - {"updates", "not_synced", "report"})
    if extra_top:
        warnings.append("Лишние поля верхнего уровня игнорируются: " + ", ".join(extra_top))

    rows: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(updates):
        row = {
            "index": index, "id": "", "ref": "", "label": "", "status": "rejected", "reason": ""
        }
        rows.append(row)
        if not isinstance(item, dict):
            row["reason"] = "запись должна быть объектом"
            continue
        row["ref"] = _ref(item)
        if resolve is None:
            node_id = item.get("id")
            if not isinstance(node_id, str) or not node_id.strip():
                row["reason"] = "нет строкового поля id"
                continue
            node_id = node_id.strip()
        else:
            node_id, reason = resolve(item)
            if node_id is None:
                row["reason"] = reason
                continue
        row["id"] = node_id
        info = known.get(node_id)
        if info is None:
            row["reason"] = "id не найден среди фактических узлов"
            continue
        row["label"] = info.get("label") or node_id
        row["kind"] = info.get("kind") or ""
        if node_id in seen:
            row["reason"] = "id повторяется — учтена первая запись"
            continue
        seen.add(node_id)
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            row["reason"] = "description должен быть непустой строкой"
            continue
        acceptance = item.get("acceptance")
        if not isinstance(acceptance, list) or not all(isinstance(v, str) for v in acceptance):
            row["reason"] = "acceptance должен быть массивом строк"
            continue
        description = description.strip()
        acceptance = [v.strip() for v in acceptance if v.strip()]
        extra = sorted(set(item) - {"id", "description", "acceptance", *extra_keys})
        if extra:
            label = row["ref"] or node_id
            warnings.append(f"{label}: лишние поля игнорируются ({', '.join(extra)})")
        before_acceptance = [str(v) for v in info.get("acceptance") or []]
        before_description = str(info.get("description") or "")
        row["before"] = {"description": before_description, "acceptance": before_acceptance}
        row["after"] = {"description": description, "acceptance": acceptance}
        same = _same(before_description, before_acceptance, description, acceptance)
        row["status"] = "same" if same else "change"

    not_synced: list[dict] = []
    raw_not_synced = payload.get("not_synced", [])
    if raw_not_synced is None:
        raw_not_synced = []
    if not isinstance(raw_not_synced, list):
        warnings.append("\"not_synced\" должен быть массивом — поле пропущено")
        raw_not_synced = []
    for item in raw_not_synced:
        ref = _ref(item) if isinstance(item, dict) else str(item or "").strip()
        if ref:
            reason = str(item.get("reason") or "").strip() if isinstance(item, dict) else ""
            not_synced.append({"id": ref, "reason": reason})
        else:
            warnings.append("Запись в \"not_synced\" без id пропущена")

    counts = {
        "change": sum(1 for row in rows if row["status"] == "change"),
        "same": sum(1 for row in rows if row["status"] == "same"),
        "rejected": sum(1 for row in rows if row["status"] == "rejected"),
        "not_synced": len(not_synced),
    }
    report = _clean_report(payload.get("report"), warnings)
    return {
        "rows": rows,
        "not_synced": not_synced,
        "report": report,
        "warnings": warnings,
        "counts": counts,
    }


def check_undo_entries(raw) -> list[dict]:
    """Записи отмены применения: [{id, before, after}] с описанием и критериями."""
    if not isinstance(raw, list) or not raw:
        raise SyncJsonError("Нечего отменять: список записей отмены пуст")
    entries = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise SyncJsonError("Запись отмены должна быть объектом с id")
        states = []
        for key in ("before", "after"):
            state = item.get(key)
            if (
                not isinstance(state, dict)
                or not isinstance(state.get("description"), str)
                or not isinstance(state.get("acceptance"), list)
                or not all(isinstance(v, str) for v in state["acceptance"])
            ):
                raise SyncJsonError(f"В записи отмены {item['id']} нет корректного «{key}»")
            states.append(
                {"description": state["description"], "acceptance": list(state["acceptance"])}
            )
        entries.append({"id": item["id"], "before": states[0], "after": states[1]})
    return entries


def same_state(info: dict, state: dict) -> bool:
    """Совпадает ли текущее описание узла с сохранённым состоянием."""
    return str(info.get("description") or "").strip() == state["description"].strip() and [
        str(v) for v in info.get("acceptance") or []
    ] == list(state["acceptance"])


__all__ = [
    "BRIEF_REPORT_BACKEND_LINES",
    "BRIEF_REPORT_FRONTEND_LINES",
    "SYNC_FORMAT_LINES",
    "SyncJsonError",
    "check_sync_payload",
    "check_undo_entries",
    "extract_json",
    "same_state",
]
