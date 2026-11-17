"""Запрос на изменение принятого (схема v3, пункт 3).

Принятое напрямую не меняется. Любая правка — вы, агент или ворота — идёт запросом:
владелец (этап, где запись принята) → расчёт затронутого по связям → решение по режиму участия →
новая версия записи, затронутое получает статус «устарело» и перепроверяется воротами своего
этапа, всё остальное остаётся принятым. Отклонённый запрос уходит в архив, принятое не тронуто.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from studio._atomic import atomic_write
from studio.pipeline.graph import ACCEPTED, STALE

from .bank import DIR, ProjectBank
from .errors import PipelineError
from .kinds import KINDS, stage_of
from .settings import PipelineSettings

FILE = DIR / "changes.json"
BIG_KINDS = (
    "problem",
    "requirement",
    "scenario",
    "table",
    "call",
)  # меняют схему БД, контракт, смысл


class ChangeLog:
    def __init__(self, bank: ProjectBank, settings: PipelineSettings, project: str | Path):
        self.bank = bank
        self.settings = settings
        self.path = Path(project) / FILE
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self.items: list[dict] = raw.get("requests", []) if isinstance(raw, dict) else []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps({"requests": self.items}, ensure_ascii=False, indent=1))

    def get(self, request_id: str) -> dict:
        for item in self.items:
            if item["id"] == request_id:
                return item
        raise PipelineError("unknown_request", f"Запроса {request_id} нет")

    # ------------------------------------------------------------------ расчёт

    def impact(self, targets: list[str]) -> list[str]:
        """Всё, что опирается на изменяемые записи (кроме них самих): их придётся перепроверить."""
        seen: dict[str, None] = {}
        for target in targets:
            for card in self.bank.descendants(target):
                if card["id"] not in targets:
                    seen[card["id"]] = None
        return list(seen)

    def _size(self, targets: list[str], impact: list[str]) -> str:
        """«Мелкая правка» — затронуто мало записей и не задеты смысл, схема БД и контракт."""
        affected = [self.bank.get(i) for i in [*targets, *impact]]
        small = len(affected) <= self.settings.quick_fix_limit and not any(
            c["kind"] in BIG_KINDS for c in affected
        )
        return "small" if small else "large"

    # ------------------------------------------------------------------ жизнь запроса

    def open(
        self,
        text: str,
        source: str,
        *,
        patches: dict[str, dict] | None = None,
        new: dict | None = None,
    ) -> dict:
        """Подать запрос: правка принятых записей (``patches``) или новая запись (``new``)."""
        text = (text or "").strip()
        if not text:
            raise PipelineError("empty_request", "Опишите, что нужно изменить и зачем")
        if bool(patches) == bool(new):
            raise PipelineError("bad_request", "Нужно либо изменить записи, либо добавить новую")
        request: dict = {
            "id": f"r{len(self.items) + 1}",
            "text": text,
            "source": source,
            "status": "open",
            "created": time.time(),
            "decided_by": None,
            "note": "",
        }
        if patches:
            targets = list(patches)
            for target in targets:
                attrs = self.bank.get(target).get("attrs") or {}
                if attrs.get("from_code") and not attrs.get("described"):
                    raise PipelineError(
                        "undescribed",
                        "Запись «из кода» без описания менять нельзя: сначала внедрите её "
                        "(опишите: сценарий, требование) или отклоните",
                    )
            impact = self.impact(targets)
            request.update(
                type="edit",
                targets=targets,
                patches=patches,
                impact=impact,
                owner_stage=min(stage_of(self.bank.get(t)["kind"]) for t in targets),
                size=self._size(targets, impact),
            )
        else:
            kind = new.get("kind", "")
            spec = KINDS.get(kind)
            if spec is None:
                raise PipelineError("unknown_kind", f"Такого вида записи нет: {kind!r}")
            clash = self.bank.find(kind, new.get("title", ""))
            request.update(
                type="new",
                new=new,
                targets=[],
                impact=[],
                owner_stage=spec.stage,
                size="large",
                conflicts=[clash["id"]] if clash else [],
            )
        self.items.append(request)
        self._save()
        return request

    def needs_human(self) -> bool:
        """Решает режим: вручную и «на воротах» — человек подтверждает, в автопилоте — агент."""
        return self.settings.mode != "autopilot"

    def decide(
        self, request_id: str, decision: str, by: str, note: str = "", keep: list[str] | None = None
    ) -> dict:
        """Принять или отклонить. ``keep`` — затронутое, которого правка на деле не касается."""
        if decision not in ("accept", "reject"):
            raise PipelineError("bad_decision", "Решение: accept или reject")
        request = self.get(request_id)
        if request["status"] != "open":
            raise PipelineError("closed", "Запрос уже решён")
        if decision == "reject":
            request.update(status="rejected", decided_by=by, note=note)
            self._save()
            return request
        if request["type"] == "edit":
            for target, patch in request["patches"].items():
                self.bank.revise(
                    target,
                    by,
                    title=patch.get("title"),
                    body=patch.get("body"),
                    attrs=patch.get("attrs"),
                    note=f"запрос {request_id}",
                )
            keep_set = set(keep or [])
            stale = [
                i
                for i in request["impact"]
                if i not in keep_set and self.bank.get(i)["status"] == ACCEPTED
            ]
            self.bank.set_status(stale, STALE, by, f"запрос {request_id}: перепроверка")
            request["stale"] = stale
        else:
            if request.get("conflicts"):
                raise PipelineError(
                    "conflict",
                    "Новая запись повторяет существующую: переформулируйте или отклоните запрос",
                )
            spec = request["new"]
            card = self.bank.add(
                spec["kind"],
                spec["title"],
                parents=spec.get("parents", ()),
                body=spec.get("body", ""),
                attrs=spec.get("attrs"),
                author=by,
            )
            request["result"] = card["id"]
        request.update(status="accepted", decided_by=by, note=note)
        self._save()
        return request

    def open_requests(self) -> list[dict]:
        return [r for r in self.items if r["status"] == "open"]
