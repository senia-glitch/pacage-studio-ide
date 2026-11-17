"""Хранилище графа одной идеи: файлы в папке проекта, операции, журнал, отмена.

Граф меняется только через :meth:`Store.apply` (пакет операций) и :meth:`Store.undo`.
Каждое изменение получает автора и новый номер ревизии и попадает в журнал, поэтому
можно спросить «что изменилось с ревизии N», отменить ревизию и показать историю карточки.

Правила, которые проверяет само ядро (остальное — дело пакета проверки):
связи ведут к существующим карточкам, идентификаторы не повторяются и
не используются заново, правка человека главнее правки агента.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from pathlib import Path

from .errors import ConflictError, GraphError
from .model import (
    ACCEPTED,
    DETAIL,
    HUMAN,
    PROPOSED,
    clean_card_fields,
    clean_edge_kind,
    is_human,
)

FORMAT = 1
DIR_NAME = ".idea-graph"
_STAMPS = ("updated_rev", "human_rev")


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _same(a: dict | None, b: dict | None) -> bool:
    """Равны ли два состояния объекта, если не смотреть на служебные отметки ревизий."""
    if a is None or b is None:
        return a is b
    return {k: v for k, v in a.items() if k not in _STAMPS} == {
        k: v for k, v in b.items() if k not in _STAMPS
    }


class Store:
    """Граф одного проекта в папке ``<папка>/.idea-graph``."""

    def __init__(self, folder: str | os.PathLike):
        self.folder = Path(folder)
        self.dir = self.folder / DIR_NAME
        self.lock = threading.RLock()
        self._state: dict = {}

    # ------------------------------------------------------------------ открытие

    @classmethod
    def exists(cls, folder: str | os.PathLike) -> bool:
        return (Path(folder) / DIR_NAME / "graph.json").is_file()

    @classmethod
    def create(cls, folder: str | os.PathLike, title: str = "") -> "Store":
        """Создать пустой граф проекта. Корневой карточки нет: граф растёт с карточек верхнего уровня."""
        if cls.exists(folder):
            raise GraphError("already_exists", "В этой папке уже есть граф")
        store = cls(folder)
        store.dir.mkdir(parents=True, exist_ok=True)
        store._state = {
            "format": FORMAT, "title": str(title or "").strip()[:200], "revision": 0,
            "next_card": 1, "next_edge": 1, "cards": {}, "edges": {},
        }
        store._save(store._state)
        return store

    @classmethod
    def open(cls, folder: str | os.PathLike) -> "Store":
        store = cls(folder)
        path = store.dir / "graph.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise GraphError("not_found", "В этой папке нет графа") from None
        except ValueError as error:
            raise GraphError("broken_file", f"Файл графа повреждён: {error}") from None
        store._state = {
            "format": raw.get("format", FORMAT),
            "title": str(raw.get("title") or ""),
            "revision": int(raw.get("revision", 0)),
            "next_card": int(raw.get("next_card", 1)),
            "next_edge": int(raw.get("next_edge", 1)),
            "cards": {card["id"]: card for card in raw.get("cards", [])},
            "edges": {edge["id"]: edge for edge in raw.get("edges", [])},
        }
        return store

    def bank(self, name: str):
        """Банк проекта: общие записи, которые пишет один этап и читают другие (см. ``bank.py``)."""
        from .bank import Bank
        return Bank(self, name)

    # ------------------------------------------------------------------ чтение

    @property
    def revision(self) -> int:
        return self._state["revision"]

    @property
    def cards(self) -> dict[str, dict]:
        return self._state["cards"]

    @property
    def edges(self) -> dict[str, dict]:
        return self._state["edges"]

    @property
    def title(self) -> str:
        return self._state.get("title", "")

    def card(self, card_id: str) -> dict:
        try:
            return self.cards[card_id]
        except KeyError:
            raise GraphError("unknown_card", f"Карточки {card_id} нет в графе") from None

    def snapshot(self) -> dict:
        """Копия графа для интерфейса и для пакетов, которые работают кодом."""
        with self.lock:
            return {
                "revision": self.revision,
                "title": self.title,
                "cards": copy.deepcopy(list(self.cards.values())),
                "edges": copy.deepcopy(list(self.edges.values())),
            }

    # ------------------------------------------------------------------ изменение

    def apply(self, ops: list[dict], author: str, base_revision: int | None = None,
              note: str = "") -> dict:
        """Применить пакет операций целиком или не применить ничего.

        ``base_revision`` — ревизия, на которой автор последний раз читал граф. Для агента
        она обязательна по смыслу: если человек после неё менял объект, который агент
        сейчас правит или удаляет, операция отклоняется (``human_changed``).

        Возвращает ``{"revision", "created": {ref: id}, "ids": [...]}``.
        """
        if not isinstance(ops, list) or not ops:
            raise GraphError("invalid_ops", "Ожидался непустой список операций")
        if not isinstance(author, str) or not author:
            raise GraphError("invalid_author", "У изменения должен быть автор")

        def run(work: dict) -> dict:
            refs: dict[str, str] = {}
            ids: list[str] = []
            conflicts: list[str] = []
            for index, op in enumerate(ops):
                if not isinstance(op, dict):
                    raise GraphError("invalid_ops", f"Операция {index + 1}: ожидался объект")
                try:
                    self._one(work, op, author, base_revision, refs, ids, conflicts)
                except GraphError as error:
                    error.message = f"Операция {index + 1} ({op.get('op')}): {error.message}"
                    error.args = (error.message,)
                    raise
            if conflicts:
                raise ConflictError(sorted(set(conflicts)), self.revision)
            return {"created": refs, "ids": ids}

        return self._transaction(author, note, run)

    def undo(self, revision: int, author: str = HUMAN) -> dict:
        """Отменить одну ревизию новой ревизией. Отказ, если её объекты менялись позже."""
        entry = next((e for e in self.journal() if e["rev"] == revision), None)
        if entry is None:
            raise GraphError("unknown_revision", f"Ревизии {revision} нет в журнале")

        def run(work: dict) -> dict:
            for change in reversed(entry["changes"]):
                bucket = work["cards" if change["type"] == "card" else "edges"]
                current = bucket.get(change["id"])
                if not _same(current, change["after"]):
                    raise GraphError(
                        "undo_conflict",
                        f"Ревизию {revision} отменить нельзя: {change['id']} менялся после неё",
                    )
                if change["before"] is None:
                    bucket.pop(change["id"], None)
                else:
                    bucket[change["id"]] = copy.deepcopy(change["before"])
            return {}

        return self._transaction(author, f"отмена ревизии {revision}", run, undo_of=revision)

    # ------------------------------------------------------------------ журнал

    def journal(self) -> list[dict]:
        path = self.dir / "journal.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        out = []
        for line in lines:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    def changes_since(self, revision: int, exclude_author: str | None = None) -> list[dict]:
        """Итоговые изменения после ревизии: по одному на объект, с первым «было» и последним «стало»."""
        merged: dict[tuple, dict] = {}
        for entry in self.journal():
            if entry["rev"] <= revision or entry["author"] == exclude_author:
                continue
            for change in entry["changes"]:
                key = (change["type"], change["id"])
                item = merged.get(key)
                if item is None:
                    merged[key] = {
                        "type": change["type"], "id": change["id"], "before": change["before"],
                        "after": change["after"], "authors": [entry["author"]],
                    }
                else:
                    item["after"] = change["after"]
                    if entry["author"] not in item["authors"]:
                        item["authors"].append(entry["author"])
        return [item for item in merged.values() if not _same(item["before"], item["after"])]

    def deleted_cards(self) -> list[dict]:
        """Карточки, которые были удалены и сейчас отсутствуют в графе (новые — первыми)."""
        found: dict[str, dict] = {}
        for entry in self.journal():
            for change in entry["changes"]:
                if change["type"] != "card":
                    continue
                if change["after"] is None and change["before"] is not None:
                    found[change["id"]] = {
                        "card": change["before"], "rev": entry["rev"], "author": entry["author"],
                        "ts": entry["ts"],
                    }
                elif change["after"] is not None:
                    found.pop(change["id"], None)
        return sorted(found.values(), key=lambda item: -item["rev"])

    def card_history(self, card_id: str) -> list[dict]:
        out = []
        for entry in self.journal():
            for change in entry["changes"]:
                if change["type"] == "card" and change["id"] == card_id:
                    out.append({
                        "rev": entry["rev"], "ts": entry["ts"], "author": entry["author"],
                        "note": entry.get("note", ""), "before": change["before"],
                        "after": change["after"],
                    })
        return out

    def undone_revisions(self) -> set[int]:
        return {e["undo_of"] for e in self.journal() if e.get("undo_of")}

    # ------------------------------------------------------------------ раскладка холста

    def layout(self) -> dict:
        try:
            data = json.loads((self.dir / "layout.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_layout(self, data: dict) -> None:
        if not isinstance(data, dict):
            raise GraphError("invalid_layout", "Раскладка — объект")
        with self.lock:
            _atomic_write(self.dir / "layout.json", json.dumps(data, ensure_ascii=False))

    # ------------------------------------------------------------------ внутреннее

    def _transaction(self, author: str, note: str, run, undo_of: int | None = None) -> dict:
        with self.lock:
            work = copy.deepcopy(self._state)
            extra = run(work) or {}
            self._check(work)
            revision = self.revision + 1
            changes = []
            for kind, bucket in (("card", "cards"), ("edge", "edges")):
                old, new = self._state[bucket], work[bucket]
                for key in list(old.keys() | new.keys()):
                    before, after = old.get(key), new.get(key)
                    if _same(before, after):
                        if after is not None and before is not None:
                            new[key] = before          # служебные отметки не трогаем
                        continue
                    if after is not None:
                        after["updated_rev"] = revision
                        if is_human(author):
                            after["human_rev"] = revision
                    changes.append({"type": kind, "id": key, "before": before, "after": after})
            if not changes:
                return {"revision": self.revision, "changed": False, **extra}
            changes.sort(key=lambda c: (c["type"] != "card", int(c["id"][1:])))
            work["revision"] = revision
            entry = {"rev": revision, "ts": time.time(), "author": author, "note": note or "",
                     "changes": changes}
            if undo_of:
                entry["undo_of"] = undo_of
            self._save(work)
            with open(self.dir / "journal.jsonl", "a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._state = work
            return {"revision": revision, "changed": True, **extra}

    def _save(self, state: dict) -> None:
        data = {
            "format": FORMAT, "title": state.get("title", ""), "revision": state["revision"], "next_card": state["next_card"],
            "next_edge": state["next_edge"],
            "cards": sorted(state["cards"].values(), key=lambda c: int(c["id"][1:])),
            "edges": sorted(state["edges"].values(), key=lambda e: int(e["id"][1:])),
        }
        _atomic_write(self.dir / "graph.json", json.dumps(data, ensure_ascii=False, indent=1))

    @staticmethod
    def _check(work: dict) -> None:
        seen = set()
        for edge in work["edges"].values():
            if edge["from"] not in work["cards"] or edge["to"] not in work["cards"]:
                raise GraphError("dangling_edge", f"Связь {edge['id']} ведёт к несуществующей карточке")
            if edge["from"] == edge["to"]:
                raise GraphError("self_edge", "Связь карточки с самой собой не допускается")
            key = (edge["from"], edge["to"], edge["kind"])
            if key in seen:
                raise GraphError("duplicate_edge", f"Такая связь уже есть: {edge['from']} → {edge['to']} ({edge['kind']})")
            seen.add(key)

    @staticmethod
    def _new_card(work: dict, fields: dict, author: str) -> dict:
        card_id = f"c{work['next_card']}"
        work["next_card"] += 1
        card = {"id": card_id, **fields, "author": author, "created_rev": work["revision"] + 1,
                "updated_rev": 0, "human_rev": 0}
        work["cards"][card_id] = card
        return card

    @staticmethod
    def _new_edge(work: dict, source: str, target: str, kind: str, author: str) -> dict:
        edge_id = f"e{work['next_edge']}"
        work["next_edge"] += 1
        edge = {"id": edge_id, "from": source, "to": target, "kind": kind, "author": author,
                "updated_rev": 0, "human_rev": 0}
        work["edges"][edge_id] = edge
        return edge

    def _one(self, work, op, author, base, refs, ids, conflicts) -> None:
        name = op.get("op")

        def resolve(value, what="карточка"):
            if isinstance(value, str) and value.startswith("@"):
                if value[1:] not in refs:
                    raise GraphError("unknown_ref", f"Временное имя {value} не объявлено раньше в пакете")
                return refs[value[1:]]
            if not isinstance(value, str) or not value:
                raise GraphError("invalid_ops", f"Не указан идентификатор: {what}")
            return value

        def existing(bucket, value, what):
            key = resolve(value, what)
            if key not in work[bucket]:
                raise GraphError("unknown_" + ("card" if bucket == "cards" else "edge"),
                                 f"В графе нет: {what} {key}")
            return work[bucket][key]

        def guard(item):
            """Правка человека главнее: агент не трогает то, что человек изменил после чтения."""
            if not is_human(author) and base is not None and item.get("human_rev", 0) > base:
                conflicts.append(item["id"])

        if name == "add_card":
            raw = {k: op[k] for k in ("kind", "title", "body", "status", "decision", "attrs") if k in op}
            raw.setdefault("status", ACCEPTED if is_human(author) else PROPOSED)
            fields = clean_card_fields(raw, partial=False)
            card = self._new_card(work, fields, author)
            ids.append(card["id"])
            if op.get("ref"):
                refs[str(op["ref"])] = card["id"]
            links = list(op.get("links") or [])
            if op.get("parent"):
                links.insert(0, {"to": op["parent"], "kind": op.get("edge_kind") or DETAIL})
            for link in links:
                target = existing("cards", link.get("to"), "карточка")
                self._new_edge(work, card["id"], target["id"], clean_edge_kind(link.get("kind")), author)
        elif name == "update_card":
            card = existing("cards", op.get("id"), "карточка")
            guard(card)
            raw = {k: op[k] for k in ("kind", "title", "body", "status", "decision") if k in op}
            fields = clean_card_fields(raw, partial=True)
            card.update(fields)
            if isinstance(op.get("attrs"), dict):
                attrs = dict(card.get("attrs") or {})
                for key, value in op["attrs"].items():
                    if value is None:
                        attrs.pop(key, None)
                    else:
                        attrs[key] = value
                card["attrs"] = attrs
        elif name == "delete_card":
            card = existing("cards", op.get("id"), "карточка")
            guard(card)
            del work["cards"][card["id"]]
            for edge in [e for e in work["edges"].values() if card["id"] in (e["from"], e["to"])]:
                del work["edges"][edge["id"]]
        elif name == "add_edge":
            source = existing("cards", op.get("from"), "карточка «from»")
            target = existing("cards", op.get("to"), "карточка «to»")
            edge = self._new_edge(work, source["id"], target["id"], clean_edge_kind(op.get("kind")), author)
            ids.append(edge["id"])
            if op.get("ref"):
                refs[str(op["ref"])] = edge["id"]
        elif name == "update_edge":
            edge = existing("edges", op.get("id"), "связь")
            guard(edge)
            if "kind" in op:
                edge["kind"] = clean_edge_kind(op["kind"])
            for end in ("from", "to"):
                if end in op:
                    edge[end] = existing("cards", op[end], f"карточка «{end}»")["id"]
        elif name == "delete_edge":
            edge = existing("edges", op.get("id"), "связь")
            guard(edge)
            del work["edges"][edge["id"]]
        else:
            raise GraphError(
                "invalid_ops",
                "Неизвестная операция. Есть: add_card, update_card, delete_card, add_edge, "
                "update_edge, delete_edge",
            )
