"""Банки: общие хранилища записей проекта, которые пишет один этап и читают другие.

Банк — файл ``.idea-graph/banks/<имя>.json``. Запись — ``{id, type, key, data, sources, schema_version,
author, created, updated}``. Структуру ``data`` определяет пакет-автор банка (ядро её не знает), а
читатели опираются на ``type`` и ``schema_version``. ``key`` — необязательный естественный ключ
(например, нормализованный адрес конкурента): запись с тем же ключом обновляется, а не дублируется.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
from pathlib import Path

from .errors import GraphError

_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
MAX_SOURCES = 10


def _clean_sources(raw) -> list[str]:
    items = raw if isinstance(raw, (list, tuple)) else ([] if raw is None else [raw])
    out: list[str] = []
    for item in items:
        text = " ".join(str(item or "").split())[:400]
        if text and text not in out:
            out.append(text)
    return out[:MAX_SOURCES]


class Bank:
    """Один банк проекта. Создаётся через ``Store.bank(name)``."""

    def __init__(self, store, name: str):
        if not _NAME.match(name or ""):
            raise GraphError("invalid_bank", "Имя банка: латиница, цифры, «-» и «_», начинается с буквы")
        self.store = store
        self.name = name
        self.path: Path = store.dir / "banks" / f"{name}.json"

    # ------------------------------------------------------------------ чтение

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        records = [r for r in data.get("records", []) if isinstance(r, dict) and r.get("id")]
        return {"next": int(data.get("next") or len(records) + 1), "records": records}

    def list(self, type: str | None = None) -> list[dict]:
        with self.store.lock:
            records = self._read()["records"]
        return copy.deepcopy([r for r in records if type is None or r.get("type") == type])

    def get(self, record_id: str) -> dict:
        for record in self.list():
            if record["id"] == record_id:
                return record
        raise GraphError("unknown_record", f"В банке «{self.name}» нет записи {record_id}")

    def find(self, key: str) -> dict | None:
        return next((r for r in self.list() if r.get("key") == key), None)

    # ------------------------------------------------------------------ запись

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def put(self, type: str, data: dict, author: str, sources=None, key: str = "",
            schema_version: int = 1) -> dict:
        """Добавить запись; если запись с таким ``key`` уже есть — обновить её (источники объединяются)."""
        if not isinstance(data, dict):
            raise GraphError("invalid_record", "data записи банка — объект")
        type = str(type or "").strip()[:60]
        if not type:
            raise GraphError("invalid_record", "У записи банка должен быть type")
        key = " ".join(str(key or "").split())[:300]
        now = time.time()
        with self.store.lock:
            state = self._read()
            existing = next((r for r in state["records"] if key and r.get("key") == key and r.get("type") == type), None)
            if existing is not None:
                existing["data"] = copy.deepcopy(data)
                existing["sources"] = _clean_sources(existing.get("sources", []) + _clean_sources(sources))
                existing["schema_version"] = int(schema_version)
                existing["updated"] = now
                existing["author"] = author
                record = existing
            else:
                record = {"id": f"b{state['next']}", "type": type, "key": key, "data": copy.deepcopy(data),
                          "sources": _clean_sources(sources), "schema_version": int(schema_version),
                          "author": author, "created": now, "updated": now}
                state["records"].append(record)
                state["next"] += 1
            self._write(state)
            return copy.deepcopy(record)

    def update(self, record_id: str, author: str, data: dict | None = None, sources=None) -> dict:
        with self.store.lock:
            state = self._read()
            record = next((r for r in state["records"] if r["id"] == record_id), None)
            if record is None:
                raise GraphError("unknown_record", f"В банке «{self.name}» нет записи {record_id}")
            if data is not None:
                if not isinstance(data, dict):
                    raise GraphError("invalid_record", "data записи банка — объект")
                record["data"] = copy.deepcopy(data)
            if sources is not None:
                record["sources"] = _clean_sources(sources)
            record["updated"] = time.time()
            record["author"] = author
            self._write(state)
            return copy.deepcopy(record)

    def remove(self, record_id: str) -> None:
        with self.store.lock:
            state = self._read()
            left = [r for r in state["records"] if r["id"] != record_id]
            if len(left) == len(state["records"]):
                raise GraphError("unknown_record", f"В банке «{self.name}» нет записи {record_id}")
            state["records"] = left
            self._write(state)

    def clear(self) -> None:
        with self.store.lock:
            state = self._read()
            state["records"] = []
            self._write(state)


def normalize_url(url: str) -> str:
    """Ключ для записи со ссылкой: схема и «www.» отброшены, регистр и хвостовой «/» убраны."""
    text = str(url or "").strip().lower()
    text = re.sub(r"^[a-z]+://", "", text)
    text = re.sub(r"^www\.", "", text)
    return text.split("#")[0].rstrip("/")
