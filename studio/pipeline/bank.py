"""Банк знаний проекта: записи по видам, связи «происходит из», статусы и версии.

Работает поверх ядра графа (``studio.pipeline.graph``): журнал, отмена и правило «правка человека
главнее» достаются бесплатно. Здесь — правила конвейера:

* запись создаётся только с допустимым родителем, а у видов с обязательным родителем — с ним;
* новая запись — «черновик» (статус ``proposed``); «принято» ставят только ворота;
* принятую запись меняют новой версией (``attrs.version``), прежняя остаётся в журнале.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from studio.pipeline.graph import ACCEPTED, PROPOSED, REJECTED, STALE, GraphError, Store

from .errors import PipelineError
from .kinds import KINDS, SOURCE, kind_of

DIR = Path(".studio") / "bank"
LIVE = (PROPOSED, ACCEPTED, STALE)  # «живые» записи: всё, кроме отклонённых


class BankStore(Store):
    """Граф банка лежит в ``<проект>/.studio/bank/records``."""

    dir_name = "records"


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


class ProjectBank:
    """Один банк на проект."""

    def __init__(self, store: BankStore):
        self.store = store

    # ------------------------------------------------------------------ открытие

    @classmethod
    def at(cls, project: str | Path, *, create: bool = True) -> ProjectBank:
        folder = Path(project) / DIR
        if BankStore.exists(folder):
            return cls(BankStore.open(folder))
        if not create:
            raise PipelineError("no_bank", "В этом проекте ещё нет банка знаний")
        folder.mkdir(parents=True, exist_ok=True)
        return cls(BankStore.create(folder, title="Банк знаний"))

    @classmethod
    def exists(cls, project: str | Path) -> bool:
        return BankStore.exists(Path(project) / DIR)

    @property
    def folder(self) -> Path:
        return self.store.folder

    @property
    def revision(self) -> int:
        return self.store.revision

    # ------------------------------------------------------------------ чтение

    def get(self, record_id: str) -> dict:
        try:
            return self.store.card(record_id)
        except GraphError:
            raise PipelineError("unknown_record", f"В банке нет записи {record_id}") from None

    def records(
        self,
        kind: str | None = None,
        *,
        status: str | None = None,
        stage: int | None = None,
        live: bool = True,
    ) -> list[dict]:
        """Записи по виду, статусу и этапу. По умолчанию отклонённые не показываются."""
        out = []
        for card in self.store.cards.values():
            if kind is not None and card["kind"] != kind:
                continue
            if status is not None and card["status"] != status:
                continue
            if live and card["status"] == REJECTED and status != REJECTED:
                continue
            spec = KINDS.get(card["kind"])
            if stage is not None and (spec is None or spec.stage != stage):
                continue
            out.append(card)
        return sorted(out, key=lambda c: int(c["id"][1:]))

    def _links(self) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        parents: dict[str, list[str]] = {}
        children: dict[str, list[str]] = {}
        for edge in self.store.edges.values():
            if edge["kind"] != SOURCE:
                continue
            parents.setdefault(edge["from"], []).append(edge["to"])
            children.setdefault(edge["to"], []).append(edge["from"])
        return parents, children

    def parents(self, record_id: str, *, live: bool = True) -> list[dict]:
        parents, _ = self._links()
        cards = [self.store.card(i) for i in parents.get(record_id, [])]
        return [c for c in cards if not live or c["status"] != REJECTED]

    def children(self, record_id: str, *, live: bool = True) -> list[dict]:
        _, children = self._links()
        cards = [self.store.card(i) for i in children.get(record_id, [])]
        return [c for c in cards if not live or c["status"] != REJECTED]

    def descendants(self, record_id: str, *, live: bool = True) -> list[dict]:
        """Всё, что происходит из записи (на что она «давит»): дети, их дети и так далее."""
        return self._walk(record_id, 1, live)

    def ancestors(self, record_id: str, *, live: bool = True) -> list[dict]:
        """Всё, из чего запись происходит, вплоть до источников."""
        return self._walk(record_id, 0, live)

    def _walk(self, record_id: str, direction: int, live: bool) -> list[dict]:
        parents, children = self._links()
        step = children if direction else parents
        seen: set[str] = set()
        queue = deque([record_id])
        order: list[str] = []
        while queue:
            current = queue.popleft()
            for nxt in step.get(current, []):
                if nxt in seen or nxt == record_id:
                    continue
                seen.add(nxt)
                order.append(nxt)
                queue.append(nxt)
        cards = [self.store.card(i) for i in order]
        return [c for c in cards if not live or c["status"] != REJECTED]

    def chain(self, record_id: str) -> list[dict]:
        """Объяснение «зачем это здесь»: запись и первый родитель на каждом шаге до источника."""
        path = [self.get(record_id)]
        seen = {record_id}
        while True:
            parents = [p for p in self.parents(path[-1]["id"]) if p["id"] not in seen]
            if not parents:
                return path
            path.append(parents[0])
            seen.add(parents[0]["id"])

    def find(self, kind: str, title: str) -> dict | None:
        """Живая запись вида ``kind`` с таким названием (без учёта регистра и пробелов)."""
        key = _norm(title)
        for card in self.records(kind):
            if _norm(card["title"]) == key:
                return card
        return None

    # ------------------------------------------------------------------ создание

    def add(
        self,
        kind: str,
        title: str,
        *,
        parents: list[str] | tuple[str, ...] = (),
        body: str = "",
        attrs: dict | None = None,
        author: str = "agent",
        status: str = PROPOSED,
    ) -> dict:
        """Создать запись. Без допустимого родителя запись не принимается: «висящее» не нужно."""
        spec = kind_of(kind)
        cards = [self.get(p) for p in dict.fromkeys(parents)]
        for parent in cards:
            if parent["kind"] not in spec.parents:
                raise PipelineError(
                    "bad_parent",
                    f"«{spec.title}» не может происходить из «{parent['title']}» "
                    f"({KINDS[parent['kind']].title}). Допустимо: "
                    + (", ".join(KINDS[p].title for p in spec.parents) or "ничего"),
                )
            if parent["status"] == REJECTED:
                raise PipelineError("rejected_parent", f"Родитель «{parent['title']}» отклонён")
        if spec.required and not cards:
            raise PipelineError(
                "parent_required",
                f"У записи «{spec.title}» должен быть родитель: "
                + ", ".join(KINDS[p].title for p in spec.parents),
            )
        data = dict(attrs or {})
        data.setdefault("version", 1)
        result = self.store.apply(
            [
                {
                    "op": "add_card",
                    "kind": kind,
                    "title": title,
                    "body": body,
                    "status": status,
                    "attrs": data,
                    "links": [{"to": p["id"], "kind": SOURCE} for p in cards],
                }
            ],
            author,
        )
        return self.get(result["ids"][0])

    def add_from_code(
        self, kind: str, title: str, *, body: str = "", attrs: dict | None = None
    ) -> dict:
        """Запись «из кода» (режим «Развитие»): факт, который уже есть в проекте.

        Родителя у неё пока нет: пока запись не описана (не внедрена в цепочку), менять её нельзя,
        а проверки ворот её «висящей» не считают. Описали — отметка ``described`` ставится сама.
        """
        kind_of(kind)
        data = dict(attrs or {})
        data.update(from_code=True, described=False)
        data.setdefault("version", 1)
        result = self.store.apply(
            [
                {
                    "op": "add_card",
                    "kind": kind,
                    "title": title,
                    "body": body,
                    "status": ACCEPTED,
                    "attrs": data,
                }
            ],
            "scanner",
        )
        return self.get(result["ids"][0])

    def link(self, child_id: str, parent_id: str, author: str = "agent") -> None:
        """Привязать запись к ещё одному родителю («внедрить» висящее)."""
        child, parent = self.get(child_id), self.get(parent_id)
        if parent["kind"] not in kind_of(child["kind"]).parents:
            raise PipelineError(
                "bad_parent",
                f"«{KINDS[child['kind']].title}» не может происходить из "
                f"«{KINDS[parent['kind']].title}»",
            )
        if any(p["id"] == parent_id for p in self.parents(child_id, live=False)):
            return
        self.store.apply(
            [{"op": "add_edge", "from": child_id, "to": parent_id, "kind": SOURCE}], author
        )
        if (child.get("attrs") or {}).get("from_code") and not child["attrs"].get("described"):
            self.mark(child_id, author, described=True)

    # ------------------------------------------------------------------ статусы и версии

    def set_status(
        self, ids: list[str] | tuple[str, ...], status: str, author: str = "agent", note: str = ""
    ) -> int:
        """Поставить статус пачке записей одной ревизией. Возвращает, сколько записей изменилось."""
        ops = [
            {"op": "update_card", "id": i, "status": status}
            for i in dict.fromkeys(ids)
            if self.get(i)["status"] != status
        ]
        if not ops:
            return 0
        self.store.apply(ops, author, note=note)
        return len(ops)

    def mark(self, record_id: str, author: str = "agent", **attrs) -> None:
        """Дописать отметки записи (``None`` — убрать отметку)."""
        self.store.apply([{"op": "update_card", "id": record_id, "attrs": attrs}], author)

    def revise(
        self,
        record_id: str,
        author: str,
        *,
        title: str | None = None,
        body: str | None = None,
        attrs: dict | None = None,
        note: str = "",
    ) -> dict:
        """Новая версия записи: текст меняется, номер версии растёт, прежнее остаётся в журнале."""
        card = self.get(record_id)
        data = dict(attrs or {})
        data["version"] = int(card.get("attrs", {}).get("version", 1)) + 1
        op: dict = {"op": "update_card", "id": record_id, "attrs": data}
        if title is not None:
            op["title"] = title
        if body is not None:
            op["body"] = body
        self.store.apply([op], author, note=note or "новая версия")
        return self.get(record_id)

    def reject(self, record_id: str, author: str = "agent", note: str = "") -> None:
        """Отклонить запись: она уходит в архив и не считается ни родителем, ни «висящей»."""
        self.set_status([record_id], REJECTED, author, note)

    def history(self, record_id: str) -> list[dict]:
        return self.store.card_history(record_id)

    # ------------------------------------------------------------------ сводки

    def counts(self) -> dict:
        """Сколько записей по этапам и статусам — для главной «Карты системы»."""
        out: dict[int, dict[str, int]] = {}
        for card in self.store.cards.values():
            spec = KINDS.get(card["kind"])
            if spec is None:
                continue
            bucket = out.setdefault(spec.stage, {})
            bucket[card["status"]] = bucket.get(card["status"], 0) + 1
        return out

    def constitution(self) -> list[dict]:
        """«Конституция»: принятые границы, ограничения, словарь и стиль — в каждое ТЗ."""
        return [
            c
            for c in self.records(status=ACCEPTED)
            if KINDS.get(c["kind"]) and KINDS[c["kind"]].constitution
        ]

    def stale(self, stage: int | None = None) -> list[dict]:
        return self.records(status=STALE, stage=stage)
