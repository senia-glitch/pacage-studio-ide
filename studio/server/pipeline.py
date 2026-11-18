"""Конвейер студии v3 в HTTP: маршруты ``/api/pipeline/*`` (только человек, агентам закрыто).

Долгие шаги (вызов агентов) идут фоновой работой: ``POST /run`` сразу отвечает, а экран спрашивает
``GET /state`` и видит, чем занят конвейер. Одновременно выполняется одна работа.
"""

from __future__ import annotations

import contextlib
import threading
import time
from pathlib import Path

from studio.pipeline import (
    GATES,
    KINDS,
    MODE_NAMES,
    ROLES,
    STAGE_GATES,
    STAGE_NAMES,
    Pipeline,
    PipelineError,
    ProjectBank,
    advice,
    clarify,
    debt,
    entry,
    flow,
    integrate,
    maps,
    reset,
    review,
    stack,
    stage1,
    stage2,
    stage3,
    waves,
)
from studio.pipeline.graph import GraphError
from studio.pipeline.llm import CLAUDE_BY_TIER, Agents
from studio.pipeline.roles import AGENT_LABELS, TIER_NAMES

PREFIX = "/api/pipeline"

# Шаги этапов, которые ещё не подключены, — для показа на схеме (состояние «planned»).
PLANNED = {
    3: ["Стиль и каркас", "План интерфейса", "Моки и контракт", "ТЗ из банка",
        "Тесты из критериев", "Реализация"],
    4: ["План бэкенда", "Модели из схемы БД", "ТЗ из банка", "Тесты из критериев",
        "Реализация", "Замена моков"],
    5: ["Запуск"],
}  # fmt: skip


def _brief(card: dict) -> dict:
    spec = KINDS.get(card["kind"])
    return {
        "id": card["id"],
        "kind": card["kind"],
        "kind_title": spec.title if spec else card["kind"],
        "stage": spec.stage if spec else 0,
        "title": card["title"],
        "body": card.get("body", ""),
        "status": card["status"],
        "attrs": card.get("attrs") or {},
        "constitution": bool(spec and spec.constitution),
        "parent_kinds": list(spec.parents) if spec else [],
        "required_parent": bool(spec and spec.required),
    }


def _summary(result) -> str:
    """Короткая фраза о том, что сделал шаг, для строки состояния экрана."""
    if isinstance(result, dict):
        if "ran" in result:
            ran = result["ran"]
            return "выполнено: " + ", ".join(ran) if ran else "проект уже на стеке"
        if result.get("gate"):
            why = advice.short(result)
            head = f"ворота «{GATES[result['gate']].title}»: {result['state']}"
            back = result.get("debt")
            if back:
                head = f"на улучшение возвращено записей: {len(back['records'])}; " + head
            done = result.get("integrated") or {}
            if done:
                head = (
                    f"внедрено: {len(done['linked'])}, создано родителей: {len(done['created'])}, "
                    f"отклонено: {len(done['rejected'])}; " + head
                )
            return head + (f" — {why}" if why else "")
        if (result.get("decided") or {}).get("added"):
            idea = result.get("idea") or {}
            where = f"ворота «Идеи» ждут вашего решения: {idea['blocked']}" if idea.get("blocked") else "«Идея» приняла"
            return f"заявка этапа отправлена в «Идею» (записей: {len(result['decided']['added'])}); {where}"
        if result.get("asked") or result.get("waiting"):
            return "студия задала вопрос: ответьте на него, и она спросит дальше или запишет"
        if "refined" in result and "created" not in result:
            return f"ответ учтён, изменено записей: {result['refined']}"
        if "discussed" in result:
            reply = result["discussed"]
            if reply["questions"] or reply["conflicts"]:
                note = ": есть вопросы"
            else:
                note = ": план правок согласован" if reply["ready"] else ""
            return "студия обсудила уточнение" + note
        if "revised" in result:
            text = (
                f"изменено записей: {len(result['revised'])}, добавлено: {len(result['added'])}, "
                f"отклонено: {len(result['rejected'])}"
            )
            if result.get("skipped"):
                text += f"; не внесено: {len(result['skipped'])}"
            if result.get("questions"):
                text += f"; вопросов к вам: {len(result['questions'])}"
            return text
        created = result.get("created")
        if created is None:
            created = result.get("designed")
        if created is not None:
            skipped = len(result.get("skipped") or [])
            note = f", отклонено без родителя: {skipped}" if skipped else ""
            return f"создано записей: {len(created)}{note}"
    if isinstance(result, list):
        if not result or (
            isinstance(result[0], dict) and ("step" in result[0] or "stop" in result[0])
        ):
            text = f"выполнено шагов: {len(result)}"
            fixed = sum(len(e.get("fixed") or []) for e in result if isinstance(e, dict))
            steps = [e for e in result if isinstance(e, dict)]
            returned = sum(len(e["debt"]["records"]) for e in steps if e.get("debt"))
            if returned:
                text += f"; возвращено на улучшение записей ниже порога: {returned}"
            tickets = sum(len((e.get("conveyor") or {}).get("sent") or []) for e in steps)
            if tickets:
                text += f"; заявок этапа в «Идею»: {tickets}"
            dropped = sum(len((e.get("rework") or {}).get("dropped") or []) for e in steps)
            if dropped:
                text += f"; отклонено по аудиту: {dropped}"
            rounds = sum(1 for e in steps if e.get("rework"))
            upstream = sum(len((e.get("rework") or {}).get("upstream") or []) for e in steps)
            if rounds:
                text += f"; кругов доработки по аудиту: {rounds}"
            if upstream:
                text += f"; запросов на изменение принятого: {upstream}"
            if fixed:
                text += f"; отклонено повторов: {fixed}"
            stop = next((e["stop"] for e in reversed(steps) if e.get("stop")), "")
            return text + (f". Остановка: {stop}" if stop else "")
        return f"создано записей: {len(result)}"
    return "готово"


class PipelineService:
    def __init__(self, root: Path, agents: Agents | None = None, on_reset=None):
        self.root = Path(root)
        self.on_reset = on_reset  # перед сносом проекта: остановить запущенные серверы
        self.agents = agents or Agents()
        self.lock = threading.RLock()
        self._pl: Pipeline | None = None
        self.job: dict | None = None
        self._stop = False

    # ------------------------------------------------------------------ доступ

    def started(self) -> bool:
        return ProjectBank.exists(self.root)

    def pl(self, create: bool = False) -> Pipeline:
        with self.lock:
            if self._pl is None:
                if not create and not self.started():
                    raise PipelineError("not_started", "Конвейер в этом проекте ещё не начат")
                self._pl = Pipeline(self.root, self.agents)
            return self._pl

    def close(self) -> None:
        self._stop = True

    # ------------------------------------------------------------------ чтение

    def state(self) -> dict:
        base = {
            "started": self.started(),
            "mode_names": MODE_NAMES,
            "themes": stage3.THEMES,
            "entry": entry.detect(self.root),
            "stage_names": {str(k): v for k, v in STAGE_NAMES.items()},
            "job": self.job_view(),
        }
        if not base["started"]:
            return {**base, "mode": "gates", "stages": [], "requests": 0, "warnings": []}
        pl = self.pl()
        steps = flow.all_steps(pl)
        stages = []
        for number in range(1, 6):
            items = steps.get(number)
            if items is None:
                items = [
                    {"num": f"{number}.{i}", "key": f"planned_{number}_{i}", "title": title,
                     "state": "planned", "role": None, "hint": ""}
                    for i, title in enumerate(PLANNED[number], 1)
                ]  # fmt: skip
            gates = []
            for key in STAGE_GATES[number]:
                last = pl.keeper.last(key)
                gates.append(
                    {"key": key, "title": GATES[key].title,
                     "state": last["state"] if last else "none",
                     "round": last["round"] if last else 0,
                     "run": last["id"] if last else None,
                     "stuck": bool(last and last.get("stuck")),
                     "why": advice.short(last) if last and last["state"] != "passed" else "",
                     "human": bool(
                         last and last["state"] != "passed" and pl.keeper.needs_human(last)
                     ),
                     "wired": number in flow.STAGES}
                )  # fmt: skip
            counts: dict[str, int] = {}
            for card in pl.bank.records(stage=number, live=False):
                counts[card["status"]] = counts.get(card["status"], 0) + 1
            stages.append(
                {"n": number, "name": STAGE_NAMES[number],
                 "open": number in flow.STAGES and flow.stage_open(pl, number),
                 "wired": number in flow.STAGES, "steps": items, "gates": gates, "counts": counts}
            )  # fmt: skip
        nxt = flow.next_step(pl)
        return {
            **base,
            "mode": pl.settings.mode,
            "stages": stages,
            "next": nxt,
            "requests": len(pl.changes.open_requests()),
            "warnings": pl.settings.evaluator_warnings(),
            "waves": waves.info(pl),
            "debt": debt.summary(pl),
            "review": review.view(pl),
            "questions": [_brief(q) for q in self._visible_questions(pl)],
            "queued": max(0, len(stage1.unanswered(pl)) - len(self._visible_questions(pl))),
            "unchosen": [
                {
                    **_brief(r),
                    "variants": [
                        _brief(v) for v in pl.bank.children(r["id"]) if v["kind"] == "variant"
                    ],
                }
                for r in stage2.unchosen(pl)
            ],
        }

    @staticmethod
    def _visible_questions(pl: Pipeline) -> list[dict]:
        """Вопросы «Идеи» человек видит по одному: следующий зависит от ответа на этот."""
        out, shown = [], False
        for q in stage1.unanswered(pl):
            if clarify.human_only(q) and (q.get("attrs") or {}).get("source"):
                if shown:
                    continue
                shown = True
            out.append(q)
        return out

    def overview(self) -> dict:
        """Для обзора «Система»: начат ли конвейер, идея проекта и сколько принятых сценариев."""
        if not self.started():
            return {"started": False, "title": "", "scenarios": 0}
        bank = self.pl().bank
        areas = bank.records("area")
        return {
            "started": True,
            "title": areas[0]["title"] if areas else "",
            "scenarios": len(bank.records("scenario", status="accepted")),
        }

    def job_view(self) -> dict | None:
        job = self.job
        if job is None:
            return None
        return {k: v for k, v in job.items() if k != "thread"}

    def bank(self, query: dict) -> dict:
        pl = self.pl()
        stage = query.get("stage", [""])[0]
        kind = query.get("kind", [""])[0] or None
        status = query.get("status", [""])[0] or None
        cards = pl.bank.records(
            kind, status=status, stage=int(stage) if stage.isdigit() else None, live=False
        )
        out = []
        for card in cards:
            item = _brief(card)
            item["parents"] = [p["id"] for p in pl.bank.parents(card["id"], live=False)]
            out.append(item)
        return {"records": out}

    def record(self, record_id: str) -> dict:
        pl = self.pl()
        card = pl.bank.get(record_id)
        return {
            "record": _brief(card),
            "chain": [_brief(c) for c in pl.bank.chain(record_id)],
            "children": [_brief(c) for c in pl.bank.children(record_id)],
            "history": [
                {"rev": h.get("rev"), "author": h.get("author"), "note": h.get("note")}
                for h in pl.bank.history(record_id)
            ][-20:],
        }

    def gate(self, key: str) -> dict:
        pl = self.pl()
        spec = GATES.get(key)
        if spec is None:
            raise PipelineError("unknown_gate", f"Таких ворот нет: {key!r}")
        last = pl.keeper.last(key)
        adv = advice.for_gate(pl, last)
        return {
            "gate": {"key": key, "title": spec.title, "stage": spec.stage, "final": spec.final},
            "advice": {k: adv[k] for k in ("summary", "lines", "actions", "criteria")},
            "criteria": pl.settings.criteria(key),
            "run": last,
            "human": bool(last and last["state"] != "passed" and pl.keeper.needs_human(last)),
            "findings": [
                {
                    **f,
                    "records": [_brief(pl.bank.get(i)) for i in f.get("ids", []) if _exists(pl, i)],
                    "advice": adv["findings"][n],
                }
                for n, f in enumerate((last or {}).get("code", []))
            ],
        }

    def settings(self) -> dict:
        pl = self.pl(create=True)
        s = pl.settings
        agents = []
        for item in self.agents.describe():
            agents.append({**item, "label": AGENT_LABELS.get(item["id"], item["name"])})
        roles = []
        for key, role in ROLES.items():
            chosen = s.role(key)
            roles.append(
                {"key": key, "title": role.title, "where": role.where,
                 "description": role.description,
                 "tier": role.tier, "tier_name": TIER_NAMES[role.tier], "why": role.why,
                 "agent": chosen["agent"], "model": chosen["model"],
                 "recommended": CLAUDE_BY_TIER.get(role.tier, "")}
            )  # fmt: skip
        return {
            "mode": s.mode,
            "mode_names": MODE_NAMES,
            "wave_threshold": s.wave_threshold,
            "quick_fix_limit": s.quick_fix_limit,
            "stall_rounds": s.stall_rounds,
            "max_rounds": s.max_rounds,
            "gates": [
                {"key": k, "title": g.title, "stage": g.stage, "threshold": s.gate_threshold(k), "criteria": s.criteria(k)}
                for k, g in GATES.items()
            ],
            "roles": roles,
            "agents": agents,
            "warnings": s.evaluator_warnings(),
        }

    def stack_info(self) -> dict:
        """Готов ли проект к бэкенду на стеке (fastbase, core-package, event-infra)."""
        tools = {name: stack._tool(self.root, name) is not None for name, _ in stack.GENERATORS}
        return {
            "line": stack.STACK_LINE,
            "missing": stack.missing_files(self.root),
            "tools": tools,
            "install": stack.INSTALL_HINT,
            "frontend": (self.root / "frontend" / "package.json").is_file(),
        }

    def get(self, path: str, query: dict) -> dict:
        route = path[len(PREFIX) :]
        if route == "/stack":
            return self.stack_info()
        if route == "/reset/plan":
            return reset.plan(self.root, query.get("settings", ["0"])[0] in ("1", "true"))
        if route == "/state":
            return self.state()
        if route == "/settings":
            return self.settings()
        if route == "/bank":
            return self.bank(query)
        if route == "/record":
            return self.record(query.get("id", [""])[0])
        if route == "/gate":
            return self.gate(query.get("key", [""])[0])
        if route == "/map":
            return maps.build(self.pl(), query.get("kind", ["trace"])[0])
        if route == "/er":
            return stage2.er(self.pl())
        if route == "/changes":
            return {"requests": self.pl().changes.items}
        raise PipelineError("not_found", "Нет такого адреса")

    # ------------------------------------------------------------------ работа

    def _start(self, label: str, work) -> dict:
        with self.lock:
            if self.job and self.job.get("running"):
                raise PipelineError("busy", f"Конвейер занят: {self.job['label']}")
            self._stop = False
            job = {
                "label": label,
                "running": True,
                "error": "",
                "result": "",
                "started": time.time(),
            }
            self.job = job

        def run() -> None:
            try:
                job["result"] = _summary(work())
            except (PipelineError, GraphError) as error:
                job["error"] = error.message
            except Exception as error:  # noqa: BLE001 — граница фоновой работы
                job["error"] = f"Внутренняя ошибка: {type(error).__name__}"
            finally:
                job["running"] = False
                job["finished"] = time.time()

        thread = threading.Thread(target=run, daemon=True, name="pipeline-job")
        thread.start()
        return self.job_view()

    @staticmethod
    def _manual_only(pl: Pipeline, what: str) -> None:
        """Ручная работа с записями и принятие ниже порога — только в «Вручную»: в остальных режимах
        согласование делает студия, а человеку она задаёт вопросы."""
        if pl.settings.mode != "manual":
            raise PipelineError(
                "manual_only",
                f"{what} — только в режиме «Вручную». В этом режиме студия сама согласует записи "
                "и задаёт вам вопросы",
            )

    def _reset(self, data: dict) -> dict:
        """Снести проект целиком (с подтверждением именем папки): начать с чистого листа."""
        with self.lock:
            if self.job and self.job.get("running"):
                raise PipelineError("busy", f"Конвейер занят: {self.job['label']}")
            reset.check(self.root, str(data.get("confirm") or ""))
            if self.on_reset:
                self.on_reset()
            done = reset.wipe(self.root, str(data.get("confirm") or ""), bool(data.get("settings")))
            self._pl = None
            self.job = None
        return {"result": done, "state": self.state()}

    def wait(self, timeout: float = 30) -> None:
        """Для тестов: дождаться конца фоновой работы."""
        end = time.time() + timeout
        while self.job and self.job.get("running") and time.time() < end:
            time.sleep(0.02)

    def post(self, path: str, data: dict) -> dict:
        route = path[len(PREFIX) :]
        if route == "/start":
            self.pl(create=True)
            return self.state()
        if route == "/reset":
            return self._reset(data)
        if route == "/stack/init":
            root = self.root
            label = "подготовка проекта на стеке"
            return {"job": self._start(label, lambda: {"ran": stack.init_stack(root)})}
        if route == "/develop/scan":
            pl = self.pl(create=True)
            return {"scanned": entry.scan_into_bank(pl), "state": self.state()}
        pl = self.pl(create=route == "/settings")
        if route == "/area":
            stage1.set_area(pl, str(data.get("text") or ""))
            return self.state()
        if route == "/answer":
            card = stage1.answer_question(
                pl, str(data.get("id") or ""), str(data.get("answer") or "")
            )
            question_id = card["id"]
            if (card.get("attrs") or {}).get("source") == "confirm":
                flow.on_answer(pl, question_id)  # «Нет» убирает найденную запись: без агента
            elif flow.needs_work(pl, card):  # студия сама делает следующий шаг: вопрос, правка, ворота
                with contextlib.suppress(PipelineError):  # занято — применится при следующем ответе
                    self._start("учёт вашего ответа", lambda: flow.on_answer(pl, question_id))
            return self.state()
        if route == "/interview/finish":
            topic = str(data.get("topic") or "")
            if not topic:
                raise PipelineError("nothing_to_do", "Не указана тема")
            return {"job": self._start("продолжение по сказанному", lambda: flow.finish_topic(pl, topic))}
        if route == "/review/say":
            text = str(data.get("text") or "")
            label = "обсуждение уточнения"
            return {"job": self._start(label, lambda: {"discussed": review.say(pl, text)})}
        if route == "/review/apply":
            if not review.view(pl)["ready"]:
                raise PipelineError("not_ready", "План ещё не согласован со студией")
            return {"job": self._start("применение уточнения", lambda: review.apply(pl))}
        if route == "/review/cancel":
            review.cancel(pl)
            return self.state()
        if route == "/answer/auto":
            ids = [str(i) for i in data.get("ids") or [data.get("id")] if i]
            if not ids:
                raise PipelineError("nothing_to_do", "Не выбраны вопросы")
            label = "ответ по правилам студии"
            return {"job": self._start(label, lambda: flow.answer_for_me(pl, ids))}
        if route == "/style":
            stage3.build_style(pl, str(data.get("theme") or "") or None)
            return self.state()
        if route == "/choose":
            stage2.choose_variant(pl, str(data.get("id") or ""))
            return self.state()
        if route == "/run":
            stage, key = int(data.get("stage") or 0), str(data.get("step") or "")
            return {"job": self._start(f"шаг {key}", lambda: flow.run_step(pl, stage, key))}
        if route == "/autopilot":
            return {"job": self._start("автопилот", lambda: flow.autopilot(pl))}
        if route == "/develop/reconstruct":
            return {"job": self._start("восстановление замысла", lambda: entry.reconstruct(pl))}
        if route == "/change/apply":
            request_id = str(data.get("id") or "")
            return {
                "job": self._start(
                    "перепроверка затронутого", lambda: entry.apply_accepted(pl, request_id)
                )
            }
        if route == "/gate/decide":
            if str(data.get("decision") or "") == "accept_below":
                self._manual_only(pl, "Принять ниже порога")
            run = pl.decide_gate(
                str(data.get("run") or ""),
                str(data.get("decision") or ""),
                str(data.get("note") or ""),
            )
            return {"run": run, "state": self.state()}
        if route == "/gate/return":
            run = pl.keeper.run(str(data.get("run") or ""))

            def rework_and_rerun():
                flow.rework(pl, run["gate"], run)
                return pl.run_gate(run["gate"], flow.gate_facts(pl, run["gate"]))

            return {"job": self._start("доработка и повторные ворота", rework_and_rerun)}
        if route == "/waves/enable":
            waves.enable(pl)
            return self.state()
        if route == "/waves/disable":
            waves.disable(pl)
            return self.state()
        if route == "/debt/reopen":
            if not debt.next_gate(pl):
                raise PipelineError(
                    "no_debt", "Записей ниже порога нет или раньше ещё идёт работа над воротами"
                )

            def reopen_and_rate():
                back = debt.reopen(pl)
                run = pl.run_gate(back["gate"], flow.gate_facts(pl, back["gate"]))
                return {**run, "debt": back}

            return {"job": self._start("возврат записей ниже порога на улучшение", reopen_and_rate)}
        if route == "/gate/fix":
            run_id, action = str(data.get("run") or ""), str(data.get("action") or "")
            gate_key = pl.keeper.run(run_id)["gate"]
            if action == "integrate":
                label = "внедрение висящего и повторные ворота"

                def integrate_and_rerun():
                    done = integrate.run(pl)
                    run = pl.run_gate(gate_key, flow.gate_facts(pl, gate_key))
                    return {**run, "integrated": done}

                return {"result": None, "job": self._start(label, integrate_and_rerun)}
            if action == "reject_all":
                self._manual_only(pl, "Отклонение записей руками")
            result = advice.fix(pl, run_id, action) if action != "rerate" else None
            facts = flow.gate_facts(pl, gate_key)
            label = "повторные ворота"
            job = self._start(label, lambda: pl.run_gate(gate_key, facts))
            return {"result": result, "job": job}
        if route == "/settings":
            return self._settings_post(pl, data)
        if route == "/criterion":
            return self._criterion_post(pl, data)
        if route == "/role":
            pl.settings.set_role(
                str(data.get("role") or ""),
                str(data.get("agent") or ""),
                str(data.get("model") or ""),
            )
            return self.settings()
        if route == "/record/edit":
            self._manual_only(pl, "Правка записи руками")
            return self._edit(pl, data)
        if route == "/record/reject":
            self._manual_only(pl, "Отклонение записи руками")
            pl.bank.reject(str(data.get("id") or ""), "human", str(data.get("note") or ""))
            return self.state()
        if route == "/record/link":
            self._manual_only(pl, "Привязка записей руками")
            pl.bank.link(str(data.get("child") or ""), str(data.get("parent") or ""), "human")
            return self.state()
        if route == "/change":
            return self._change(pl, data)
        if route == "/change/decide":
            keep = data.get("keep")
            done = pl.changes.decide(
                str(data.get("id") or ""), str(data.get("decision") or ""), "human",
                str(data.get("note") or ""), keep if isinstance(keep, list) else None,
            )  # fmt: skip
            return {"request": done, "state": self.state()}
        raise PipelineError("not_found", "Нет такого адреса")

    # ------------------------------------------------------------------ части POST

    def _settings_post(self, pl: Pipeline, data: dict) -> dict:
        if "mode" in data:
            pl.settings.set_mode(str(data["mode"]))
        for key in ("wave_threshold", "quick_fix_limit"):
            if key in data:
                pl.settings.set_number(key, data[key])
        return self.settings()

    def _criterion_post(self, pl: Pipeline, data: dict) -> dict:
        gate, action = str(data.get("gate") or ""), str(data.get("action") or "set")
        if action == "threshold":
            pl.settings.set_gate_threshold(gate, data.get("threshold"))
        elif action == "add":
            pl.settings.add_criterion(
                gate,
                str(data.get("title") or ""),
                str(data.get("hint") or ""),
                data.get("threshold"),
            )
        elif action == "remove":
            pl.settings.remove_criterion(gate, str(data.get("key") or ""))
        elif action == "reset":
            pl.settings.reset_gate(gate)
        else:
            pl.settings.set_criterion(
                gate, str(data.get("key") or ""),
                enabled=data.get("enabled"), threshold=data.get("threshold"),
                title=data.get("title"), hint=data.get("hint"),
            )  # fmt: skip
        return self.settings()

    def _edit(self, pl: Pipeline, data: dict) -> dict:
        record_id = str(data.get("id") or "")
        card = pl.bank.get(record_id)
        if card["status"] == "accepted":
            raise PipelineError("accepted", "Принятое меняется запросом на изменение, а не правкой")
        pl.bank.revise(
            record_id, "human", title=str(data.get("title") or "") or None,
            body=data.get("body"),
        )  # fmt: skip
        return self.record(record_id)

    def _change(self, pl: Pipeline, data: dict) -> dict:
        patches = data.get("patches")
        new = data.get("new")
        request = entry.submit(
            pl,
            str(data.get("text") or ""),
            patches=patches if isinstance(patches, dict) else None,
            new=new if isinstance(new, dict) else None,
        )
        return {"request": request, "state": self.state()}


def _exists(pl: Pipeline, record_id: str) -> bool:
    try:
        pl.bank.get(record_id)
    except PipelineError:
        return False
    return True


__all__ = ["PREFIX", "PipelineService"]
