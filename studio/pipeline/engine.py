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
        from .stage5 import Launcher

        self.launcher = Launcher()  # запуск приложения (этап 5)
        from . import stack
        from .stackcheck import StackChecker

        self.stack = StackChecker()  # проверки стека сканерами студии (ворота 3–4)
        self.stack_init = stack.init_stack  # генераторы стека (fb init и др.), если проекта ещё нет

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
            self._audit(run)
        if run["state"] == "ready":
            auto = self.keeper.auto(run["id"])
            if auto is not None:
                run = auto
        run["human"] = run["state"] not in ("passed",) and self.keeper.needs_human(run)
        self._more_waves(run)
        return run

    def _more_waves(self, run: dict) -> None:
        """Бэкенд волны принят: если есть следующая волна, ворота этапов 3–4 начинаются заново."""
        if run["gate"] == "backend" and run["state"] == "passed":
            from . import waves

            waves.advance(self)

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
        system = prompts.SYSTEM["evaluator"]
        prompt += self._recheck_note(run)
        try:
            data = self.llm.ask_json("evaluator", system, prompt)
        except PipelineError as error:
            run["eval_note"] = f"ответ не удалось разобрать ({error.message})"
            return {}
        data = data if isinstance(data, dict) else {}
        missing = [c for c in criteria if not isinstance(data.get(c["key"]), dict)]
        if missing:  # слабая модель ответила не по всем критериям: просим один раз дополнить
            names = ", ".join(f"{c['key']} ({c['title']})" for c in missing)
            again_prompt = (
                f"{prompt}\n\nВ прошлом ответе нет оценок по критериям: {names}. "
                "Ответь ТОЛЬКО JSON с оценками по ВСЕМ критериям."
            )
            try:
                again = self.llm.ask_json("evaluator", system, again_prompt)
            except PipelineError:
                again = None
            if isinstance(again, dict):
                for c in missing:
                    if isinstance(again.get(c["key"]), dict):
                        data[c["key"]] = again[c["key"]]
            left = [c["title"] for c in missing if not isinstance(data.get(c["key"]), dict)]
            if left:
                run["eval_note"] = "в ответе модели нет оценок по критериям: " + ", ".join(left)
        return data

    def _audit(self, run: dict) -> None:
        """Ворота вернули по оценке: критический разбор причин (что именно исправить автору)."""
        from . import advice, audit

        if run["state"] == "returned" and not advice.evaluator_failed(run):
            audit.make(self, run)

    def _recheck_note(self, run: dict) -> str:
        """Прошлый круг вернули: оценщик проверяет, устранены ли дефекты по существу, а не словами."""
        from . import audit

        before = self.keeper.previous_failed(run)
        if before is None:
            return ""
        remarks = audit.lines(before) or [
            f"- {f['title']}: {f['why']}" for f in before.get("failing") or []
        ]
        remarks += [f"- {f['message']}" for f in before.get("code") or []]
        if not remarks:
            return ""
        return (
            "\n\nПрошлый круг этих ворот не прошёл, автор доработал записи. Замечания были:\n"
            + "\n".join(remarks)
            + "\nПроверь по существу, что каждое устранено, а не переписано другими словами; "
            "за формулировки оценку не повышай, новые дефекты называй."
        )

    def decide_gate(self, run_id: str, decision: str, note: str = "") -> dict:
        """Решение человека на воротах (принять, вернуть, принять ниже порога)."""
        run = self.keeper.decide(run_id, decision, "human", note)
        self._more_waves(run)
        return run

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
