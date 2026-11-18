"""Прогон ворот: слой 1 (код) → слой 2 (независимая оценка) → слой 3 (решение).

Ворота пишут свой журнал в ``.studio/bank/gates.json``. «Принято» записи получают только здесь:
при прохождении ворот черновики и устаревшие записи их вида переходят в «принято».
Кто принимает решение, определяет режим участия (вручную / на воротах / автопилот).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from studio._atomic import atomic_write
from studio.pipeline.graph import ACCEPTED, PROPOSED, STALE

from .bank import DIR, ProjectBank
from .coverage import layer_of
from .errors import PipelineError
from .gates import gate, run_code_layer
from .settings import PipelineSettings

FILE = DIR / "gates.json"
DECISIONS = ("accept", "return", "accept_below")


def in_scope(spec, card: dict) -> bool:
    """Тесты делятся по слоям: интерфейс, бэкенд, сквозные — каждые ворота берут свои."""
    if card["kind"] not in spec.subject:
        return False
    if card["kind"] == "test":
        wanted = {"frontend": "frontend", "backend": "backend", "final": "e2e"}.get(spec.key)
        return layer_of(card) == wanted
    return True


class GateKeeper:
    def __init__(self, bank: ProjectBank, settings: PipelineSettings, project: str | Path):
        self.bank = bank
        self.settings = settings
        self.path = Path(project) / FILE
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self.runs: list[dict] = raw.get("runs", []) if isinstance(raw, dict) else []
        floors = raw.get("floors", {}) if isinstance(raw, dict) else {}
        self.floors: dict[str, int] = floors if isinstance(floors, dict) else {}

    # ------------------------------------------------------------------ файл

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"runs": self.runs, "floors": self.floors}
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=1))

    def run(self, run_id: str) -> dict:
        for item in self.runs:
            if item["id"] == run_id:
                return item
        raise PipelineError("unknown_run", f"Прогона ворот {run_id} нет")

    def last(self, gate_key: str) -> dict | None:
        """Последний прогон ворот; прогоны до сброса (новая волна) не считаются."""
        for index in range(len(self.runs) - 1, int(self.floors.get(gate_key, 0)) - 1, -1):
            if self.runs[index]["gate"] == gate_key:
                return self.runs[index]
        return None

    def reset(self, gate_keys) -> None:
        """Начать ворота заново (новая волна): прежние прогоны остаются, но не считаются."""
        for key in gate_keys:
            self.floors[key] = len(self.runs)
        self._save()

    # ------------------------------------------------------------------ слои

    def open(self, gate_key: str, facts: dict | None = None) -> dict:
        """Начать круг ворот: сразу работает слой 1 (проверки кодом)."""
        gate(gate_key)
        previous = self.last(gate_key)
        round_no = previous["round"] + 1 if previous and previous["state"] == "returned" else 1
        findings = [f.as_dict() for f in run_code_layer(self.bank, gate_key, facts)]
        run = {
            "id": f"g{len(self.runs) + 1}",
            "gate": gate_key,
            "round": round_no,
            "state": "returned" if findings else "rating",
            "reason": "code" if findings else "",
            "code": findings,
            "ratings": None,
            "failing": [],
            "decision": None,
            "by": None,
            "note": "",
            "stuck": False,
            "deficit": None,
            "opened": time.time(),
        }
        self.runs.append(run)
        self._mark_progress(run)
        self._save()
        return run

    def rate(self, run_id: str, ratings: dict) -> dict:
        """Слой 2: оценка по каждому критерию. Нужно не меньше порога и с доказательством."""
        run = self.run(run_id)
        if run["state"] != "rating":
            raise PipelineError(
                "bad_state", "Оценка возможна только после прохождения проверок кодом"
            )
        failing, cleaned = [], {}
        for crit in self.settings.enabled_criteria(run["gate"]):
            item = (ratings or {}).get(crit["key"])
            if not isinstance(item, dict):
                failing.append(
                    {
                        "key": crit["key"],
                        "title": crit["title"],
                        "why": "оценщик не вернул оценку",
                        "missing": True,
                    }
                )
                continue
            try:
                score = int(item.get("score"))
            except (TypeError, ValueError):
                score = -1
            if not 0 <= score <= 10:
                raise PipelineError("bad_score", f"Оценка критерия «{crit['title']}» — от 0 до 10")
            evidence = " ".join(str(item.get("evidence") or "").split())
            cleaned[crit["key"]] = {"score": score, "evidence": evidence}
            if not evidence:
                failing.append(
                    {
                        "key": crit["key"],
                        "title": crit["title"],
                        "score": score,
                        "why": "нет доказательства — оценка не засчитана",
                    }
                )
            elif score < crit["threshold"]:
                failing.append(
                    {
                        "key": crit["key"],
                        "title": crit["title"],
                        "score": score,
                        "threshold": crit["threshold"],
                        "why": "ниже порога",
                    }
                )
        run["ratings"] = cleaned
        run["failing"] = failing
        run["state"] = "returned" if failing else "ready"
        run["reason"] = "rating" if failing else ""
        self._mark_progress(run)
        self._save()
        return run

    # ------------------------------------------------------------------ прогресс доработки

    @staticmethod
    def deficit(run: dict) -> int:
        """Насколько ворота далеки от прохода: чем меньше, тем лучше (0 — проходят)."""
        if run.get("code"):
            return 100 * len(run["code"])  # пока проверки кодом не пройдены, оценки нет
        total = 0
        for item in run.get("failing") or []:
            if "score" in item:
                total += max(1, int(item.get("threshold", 8)) - int(item["score"]))
            else:
                total += int(item.get("threshold", 8))  # оценщик не вернул оценку
        return total

    def streak(self, run: dict) -> list[dict]:
        """Подряд идущие круги этих ворот, не принятые, до ``run`` включительно."""
        floor = int(self.floors.get(run["gate"], 0))
        index = next((i for i, r in enumerate(self.runs) if r["id"] == run["id"]), len(self.runs))
        chain = [run]
        for earlier in reversed(self.runs[floor:index]):
            if earlier["gate"] != run["gate"]:
                continue
            if earlier["state"] != "returned":
                break
            chain.append(earlier)
        chain.reverse()
        return chain

    def previous_failed(self, run: dict) -> dict | None:
        """Прошлый круг этих ворот, если он вернули: его замечания надо проверить заново."""
        chain = self.streak(run)
        return chain[-2] if len(chain) > 1 else None

    def _mark_progress(self, run: dict) -> None:
        """Оценка не растёт ``stall_rounds`` кругов подряд (или кругов слишком много): стоп."""
        run["stuck"] = run["round"] > self.settings.max_rounds
        if run["state"] != "returned":
            return
        run["deficit"] = self.deficit(run)
        chain = self.streak(run)
        values = [r["deficit"] if r.get("deficit") is not None else self.deficit(r) for r in chain]
        best = values.index(min(values))  # первый круг с лучшим результатом
        if len(chain) - 1 - best >= self.settings.stall_rounds:
            run["stuck"] = True
            run["stalled"] = True

    def annotate(self, run_id: str, **fields) -> dict:
        """Дописать в прогон ворот поля (аудит и т. п.)."""
        run = self.run(run_id)
        run.update(fields)
        self._save()
        return run

    # ------------------------------------------------------------------ слой 3

    def needs_human(self, run: dict) -> bool:
        """Кто решает: в ручном режиме и «на воротах» — человек; в автопилоте — агент,
        кроме итоговой приёмки и случая, когда круги доработки закончились."""
        if self.settings.mode != "autopilot":
            return True
        # чистым воротам израсходованные круги не мешают
        stuck = bool(run.get("stuck")) and run.get("state") != "ready"
        return gate(run["gate"]).final or stuck

    def decide(self, run_id: str, decision: str, by: str, note: str = "") -> dict:
        if decision not in DECISIONS:
            raise PipelineError("bad_decision", "Решение: accept, return или accept_below")
        run = self.run(run_id)
        if run["state"] in ("passed", "rejected"):
            raise PipelineError("closed", "Эти ворота уже закрыты")
        if decision == "return":
            run.update(
                state="returned",
                reason=run.get("reason") or "human",
                decision=decision,
                by=by,
                note=note,
            )
            self._save()
            return run
        if run["code"]:
            raise PipelineError("code_failed", "Проверки кодом не пройдены: их обойти нельзя")
        if decision == "accept" and run["state"] != "ready":
            raise PipelineError(
                "not_ready", "Оценка ниже порога: можно вернуть или принять ниже порога"
            )
        if decision == "accept_below" and run["state"] not in ("ready", "returned"):
            raise PipelineError("not_ready", "Сначала должна быть независимая оценка")
        below = decision == "accept_below" and bool(run["failing"])
        run["promoted"] = self._promote(run["gate"], below)
        run.update(state="passed", decision=decision, by=by, note=note, below=below)
        self._save()
        return run

    def auto(self, run_id: str) -> dict | None:
        """Автопилот: если слои 1–2 пройдены и человек не нужен — принять без него."""
        run = self.run(run_id)
        if run["state"] == "ready" and not self.needs_human(run):
            return self.decide(run_id, "accept", "autopilot")
        return None

    def _promote(self, gate_key: str, below: bool) -> list[str]:
        spec = gate(gate_key)
        ids = [
            c["id"]
            for c in self.bank.records()
            if c["status"] in (PROPOSED, STALE) and in_scope(spec, c)
        ]
        self.bank.set_status(ids, ACCEPTED, "gate:" + gate_key, f"ворота «{spec.title}»")
        if below:
            for i in ids:
                self.bank.mark(i, "gate:" + gate_key, below_threshold=True)
        return ids
