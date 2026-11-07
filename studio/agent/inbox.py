"""Входящие отчёты агента: `.studio/agent-reports/<id>.json`.

Агент отправляет итоговый JSON-отчёт (`POST /api/agent/report`); студия проверяет
его так же, как при ручном приёме, и кладёт сюда со статусом ``pending``. Ничего
из отчёта не применяется: решение принимает пользователь в интерфейсе
(``accepted`` после применения или ``rejected``).
"""

from __future__ import annotations

import datetime
import json
import re
import secrets
import threading
import time
from pathlib import Path

from studio.agent.redaction import redact
from studio.metadata import MetadataError
from studio.store import atomic_write

REPORTS_DIR = "agent-reports"
MAX_REPORTS = 50
PENDING = "pending"
DECISIONS = ("accepted", "rejected")
_LOCK = threading.RLock()  # отправка и решение — «прочитать, изменить, записать»
_ID_RE = re.compile(r"^r-\d{8}-\d{6}-[0-9a-f]{4}$")


def reports_dir(root: Path) -> Path:
    return Path(root) / ".studio" / REPORTS_DIR


def _path(root: Path, report_id: str) -> Path:
    if not _ID_RE.match(str(report_id or "")):
        raise MetadataError("invalid_input", "Некорректный id отчёта")
    return reports_dir(root) / f"{report_id}.json"


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _all(root: Path) -> list[dict]:
    folder = reports_dir(root)
    if not folder.is_dir():
        return []
    records = [_read(path) for path in folder.glob("r-*.json")]
    found = [item for item in records if item and item.get("id")]
    # по времени создания (с микросекундами): порядок не зависит от случайной части id
    return sorted(found, key=lambda item: (str(item.get("created_at") or ""), item["id"]))


def _write(root: Path, record: dict) -> None:
    folder = reports_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    # ключ или токен, случайно попавший в отчёт агента, на диск не пишем
    payload = redact(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    if not atomic_write(_path(root, record["id"]), payload):
        raise MetadataError("not_writable", "Не удалось сохранить отчёт агента")


def summary(record: dict) -> dict:
    """Запись без текста отчёта — для списка."""
    return {key: value for key, value in record.items() if key != "raw"}


def submit_report(
    root: Path, part: str, mode: str, raw: str, preview: dict, summary: str = ""
) -> dict:
    """Сохранить отчёт агента со статусом pending; ``preview`` — результат проверки."""
    with _LOCK:
        records = _all(root)
        if sum(1 for item in records if item.get("status") == PENDING) >= MAX_REPORTS:
            raise MetadataError(
                "invalid_input",
                f"Неразобранных отчётов уже {MAX_REPORTS}: разберите их в интерфейсе студии",
            )
        decided = [item for item in records if item.get("status") != PENDING]
        for old in decided[: max(0, len(records) + 1 - MAX_REPORTS)]:
            _path(root, old["id"]).unlink(missing_ok=True)
        now = datetime.datetime.now()
        while True:
            report_id = f"r-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
            if not _path(root, report_id).exists():
                break
        record = {
            "id": report_id,
            "created_at": now.isoformat(timespec="microseconds"),
            "part": part,
            "mode": mode,
            "status": PENDING,
            "counts": preview.get("counts") or {},
            "agent_report": preview.get("report"),
            "summary": str(summary or ""),
            "raw": raw,
        }
        _write(root, record)
        return record


def list_reports(root: Path) -> list[dict]:
    """Новые первыми, без текста отчётов."""
    return [summary(item) for item in reversed(_all(root))]


def get_report(root: Path, report_id: str) -> dict:
    record = _read(_path(root, report_id))
    if record is None:
        raise MetadataError("node_not_found", "Отчёт агента не найден")
    return record


def decide_report(root: Path, report_id: str, decision: str) -> dict:
    if decision not in DECISIONS:
        raise MetadataError("invalid_input", "decision: accepted или rejected")
    with _LOCK:
        record = get_report(root, report_id)
        record["status"] = decision
        record["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _write(root, record)
        return summary(record)


__all__ = [
    "DECISIONS",
    "MAX_REPORTS",
    "PENDING",
    "decide_report",
    "get_report",
    "list_reports",
    "reports_dir",
    "submit_report",
    "summary",
]
