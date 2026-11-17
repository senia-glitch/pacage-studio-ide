"""Движок конвейера: собирает банк, настройки, ворота, запросы и вызов агентов в один объект.

Этапы пишут свои шаги функциями в ``stage1``, ``stage2`` и так далее; здесь — общее: прогон
ворот с независимой оценкой и решением по режиму участия.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import prompts
from .bank import ProjectBank
from .changes import ChangeLog
from .coder import AgentCoder, ProjectTests
from .errors import PipelineError
from .gates import GATES, gate
from .keeper import GateKeeper
from .llm import Agents, Llm
from .settings import PipelineSettings


class Pipeline:
    """Один проект: банк знаний, настройки, ворота, запросы на изменение и агенты."""

    def __init__(self, project: str | Path, agents: Agents | None = None):
        self.project = Path(project)
        self.bank = ProjectBank.at(self.project)
        self.settings = PipelineSettings(self.project)
        self.llm = Llm(self.project, self.settings, agents)
        self.keeper = GateKeeper(self.bank, self.settings, self.project)
        self.changes = ChangeLog(self.bank, self.settings, self.project)
        self.coder = AgentCoder(self.llm)  # исполнитель кода (этапы 3–4)
        self.tests = ProjectTests(self.project)  # запуск тестов проекта

    # ------------------------------------------------------------------ ворота

    def run_gate(self, gate_key: str, facts: dict | None = None, *, rate: bool = True) -> dict:
        """Круг ворот: проверки кодом → независимая оценка → решение по режиму участия.

        Человека зовёт ``needs_human`` (``run["human"]``): в ручном режиме и «на воротах» он решает
        всегда, в автопилоте — только на итоговой приёмке и когда круги доработки закончились.
        """
        run = self.keeper.open(gate_key, facts)
        if run["state"] == "rating" and rate:
            ratings = self.evaluate(run)
            run = self.keeper.rate(run["id"], ratings)
        if run["state"] == "ready":
            auto = self.keeper.auto(run["id"])
            if auto is not None:
                run = auto
        run["human"] = run["state"] not in ("passed",) and self.keeper.needs_human(run)
        return run

    def evaluate(self, run: dict) -> dict:
        """Слой 2: независимый оценщик ставит оценку по каждому критерию (JSON с доказательствами)."""
        spec = gate(run["gate"])
        criteria = self.settings.enabled_criteria(run["gate"])
        if not criteria:
            return {}
        subject = prompts.render(self.bank, *spec.subject)
        parents = prompts.render(
            self.bank, "area", "boundary", "constraint", "problem", "requirement"
        )
        listing = "\n".join(
            f"- {c['key']}: {c['title']} — {c['hint']} (порог {c['threshold']})" for c in criteria
        )
        prompt = (
            f"Ворота «{spec.title}». Оцени результат этапа по КАЖДОМУ критерию.\n\n"
            f"Критерии:\n{listing}\n\n"
            f"Что оцениваем:\n{subject}\n\n"
            f"Контекст (идея, границы, проблемы, требования):\n{parents}\n\n"
            'Ответ: {"<ключ критерия>": {"score": 0-10, "evidence": "код записи и что именно"}, ...}'
        )
        data = self.llm.ask_json("evaluator", prompts.SYSTEM["evaluator"], prompt)
        return data if isinstance(data, dict) else {}

    def decide_gate(self, run_id: str, decision: str, note: str = "") -> dict:
        """Решение человека на воротах (принять, вернуть, принять ниже порога)."""
        return self.keeper.decide(run_id, decision, "human", note)

    # ------------------------------------------------------------------ сводка

    def state(self) -> dict:
        """Состояние для главного экрана: режим, записи по этапам, последние ворота, запросы."""
        counts = self.bank.counts()
        gates = {}
        for key in GATES:
            last = self.keeper.last(key)
            gates[key] = (
                {"state": last["state"], "round": last["round"], "id": last["id"]} if last else None
            )
        return {
            "mode": self.settings.mode,
            "counts": {str(stage): data for stage, data in counts.items()},
            "gates": gates,
            "requests": len(self.changes.open_requests()),
            "warnings": self.settings.evaluator_warnings(),
        }

    def export_state(self) -> str:
        return json.dumps(self.state(), ensure_ascii=False, indent=1)


__all__ = ["Pipeline", "PipelineError"]
