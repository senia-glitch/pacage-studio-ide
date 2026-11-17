"""Проект: граф и банк знаний, действия человека, работа агентов.

Цепочка линейна (см. ``flow.py``). Человек принимает, отклоняет, удаляет и пишет своё; агенты только предлагают:
три исследователя ищут и анализируют конкурентов в сети, помощник предлагает роли, сценарии, проблемы,
варианты автоматизации и строит диаграммы. В спорной ситуации помощник задаёт вопрос с вариантами.
Одновременно идёт одна работа.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from contextlib import contextmanager, suppress
from pathlib import Path

from studio.pipeline.graph import ACCEPTED, HUMAN, PROPOSED, GraphError, Store, normalize_url

from . import check, diagrams, export, flow, rules, tools
from .agents import TurnRequest, TurnResult
from .errors import EngineError

AGENT = "agent"
RESEARCHERS = 3
SCENARIO_RULE = (
    "Принимай ключевые сценарии, которые стоит автоматизировать и которые в границах системы."
)


def _clip(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


class Run:
    """Одна работа агентов: что делается, на каком этапе, что спросили."""

    def __init__(self, kind: str, title: str):
        self.kind, self.title = kind, title
        self.auto = kind in (
            "autopilot",
            "export",
            "export_auto",
        )  # вопросы помощника отключены: решает автопилот или идёт проверка перед выгрузкой
        self.improve = (
            False  # автопилот: улучшать уже сделанное, а не только доводить до согласования
        )
        self.each = False  # автопилот «по сценариям»: сценарии по одному, каждый до «решения», следующий учитывает прежние
        self.status = "running"  # running | asking | done | error | stopped
        self.error = ""
        self.summary = ""
        self.question: dict | None = None
        self.stop = False
        self.procs: set = set()
        self.answer: dict | None = None
        self.answered = threading.Event()
        self.done = self.total = 0
        self.notes: list[str] = []  # что не вышло или пропущено: показываем человеку

    def public(self) -> dict:
        return {
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "error": self.error,
            "summary": self.summary,
            "question": self.question,
            "done": self.done,
            "total": self.total,
            "notes": self.notes[-6:],
        }


class Project:
    def __init__(self, folder, settings, adapters):
        self.folder = Path(folder)
        self.store = Store.open(self.folder)
        self.dir = self.store.dir
        self.settings, self.adapters = settings, adapters
        self.bank = self.store.bank(flow.BANK)
        self.lock = threading.RLock()
        self._local = threading.local()  # замороженный снимок графа: свой у каждого потока
        self._log_lock = threading.Lock()
        self.run: Run | None = None
        self.tokens: dict[str, dict] = {}
        self.version = 0
        self.base_url = ""
        self.extra_env: dict[str, str] = {}  # добавка к окружению хода (токен моста в студии)
        self._flow = self._load_flow()
        self._migrate_requirements()

    # ------------------------------------------------------------------ состояние

    def _load_flow(self) -> dict:
        try:
            data = json.loads((self.dir / "flow.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        rejected = data.get("rejected") if isinstance(data, dict) else {}
        return {
            "rejected": {
                str(k): [str(x) for x in v]
                for k, v in (rejected or {}).items()
                if isinstance(v, list)
            },
            "roles_confirmed": bool(data.get("roles_confirmed"))
            if isinstance(data, dict)
            else False,
        }

    @property
    def roles_confirmed(self) -> bool:
        """Список ролей подтверждён человеком (или автопилотом по итогам своего шага «роли»): агенты его не меняют."""
        return bool(self._flow.get("roles_confirmed"))

    def _set_confirmed(self, value: bool) -> None:
        if self.roles_confirmed != value:
            self._flow["roles_confirmed"] = value
            self._save_flow()
            self._touch()

    def _roles_locked(self) -> bool:
        return self.roles_confirmed and bool(self._components())

    def _save_flow(self) -> None:
        (self.dir / "flow.json").write_text(
            json.dumps(self._flow, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def _touch(self) -> None:
        self.version += 1

    def _snap(self) -> dict:
        frozen = getattr(self._local, "snap", None)
        return frozen if frozen is not None else self.store.snapshot()

    @contextmanager
    def frozen(self):
        """Один снимок графа на всё чтение внутри блока (только чтение; в других потоках граф виден как обычно)."""
        if getattr(self._local, "snap", None) is not None:
            yield
            return
        self._local.snap = self.store.snapshot()
        try:
            yield
        finally:
            self._local.snap = None

    def warnings(self) -> list[str]:
        """Что в открытом проекте может помешать работе (только предупреждения, ничего не запрещается)."""
        out: list[str] = []
        with self.frozen():
            roles = self._accepted_all(flow.ROLE)
            scenarios = self._accepted_all(flow.SCENARIO)
            untyped = [r["title"] for r in roles if not (r.get("attrs") or {}).get("type")]
            if untyped:
                out.append(
                    "Роли без типа считаются людьми: "
                    + ", ".join(untyped[:5])
                    + ("…" if len(untyped) > 5 else "")
                    + ". Исправьте тип кнопкой «Изменить» на шаге «Роли»"
                )
            if scenarios and not self._components():
                out.append(
                    "Нет компонентов системы (Сайт, Админка…): диаграммы и проверка перед выгрузкой не заработают. "
                    "Примите хотя бы один компонент на шаге «Роли»"
                )
            legacy = [c for c in self.store.snapshot()["cards"] if c["kind"] == flow.LEGACY_ASIS]
            if legacy:
                out.append(
                    f"В проекте остались диаграммы «как есть» прежней цепочки ({len(legacy)}): "
                    "новая цепочка строится от проблем к требованиям и решениям и их не использует"
                )
            stale = {i["scenario"] for i in check.issues(self) if i["code"] == "diagram"}
            if stale:
                out.append(
                    f"Диаграммы не соответствуют графу в сценариях: {len(stale)}. Их перестроит проверка перед выгрузкой"
                )
        return out

    def _migrate_requirements(self) -> None:
        """Проекты прежней цепочки: варианты решения висели на проблемах. Теперь они вырастают из требований.

        Для такой проблемы создаётся требование «Решить проблему: …» (принято, если принята проблема), и варианты
        переезжают к нему: ничего не теряется, выбранные решения остаются выбранными.
        """
        snap = self.store.snapshot()
        cards = {c["id"]: c for c in snap["cards"]}
        by_problem: dict[str, list[dict]] = {}
        for edge in snap["edges"]:
            if edge["kind"] != "детализирует":
                continue
            child, parent = cards.get(edge["from"]), cards.get(edge["to"])
            if child and parent and child["kind"] == flow.VARIANT and parent["kind"] == flow.PROBLEM:
                by_problem.setdefault(parent["id"], []).append(edge)
        ops: list[dict] = []
        for number, (problem_id, edges) in enumerate(by_problem.items(), 1):
            problem = cards[problem_id]
            ref = f"m{number}"
            ops.append(
                {
                    "op": "add_card",
                    "ref": ref,
                    "kind": flow.REQUIREMENT,
                    "title": _clip(f"Решить проблему: {problem['title']}", 200),
                    "body": _clip(problem.get("body"), 250),
                    "status": problem["status"],
                    "parent": problem_id,
                }
            )
            ops += [{"op": "update_edge", "id": e["id"], "to": f"@{ref}"} for e in edges]
        if ops:
            self.store.apply(ops, "package:migration", note="Требования для прежних проблем с решениями")

    def state(self) -> dict:
        return {
            "title": self.store.title,
            "revision": self.store.revision,
            "version": self.version,
            "roles_confirmed": self.roles_confirmed,
            "run": self.run.public() if self.run else None,
        }

    def graph(self) -> dict:
        return {**self.store.snapshot(), "layout": self.store.layout()}

    def rejected(self, kind: str) -> list[str]:
        return list(self._flow["rejected"].get(kind, []))

    def _reject(self, kind: str, key: str) -> None:
        items = self._flow["rejected"].setdefault(kind, [])
        if key and key not in items:
            items.append(key)
            self._save_flow()

    # ------------------------------------------------------------------ чтение графа и банка

    def cards(self, kind: str, parent: str | None = None) -> list[dict]:
        snap = self._snap()
        under = None
        if parent:
            under = {
                e["from"]
                for e in snap["edges"]
                if e["to"] == parent and e["kind"] == "детализирует"
            }
        found = [
            c for c in snap["cards"] if c["kind"] == kind and (under is None or c["id"] in under)
        ]
        return sorted(found, key=lambda c: int(c["id"][1:]))

    def sphere(self) -> dict | None:
        found = self.cards(flow.SPHERE)
        return found[0] if found else None

    def need_sphere(self) -> dict:
        sphere = self.sphere()
        if sphere is None:
            raise EngineError("no_sphere", "Сначала напишите сферу бизнеса")
        return sphere

    def card(self, card_id: str) -> dict:
        try:
            return self.store.card(card_id)
        except GraphError as error:
            raise EngineError("unknown_card", error.message) from None

    def roles_of(self, scenario_id: str) -> list[dict]:
        snap = self._snap()
        cards = {c["id"]: c for c in snap["cards"]}
        return [
            cards[e["to"]]
            for e in snap["edges"]
            if e["from"] == scenario_id and e["kind"] == flow.PARTICIPATES and e["to"] in cards
        ]

    def scenario_info(self, card: dict) -> dict:
        usecase = (card.get("attrs") or {}).get("usecase") or {}
        return {
            "id": card["id"],
            "title": card["title"],
            "body": card.get("body", ""),
            "roles": [r["title"] for r in self.roles_of(card["id"])],
            "cases": [c.get("name", "") for c in usecase.get("cases", [])],
        }

    def records(self, type_: str | None = None) -> list[dict]:
        return self.bank.list(type_)

    def accepted_competitors(self) -> list[dict]:
        return [
            r for r in self.records("competitor") if (r["data"] or {}).get("state") == "accepted"
        ]

    def facts_of(self, competitor_key: str) -> list[dict]:
        return [
            r for r in self.records("fact") if (r["data"] or {}).get("competitor") == competitor_key
        ]

    def built(self, skip: str = "") -> list[str]:
        """Что уже построено по другим сценариям: требования и выбранные решения; новое согласуется с этим."""
        out = []
        for scenario in self._accepted_all(flow.SCENARIO):
            problems = self._accepted(flow.PROBLEM, scenario["id"])
            if scenario["id"] == skip or not problems:
                continue
            chosen = {s["requirement"]: s["title"] for s in self._solved(scenario["id"])}
            requirements = [r for p in problems for r in self._accepted(flow.REQUIREMENT, p["id"])]
            items = (
                [f"{r['title']} → {chosen.get(r['title'], 'решения нет')}" for r in requirements]
                if requirements
                else [p["title"] for p in problems]
            )
            out.append(f"«{scenario['title']}»: " + "; ".join(items))
        return out[:12]

    def knowledge(self, skip: str = "") -> str:
        requirements = [r["data"].get("text", "") for r in self.records("requirement")]
        names = {r["key"]: r["data"].get("name", r["key"]) for r in self.accepted_competitors()}
        facts = [
            f"{r['data'].get('kind', 'факт')}: {r['data'].get('text', '')}"
            + (
                f" ({names[r['data']['competitor']]})"
                if r["data"].get("competitor") in names
                else ""
            )
            for r in self.records("fact")
            if (r["data"] or {}).get("competitor") in names
            or not (r["data"] or {}).get("competitor")
        ]
        sphere = self.need_sphere()
        comps = [
            f"{d.get('name')} ({d.get('url')})"
            for d in (r["data"] for r in self.accepted_competitors())
        ]
        bounds = [t[len(flow.BOUNDARY) :] for t in requirements if t.startswith(flow.BOUNDARY)]
        wishes = [t for t in requirements if not t.startswith(flow.BOUNDARY)]
        return flow.context(sphere["title"], wishes, comps, facts[:80], bounds, self.built(skip))

    # ------------------------------------------------------------------ действия человека

    def act(self, action: str, d: dict) -> dict:
        handler = getattr(self, "do_" + action, None)
        if not callable(handler) or action.startswith("_"):
            raise EngineError("unknown_action", "Нет такого действия")
        if (
            self.run
            and self.run.status in ("running", "asking")
            and action not in ("bank_add", "bank_remove", "bank_update")
        ):
            raise EngineError(
                "busy", "Сейчас работают агенты: дождитесь конца или остановите работу"
            )
        out = handler(d) or {}
        self._touch()
        return out

    def _human(self, ops: list[dict], note: str = "") -> dict:
        try:
            return self.store.apply(ops, HUMAN, note=note)
        except GraphError as error:
            raise EngineError(error.code, error.message) from None

    def do_set_sphere(self, d: dict) -> dict:
        title = _clip(d.get("title"), 200)
        if not title:
            raise EngineError("empty", "Напишите сферу бизнеса")
        sphere = self.sphere()
        if sphere:
            self._human([{"op": "update_card", "id": sphere["id"], "title": title}])
        else:
            self._human([{"op": "add_card", "kind": flow.SPHERE, "title": title}])
        return {}

    def do_accept(self, d: dict) -> dict:
        card = self.card(str(d.get("id") or ""))
        self._human([{"op": "update_card", "id": card["id"], "status": ACCEPTED}])
        return {}

    def _subtree(self, card_id: str) -> list[str]:
        snap = self.store.snapshot()
        out, todo = [], [card_id]
        while todo:
            current = todo.pop()
            out.append(current)
            todo += [
                e["from"]
                for e in snap["edges"]
                if e["to"] == current and e["kind"] == "детализирует"
            ]
        return out[::-1]

    def do_remove(self, d: dict) -> dict:
        """Отклонить или удалить карточку вместе с тем, что под ней; название запоминается, чтобы агент не предлагал снова."""
        card = self.card(str(d.get("id") or ""))
        if card["kind"] == flow.SPHERE:
            raise EngineError("forbidden", "Сферу удалить нельзя: её можно переписать")
        if card["kind"] not in (flow.LEGACY_ASIS, flow.SOLUTION):
            self._reject(card["kind"], card["title"].lower())
        self._human(
            [{"op": "delete_card", "id": i} for i in self._subtree(card["id"])],
            f"Удалено: {card['title']}",
        )
        if card["kind"] == flow.ROLE:
            self._forget_actor(card["title"])
        return {}

    def _forget_actor(self, name: str) -> None:
        ops = []
        for scenario in self.cards(flow.SCENARIO):
            usecase = (scenario.get("attrs") or {}).get("usecase")
            if not usecase:
                continue
            cases = []
            for case in usecase.get("cases", []):
                actors = [a for a in case.get("actors", []) if a.lower() != name.lower()]
                if actors:
                    cases.append({**case, "actors": actors})
            if cases != usecase.get("cases"):
                ops.append(
                    {
                        "op": "update_card",
                        "id": scenario["id"],
                        "attrs": {"usecase": {**usecase, "cases": cases}},
                    }
                )
        if ops:
            self._human(ops)

    def _add_unique(self, kind: str, title: str, body: str, parent: str, **extra) -> str:
        title = _clip(title, 200)
        if not title:
            raise EngineError("empty", "Название не может быть пустым")
        if any(
            c["title"].lower() == title.lower()
            for c in self.cards(kind, parent if kind != flow.ROLE else None)
        ):
            raise EngineError("duplicate", f"«{title}» уже есть")
        ops = [
            {
                "op": "add_card",
                "kind": kind,
                "title": title,
                "body": str(body or "").strip(),
                "parent": parent,
                **extra,
            }
        ]
        return self._human(ops)["ids"][0]

    def _role_names(self) -> list[str]:
        """Единое пространство имён участников диаграмм: принятые роли и компоненты."""
        return [c["title"] for c in self._accepted_all(flow.ROLE)]

    @staticmethod
    def _role_type(card: dict) -> str:
        return (card.get("attrs") or {}).get(
            "type"
        ) or flow.HUMAN_TYPE  # у старых ролей типа нет: считаются людьми

    def _components(self) -> list[dict]:
        return [
            c for c in self._accepted_all(flow.ROLE) if self._role_type(c) == flow.COMPONENT_TYPE
        ]

    def _need_components(self) -> None:
        if not self._components():
            raise EngineError(
                "no_components",
                "Не определены компоненты системы (Сайт, Админка…): без них нельзя строить диаграммы. "
                "На шаге «Роли» нажмите «Предложить роли» и примите хотя бы один компонент",
            )

    def _names_groups(self) -> list[str]:
        groups = []
        for label, kind in (
            ("люди", flow.HUMAN_TYPE),
            ("внешние системы", flow.EXTERNAL_TYPE),
            ("компоненты системы", flow.COMPONENT_TYPE),
        ):
            titles = [
                c["title"] for c in self._accepted_all(flow.ROLE) if self._role_type(c) == kind
            ]
            if titles:
                groups.append(f"{label}: {', '.join(titles)}")
        return groups

    def do_add_role(self, d: dict) -> dict:
        kind = str(d.get("type") or flow.HUMAN_TYPE)
        if kind not in flow.ROLE_TYPES:
            raise EngineError("bad_type", "Тип роли: " + ", ".join(flow.ROLE_TYPES))
        return {
            "id": self._add_unique(
                flow.ROLE,
                d.get("title"),
                d.get("body"),
                self.need_sphere()["id"],
                attrs={"type": kind},
            )
        }

    def _role_used(self, role: dict) -> bool:
        """Роль или компонент уже стоит в сценарии, в диаграмме вариантов использования или в диаграмме последовательности."""
        name = role["title"].lower()
        snap = self._snap()
        if any(e["to"] == role["id"] and e["kind"] == flow.PARTICIPATES for e in snap["edges"]):
            return True
        for card in snap["cards"]:
            attrs = card.get("attrs") or {}
            for case in (attrs.get("usecase") or {}).get("cases", []):
                if any(str(a).lower() == name for a in case.get("actors", [])):
                    return True
            diagram = attrs.get("diagram") or {}
            if any(str(p).lower() == name for p in diagram.get("participants", [])):
                return True
            if any(
                name in (str(s.get("from", "")).lower(), str(s.get("to", "")).lower())
                for s in diagram.get("steps", [])
            ):
                return True
        return False

    def do_edit_role(self, d: dict) -> dict:
        """Ручная правка роли или компонента: название, описание, тип. Имя и «компонент / не компонент» нельзя менять там, где роль
        уже стоит в сценариях и диаграммах: иначе они сломаются."""
        role = self.card(str(d.get("id") or ""))
        if role["kind"] != flow.ROLE:
            raise EngineError("bad_kind", "Правится только роль или компонент")
        used = self._role_used(role)
        op: dict = {"op": "update_card", "id": role["id"]}
        if "title" in d:
            title = _clip(d.get("title"), 200)
            if not title:
                raise EngineError("empty", "Название не может быть пустым")
            if title != role["title"]:
                if used:
                    raise EngineError(
                        "role_used",
                        f"«{role['title']}» уже есть в сценариях и диаграммах: переименование их сломает. "
                        "Измените описание или тип, либо добавьте новую роль и удалите эту",
                    )
                if any(
                    c["id"] != role["id"] and c["title"].lower() == title.lower()
                    for c in self.cards(flow.ROLE)
                ):
                    raise EngineError("duplicate", f"«{title}» уже есть")
                op["title"] = title
        if "body" in d:
            body = str(d.get("body") or "").strip()[:4000]
            if body != (role.get("body") or ""):
                op["body"] = body
        if "type" in d:
            kind = str(d.get("type") or "").strip().lower()
            if kind not in flow.ROLE_TYPES:
                raise EngineError("bad_type", "Тип роли: " + ", ".join(flow.ROLE_TYPES))
            old = self._role_type(role)
            if kind != old:
                if used and (kind == flow.COMPONENT_TYPE) != (old == flow.COMPONENT_TYPE):
                    raise EngineError(
                        "role_used",
                        f"«{role['title']}» уже стоит в сценариях и диаграммах: менять «компонент» на роль "
                        "(и наоборот) нельзя. Между «человек» и «внешняя система» менять можно",
                    )
                op["attrs"] = {"type": kind}
        if len(op) > 2:
            self._human([op], f"Правка роли: {role['title']}")
        return {}

    def do_confirm_roles(self, d: dict) -> dict:
        """Подтвердить список ролей: агенты его больше не меняют, пока вы сами не попросите предложить ещё
        или не запустите «Улучшить уже есть»."""
        pending = [r["title"] for r in self.cards(flow.ROLE) if r["status"] == PROPOSED]
        if pending:
            raise EngineError(
                "pending_roles",
                f"Есть роли, которые ждут решения ({len(pending)}): "
                + ", ".join(pending[:5])
                + ". Примите или отклоните их",
            )
        self._need_components()
        self._set_confirmed(True)
        return {}

    def _sync_scenario_roles(self, scenario_id: str, diagram: dict, author: str) -> None:
        """Люди и внешние системы из диаграммы сценария становятся его ролями: «Роли» сценария совпадают с тем, кто действует."""
        have = {r["id"] for r in self.roles_of(scenario_id)}
        names = {str(p).lower() for p in diagram.get("participants", [])}
        names |= {
            str(s.get(k, "")).lower() for s in diagram.get("steps", []) for k in ("from", "to")
        }
        ops = [
            {"op": "add_edge", "from": scenario_id, "to": r["id"], "kind": flow.PARTICIPATES}
            for r in self.cards(flow.ROLE)
            if r["title"].lower() in names
            and r["status"] == ACCEPTED
            and self._role_type(r) != flow.COMPONENT_TYPE
            and r["id"] not in have
        ]
        if ops:
            self.store.apply(ops, author, note="Роли сценария по диаграмме")

    def do_add_scenario(self, d: dict) -> dict:
        """Свой сценарий: название и роли; диаграмма вариантов использования строится из них."""
        sphere = self.need_sphere()
        names = [_clip(n, 60) for n in (d.get("roles") or [])]
        roles = {
            r["title"].lower(): r
            for r in self.cards(flow.ROLE)
            if self._role_type(r) != flow.COMPONENT_TYPE
        }  # компоненты не актёры
        found = [roles[n.lower()] for n in names if n.lower() in roles]
        title = _clip(d.get("title"), 200)
        usecase = {
            "system": title,
            "cases": [{"name": title, "actors": [r["title"] for r in found]}] if found else [],
        }
        card_id = self._add_unique(
            flow.SCENARIO,
            title,
            d.get("body"),
            sphere["id"],
            attrs={"usecase": usecase},
            links=[{"to": r["id"], "kind": flow.PARTICIPATES} for r in found],
        )
        return {"id": card_id}

    def do_add_problem(self, d: dict) -> dict:
        scenario = self.card(str(d.get("scenario") or ""))
        return {
            "id": self._add_unique(
                flow.PROBLEM,
                d.get("title"),
                d.get("body"),
                scenario["id"],
                attrs={"importance": _clip(d.get("importance"), 20)},
            )
        }

    def do_add_requirement(self, d: dict) -> dict:
        """Своё требование к системе: оно вырастает из проблемы сценария."""
        problem = self.card(str(d.get("problem") or ""))
        if problem["kind"] != flow.PROBLEM:
            raise EngineError("bad_kind", "Требование вырастает из проблемы")
        return {
            "id": self._add_unique(flow.REQUIREMENT, d.get("title"), d.get("body"), problem["id"])
        }

    def do_add_variant(self, d: dict) -> dict:
        requirement = self.card(str(d.get("requirement") or ""))
        if requirement["kind"] != flow.REQUIREMENT:
            raise EngineError("bad_kind", "Вариант решения вырастает из требования")
        return {
            "id": self._add_unique(flow.VARIANT, d.get("title"), d.get("body"), requirement["id"])
        }

    def do_choose_variant(self, d: dict) -> dict:
        """Выбрать один вариант решения требования: он принят, остальные варианты этого требования убираются."""
        variant = self.card(str(d.get("id") or ""))
        snap = self.store.snapshot()
        parent = next(
            (
                e["to"]
                for e in snap["edges"]
                if e["from"] == variant["id"] and e["kind"] == "детализирует"
            ),
            "",
        )
        others = [c["id"] for c in self.cards(flow.VARIANT, parent) if c["id"] != variant["id"]]
        self._human(
            [{"op": "update_card", "id": variant["id"], "status": ACCEPTED}]
            + [{"op": "delete_card", "id": i} for i in others]
        )
        return {}

    def do_save_steps(self, d: dict) -> dict:
        """Шаги диаграммы последовательности, написанные человеком («1. Кто → Кому: что»): строим по ним диаграмму."""
        scenario = self.card(str(d.get("scenario") or ""))
        kind = str(d.get("kind") or "")
        if kind != flow.SOLUTION:
            raise EngineError("bad_kind", "Диаграмма бывает только «решение»")
        diagram = diagrams.parse_text(str(d.get("text") or ""))
        if not diagram["steps"]:
            raise EngineError(
                "no_steps", "Не вижу шагов. Формат строки: «Кто → Кому: что происходит»"
            )
        existing = self.cards(kind, scenario["id"])
        if existing:
            self._human(
                [{"op": "update_card", "id": existing[0]["id"], "attrs": {"diagram": diagram}}]
            )
        else:
            prefix = "Решение"
            self._human(
                [
                    {
                        "op": "add_card",
                        "kind": kind,
                        "title": f"{prefix}: {scenario['title']}"[:200],
                        "parent": scenario["id"],
                        "attrs": {"diagram": diagram},
                    }
                ]
            )
        self._sync_scenario_roles(scenario["id"], diagram, HUMAN)
        return {}

    # ---- банк знаний

    def do_bank_add(self, d: dict) -> dict:
        kind = str(d.get("type") or "")
        text = _clip(d.get("text"), 600)
        if kind == "competitor":
            return self.add_competitor(_clip(d.get("name") or text, 120), _clip(d.get("url"), 300))
        if kind not in ("fact", "requirement") or not text:
            raise EngineError("invalid", "Введите текст факта или требования")
        data = (
            {"text": text}
            if kind == "requirement"
            else {"kind": _clip(d.get("kind"), 20) or "факт", "text": text, "competitor": ""}
        )
        self.bank.put(kind, data, HUMAN, key=f"{kind}|{text.lower()}")
        return {}

    def add_competitor(self, name: str, url: str) -> dict:
        key = normalize_url(url)
        if not name or not key:
            raise EngineError("invalid", "Нужны название и сайт конкурента")
        self.bank.put(
            "competitor",
            {"name": name, "url": url, "why": "", "state": "accepted"},
            HUMAN,
            sources=[url],
            key=key,
        )
        return {}

    def do_bank_update(self, d: dict) -> dict:
        record = self.bank.get(str(d.get("id") or ""))
        data = dict(record["data"])
        for field in ("text", "name", "url", "why", "kind", "state"):
            if field in d:
                data[field] = _clip(d[field], 600)
        self.bank.update(record["id"], HUMAN, data=data)
        return {}

    def do_export_md(self, d: dict) -> dict:
        """Выгрузить документацию проекта в .md в папке проекта (файл перезаписывается)."""
        self.need_sphere()
        path = self.folder / export.FILE_NAME
        path.write_text(export.build(self), encoding="utf-8")
        return {"path": str(path)}

    def do_bank_remove(self, d: dict) -> dict:
        """Удалить запись; у конкурента вместе с ним уходят его факты, а сайт больше не предлагается."""
        record = self.bank.get(str(d.get("id") or ""))
        if record["type"] == "competitor":
            for fact in self.facts_of(record["key"]):
                self.bank.remove(fact["id"])
            self._reject("competitor", record["key"])
        self.bank.remove(record["id"])
        return {}

    # ------------------------------------------------------------------ запуск агентов

    def start(
        self, kind: str, ids: list[str] | None = None, redo: bool = False, each: bool = False
    ) -> dict:
        with self.lock:  # проверка «не занято» и запуск — одна неделимая операция
            if self.run and self.run.status in ("running", "asking"):
                raise EngineError("busy", "Уже идёт работа агентов")
            plan = self._plan(kind, [str(i) for i in (ids or [])], redo)
            if kind == "roles":
                self._set_confirmed(
                    False
                )  # вы сами просите предложить ещё: список снова открыт, потом подтвердите
            run = Run(kind, plan["title"])
            run.total = plan.get("total", 1)
            run.improve = kind == "autopilot" and redo
            run.each = kind == "autopilot" and each
            self.run = run
            self._touch()
            threading.Thread(target=self._work, args=(run, plan), daemon=True).start()
        return {}

    def _plan(self, kind: str, ids: list[str], redo: bool) -> dict:
        sphere = self.need_sphere()
        if kind in ("export", "export_auto"):
            return {
                "title": "Проверка графа перед выгрузкой…"
                if kind == "export"
                else "Автоматическая проверка и правка графа…",
                "kind": kind,
            }
        if kind == "autopilot":
            return {"title": "Автопилот: уточняет границы системы…", "kind": kind}
        if kind == "competitors":
            return {"title": "Исследователи ищут конкурентов…", "kind": kind}
        if kind == "analysis":
            targets = (
                self.accepted_competitors()
                if redo
                else [
                    r for r in self.accepted_competitors() if not (r["data"] or {}).get("analyzed")
                ]
            )
            if not targets:
                raise EngineError(
                    "nothing", "Нет принятых конкурентов, которых нужно проанализировать"
                )
            return {
                "title": "Исследователи анализируют сайты…",
                "kind": kind,
                "targets": targets,
                "redo": redo,
            }
        if kind in ("roles", "scenarios"):
            if kind == "scenarios" and not self.cards(flow.ROLE):
                raise EngineError("no_roles", "Сначала нужны роли")
            return {
                "title": "Помощник предлагает " + ("роли…" if kind == "roles" else "сценарии…"),
                "kind": kind,
                "sphere": sphere,
            }
        if kind == "requirements":
            # ids — проблемы (одна или несколько); без ids — все принятые проблемы принятых сценариев
            if ids:
                problems = [self.card(i) for i in ids]
                problems = [
                    p for p in problems if p["kind"] == flow.PROBLEM and p["status"] == ACCEPTED
                ]
            else:
                problems = [
                    p
                    for s in self.cards(flow.SCENARIO)
                    if s["status"] == ACCEPTED
                    for p in self._accepted(flow.PROBLEM, s["id"])
                ]
            if not problems:
                raise EngineError("no_problems", "Сначала примите проблемы сценария")
            return {
                "title": "Помощник формулирует требования к системе…",
                "kind": kind,
                "problems": problems,
                "total": len(problems),
            }
        if kind == "variants":
            # ids — требования (одно или несколько); без ids — все принятые требования
            if ids:
                requirements = [self.card(i) for i in ids]
                requirements = [
                    r
                    for r in requirements
                    if r["kind"] == flow.REQUIREMENT and r["status"] == ACCEPTED
                ]
            else:
                requirements = [
                    r
                    for s in self.cards(flow.SCENARIO)
                    if s["status"] == ACCEPTED
                    for r in self._requirements_of(s["id"])
                ]
            if not requirements:
                raise EngineError("no_requirements", "Сначала примите требования к системе")
            return {
                "title": "Помощник предлагает варианты автоматизации…",
                "kind": kind,
                "requirements": requirements,
                "total": len(requirements),
            }
        if kind in ("problems", "solution"):
            if kind != "problems":
                self._need_components()  # диаграмма строится из единого списка имён: нужны компоненты
            scenarios = (
                [self.card(i) for i in ids]
                if ids
                else [c for c in self.cards(flow.SCENARIO) if c["status"] == ACCEPTED]
            )
            scenarios = [s for s in scenarios if s["kind"] == flow.SCENARIO]
            if not scenarios:
                raise EngineError("no_scenarios", "Нет принятых сценариев")
            if kind == "solution":
                scenarios = [s for s in scenarios if self._solved(s["id"])]
                if not scenarios:
                    raise EngineError(
                        "no_variant",
                        "Сначала выберите вариант решения хотя бы для одного требования",
                    )
            names = {"problems": "проблемы", "solution": "диаграмму «решение»"}
            return {
                "title": f"Помощник предлагает {names[kind]}…",
                "kind": kind,
                "scenarios": scenarios,
                "total": len(scenarios),
            }
        raise EngineError("unknown_job", "Нет такой работы")

    def _accepted(self, kind: str, parent_id: str) -> list[dict]:
        return [c for c in self.cards(kind, parent_id) if c["status"] == ACCEPTED]

    def _requirements_of(self, scenario_id: str) -> list[dict]:
        """Принятые требования сценария: из принятых проблем."""
        return [
            r
            for p in self._accepted(flow.PROBLEM, scenario_id)
            for r in self._accepted(flow.REQUIREMENT, p["id"])
        ]

    def _unsolved_requirements(self, scenario_id: str) -> list[dict]:
        """Принятые требования сценария, у которых ещё нет выбранного варианта решения."""
        return [
            r for r in self._requirements_of(scenario_id) if not self._accepted(flow.VARIANT, r["id"])
        ]

    def _solved(self, scenario_id: str) -> list[dict]:
        """Принятые требования сценария с выбранным вариантом решения: [{problem, requirement, title, body}]."""
        out = []
        for problem in self._accepted(flow.PROBLEM, scenario_id):
            for requirement in self._accepted(flow.REQUIREMENT, problem["id"]):
                for variant in self._accepted(flow.VARIANT, requirement["id"]):
                    out.append(
                        {
                            "problem": problem["title"],
                            "requirement": requirement["title"],
                            "title": variant["title"],
                            "body": variant["body"],
                        }
                    )
        return out

    def _parent_of(self, card_id: str, snap: dict | None = None) -> str:
        snap = snap or self.store.snapshot()
        return next(
            (e["to"] for e in snap["edges"] if e["from"] == card_id and e["kind"] == "детализирует"),
            "",
        )

    def _work(self, run: Run, plan: dict) -> None:
        try:
            kind = plan["kind"]
            if kind in ("export", "export_auto"):
                self._job_export(run)
            elif kind == "autopilot":
                self._job_autopilot(run)
            elif kind == "competitors":
                self._job_competitors(run)
            elif kind == "analysis":
                self._job_analysis(run, plan)
            elif kind in ("roles", "scenarios"):
                self._job_global(run, kind)
            elif kind == "requirements":
                self._job_requirements(run, plan)
            elif kind == "variants":
                self._job_variants(run, plan)
            else:
                self._job_scenarios(run, plan)
            if run.stop:
                run.status = "stopped"
            elif run.status != "error":
                run.status = "done"
        except EngineError as error:
            run.status, run.error = "error", error.message
        except Exception as error:  # noqa: BLE001 — любая поломка показывается человеку, а не теряется в потоке
            run.status, run.error = "error", f"Внутренняя ошибка: {error}"
        finally:
            run.question = None
            self._touch()

    def stop(self) -> dict:
        run = self.run
        if run and run.status in ("running", "asking"):
            run.stop = True
            run.answered.set()
            for proc in list(run.procs):
                with suppress(OSError):
                    proc.kill()
        return {}

    # ---- вопрос помощника

    def answer(self, question_id: str, choice: dict) -> dict:
        run = self.run
        if not run or not run.question or run.question["id"] != question_id:
            raise EngineError("no_question", "Этот вопрос уже не ждёт ответа")
        run.answer = choice if isinstance(choice, dict) else {}
        run.status = "running"
        run.answered.set()
        self._touch()
        return {}

    @staticmethod
    def _answer_text(question: dict, choice: dict) -> str:
        if choice.get("type") == "rejected":
            return "ни один вариант не подходит" + (
                f"; пояснение: {choice.get('value')}" if choice.get("value") else ""
            )
        picked = [
            question["options"][i - 1]["title"]
            for i in choice.get("indexes", [])
            if 1 <= i <= len(question["options"])
        ]
        extra = _clip(choice.get("text"), 600)
        return "; ".join(picked + ([f"своё: {extra}"] if extra else [])) or "решай сам"

    # ---- вызов агента

    def _request(
        self,
        slot: dict,
        name: str,
        system: str,
        note: str,
        token: str,
        session: str,
        web: bool,
        run: Run,
    ) -> TurnResult:
        adapter = self.adapters.get(slot.get("agent") or "")
        if adapter is None:
            return TurnResult(
                error=f"Агент «{slot.get('agent')}» не настроен. Откройте «Настройки»."
            )

        def remember(proc):
            run.procs.add(proc)

        request = TurnRequest(
            workdir=self.dir / "agent" / name,
            system_text=system,
            note=note,
            env={"IDEA_GRAPH_URL": self.base_url, "IDEA_GRAPH_TOKEN": token, **self.extra_env},
            session_id=session,
            model=slot.get("model") or "",
            effort=slot.get("effort") or "",
            web_search=web,
            on_process=remember,
            extra={"token": token, "project": self},
        )
        began = time.time()
        result = adapter.run(request)
        self._journal(name, slot, began, result)
        return result

    def _journal(self, name: str, slot: dict, began: float, result: TurnResult) -> None:
        """Журнал работы агентов (.idea-graph/agents.log): время, кто, какая модель, сколько длилось, чем кончилось. Тексты графа не пишутся."""
        line = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(began)),
            "turn": name,
            "agent": slot.get("agent") or "",
            "model": slot.get("model") or "",
            "seconds": round(time.time() - began, 1),
            "cost": result.cost,
            "error": _clip(result.error, 300),
        }
        try:
            with self._log_lock:
                path = self.dir / "agents.log"
                if path.exists() and path.stat().st_size > 1_000_000:
                    path.replace(self.dir / "agents.log.1")
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass  # журнал не должен ломать работу

    # ---- исследователи

    def _research(self, run: Run, note: str) -> list[dict]:
        """Три исследователя независимо; возвращает находки всех, кто ответил."""
        reports: list[dict | None] = [None] * RESEARCHERS
        errors: list[str] = []

        def one(index: int) -> None:
            token = secrets.token_urlsafe(24)
            ctx = {"kind": "researcher", "note": note, "report": None}
            self.tokens[token] = ctx
            try:
                result = self._request(
                    self.settings.data["researchers"][index],
                    f"researcher{index + 1}",
                    tools.RESEARCHER_RULES,
                    note,
                    token,
                    "",
                    True,
                    run,
                )
                if result.error and not run.stop:
                    errors.append(f"Исследователь {index + 1}: {result.error}")
                reports[index] = ctx["report"]
            finally:
                self.tokens.pop(token, None)
                run.done += 1
                self._touch()

        run.total, run.done = RESEARCHERS, 0
        threads = [threading.Thread(target=one, args=(i,), daemon=True) for i in range(RESEARCHERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        run.notes += errors
        found = [r for r in reports if r]
        if not found and not run.stop:
            raise EngineError(
                "no_report",
                errors[0] if errors else "Исследователи не прислали результат. Попробуйте ещё раз",
            )
        return [item for r in found for item in r["findings"]]

    def _job_competitors(self, run: Run) -> None:
        sphere = self.need_sphere()
        requirements = [r["data"].get("text", "") for r in self.records("requirement")]
        known = [r["data"].get("name", "") for r in self.records("competitor")]
        note = flow.competitors(sphere["title"], requirements, known, self.rejected("competitor"))
        added = 0
        for item in self._research(run, note):
            url = _clip((item["data"] or {}).get("url") or (item["sources"] or [""])[0], 300)
            key = normalize_url(url)
            if not key or key in self.rejected("competitor") or self.bank.find(key):
                continue
            name = item["title"]
            self.bank.put(
                "competitor",
                {"name": name, "url": url, "why": item["description"], "state": "proposed"},
                AGENT,
                sources=item["sources"],
                key=key,
            )
            added += 1
        run.summary = f"Новых конкурентов: {added}"

    def _job_analysis(self, run: Run, plan: dict) -> None:
        sphere = self.need_sphere()
        targets = plan["targets"]
        by_key = {t["key"]: t for t in targets}
        note = flow.analysis(
            sphere["title"],
            [(t["data"].get("name", ""), t["data"].get("url", "")) for t in targets],
        )
        findings = self._research(run, note)
        if run.stop:
            return
        if plan.get("redo"):
            for target in targets:
                for fact in self.facts_of(target["key"]):
                    self.bank.remove(fact["id"])
        added = 0
        for item in findings:
            kind = _clip(item["kind"], 20).lower()
            owner = normalize_url((item["data"] or {}).get("competitor", ""))
            if kind not in ("роль", "процесс", "фича") or owner not in by_key:
                continue
            text = item["title"] + (f". {item['description']}" if item["description"] else "")
            self.bank.put(
                "fact",
                {"kind": kind, "text": text, "competitor": owner},
                AGENT,
                sources=item["sources"],
                key=f"{owner}|{kind}|{item['title'].lower()}",
            )
            added += 1
        for target in targets:
            self.bank.update(target["id"], AGENT, data={**target["data"], "analyzed": True})
        run.summary = f"Фактов в банке: {added}"

    # ---- помощник

    def _job_global(self, run: Run, kind: str) -> None:
        self.need_sphere()
        ctx = self.knowledge()
        roles = [c["title"] for c in self.cards(flow.ROLE)]
        if kind == "roles":
            note, allowed = flow.roles(ctx, roles, self.rejected(flow.ROLE)), {flow.ROLE}
        else:
            known = [c["title"] for c in self.cards(flow.SCENARIO)]
            actors = [
                c["title"]
                for c in self.cards(flow.ROLE)
                if self._role_type(c) != flow.COMPONENT_TYPE
            ]
            note, allowed = (
                flow.scenarios(ctx, actors, known, self.rejected(flow.SCENARIO)),
                {flow.SCENARIO},
            )
        self._converse(run, note, allowed, "")

    def _job_scenarios(self, run: Run, plan: dict) -> None:
        kind = plan["kind"]
        if kind == "solution":
            self._need_components()
        run.total, run.done = len(plan["scenarios"]), 0
        for scenario in plan["scenarios"]:
            if run.stop:
                return
            ctx = self.knowledge(
                skip=scenario["id"]
            )  # новое согласуется с уже построенными сценариями
            info = self.scenario_info(self.card(scenario["id"]))
            if kind == "problems":
                note = flow.problems(
                    ctx,
                    info,
                    [c["title"] for c in self.cards(flow.PROBLEM, scenario["id"])],
                    self.rejected(flow.PROBLEM),
                )
                allowed = {flow.PROBLEM}
            else:
                note, allowed = (
                    flow.solution(
                        ctx,
                        info,
                        self._solved(scenario["id"]),
                        [r["title"] for r in self._unsolved_requirements(scenario["id"])],
                        self._names_groups(),
                    ),
                    {flow.SOLUTION},
                )
            self._converse(run, note, allowed, scenario["id"])
            run.done += 1
            self._touch()

    def _job_requirements(self, run: Run, plan: dict) -> None:
        """Требования к системе — по одной проблеме за ход: они вырастают из проблемы."""
        run.total, run.done = len(plan["problems"]), 0
        snap = self.store.snapshot()
        for problem in plan["problems"]:
            if run.stop:
                return
            scenario_id = self._parent_of(problem["id"], snap)
            ctx = self.knowledge(skip=scenario_id)
            note = flow.requirements(
                ctx,
                self.scenario_info(self.card(scenario_id)),
                {"title": problem["title"], "body": problem["body"]},
                [c["title"] for c in self.cards(flow.REQUIREMENT, problem["id"])],
                self.rejected(flow.REQUIREMENT),
            )
            self._converse(run, note, {flow.REQUIREMENT}, problem["id"])
            run.done += 1
            self._touch()

    def _job_variants(self, run: Run, plan: dict) -> None:
        """Варианты решения — по одному требованию за ход: они вырастают из требования."""
        run.total, run.done = len(plan["requirements"]), 0
        snap = self.store.snapshot()
        for requirement in plan["requirements"]:
            if run.stop:
                return
            problem = self.card(self._parent_of(requirement["id"], snap))
            scenario_id = self._parent_of(problem["id"], snap)
            ctx = self.knowledge(skip=scenario_id)
            note = flow.variants(
                ctx,
                self.scenario_info(self.card(scenario_id)),
                {"title": requirement["title"], "body": requirement["body"]},
                {"title": problem["title"], "body": problem["body"]},
                [c["title"] for c in self.cards(flow.VARIANT, requirement["id"])],
                self.rejected(flow.VARIANT),
            )
            self._converse(run, note, {flow.VARIANT}, requirement["id"])
            run.done += 1
            self._touch()

    def _converse(self, run: Run, note: str, allowed: set, target: str, limit: int = 0) -> None:
        """Ходы помощника, пока он не закончит; в спорной ситуации он спрашивает человека и продолжает с его ответом."""
        session, task = "", note
        if run.auto:
            note = task = "Автоматический режим: вопросов не задавай, реши сам.\n\n" + note
        while True:
            token = secrets.token_urlsafe(24)
            ctx = {
                "kind": "helper",
                "run": run,
                "allowed": allowed,
                "target": target,
                "asked": False,
                "note": note,
                "created": 0,
                "auto": run.auto,
                "limit": limit,
            }
            self.tokens[token] = ctx
            run.answered.clear()
            try:
                result = self._request(
                    self.settings.data["helper"],
                    "helper",
                    tools.HELPER_RULES,
                    note,
                    token,
                    session,
                    False,
                    run,
                )
            finally:
                self.tokens.pop(token, None)
            if run.stop:
                return
            if result.error:
                raise EngineError("agent_error", result.error)
            session = result.session_id or session
            if ctx["asked"]:
                run.status = "asking"
                self._touch()
                run.answered.wait()
                if run.stop:
                    return
                question = run.question
                note = (
                    f"Ответ человека на твой вопрос «{question['question']}»: {self._answer_text(question, run.answer or {})}. "
                    "Продолжи работу с учётом ответа.\n\nЗадача прежняя:\n" + task
                )
                run.question = None
                continue
            if not ctx["created"]:
                run.notes.append(
                    "Помощник ничего не добавил" + (f": {result.text[:200]}" if result.text else "")
                )
            return

    # ------------------------------------------------------------------ инструменты агентов

    def tools_for(self, token: str) -> list[dict]:
        ctx = self.tokens.get(token)
        if ctx is None:
            raise EngineError("bad_token", "Ход уже закончен: инструменты недоступны")
        if ctx["kind"] == "researcher":
            return tools.RESEARCH_TOOLS
        if ctx["kind"] == "bounds":
            return tools.BOUNDS_TOOLS
        if ctx["kind"] == "check":
            return tools.CHECK_TOOLS
        if ctx["kind"] == "pilot":
            return tools.PILOT_TOOLS
        return (
            [x for x in tools.HELPER_TOOLS if x["name"] != "ask"]
            if ctx.get("auto")
            else tools.HELPER_TOOLS
        )

    def tool_call(self, token: str, tool: str, args: dict) -> str:
        ctx = self.tokens.get(token)
        if ctx is None:
            raise EngineError("bad_token", "Ход уже закончен: инструменты недоступны")
        args = args if isinstance(args, dict) else {}
        if tool == "turn_info":
            return ctx["note"]
        if ctx["kind"] == "pilot":
            if tool != "decide":
                raise EngineError("unknown_tool", "Здесь есть только turn_info и decide")
            raw = args.get("accept")
            ids = [str(i) for i in raw if i is not None] if isinstance(raw, list) else []
            rm = args.get("remove")
            ctx["decision"] = {
                "accept": ids,
                "reason": _clip(args.get("reason"), 300),
                "remove": [str(i) for i in rm if i is not None] if isinstance(rm, list) else [],
            }
            return "Принято. Заверши ход."
        if ctx["kind"] == "check":
            if tool == "report":
                return self._tool_check_report(ctx, args)
            raise EngineError("unknown_tool", "Здесь есть только turn_info и report")
        if ctx["kind"] == "bounds":
            if tool == "ask":
                return self._tool_ask(ctx, args)
            if tool == "finish":
                topics = args.get("topics") if isinstance(args.get("topics"), dict) else {}
                missing = [name for name in flow.BOUND_TOPICS if not _clip(topics.get(name), 120)]
                if missing:
                    raise EngineError(
                        "topics",
                        "В topics нет статуса по темам: "
                        + "; ".join(missing)
                        + ". По каждой теме — что решено ответами или почему неприменимо; если тема не ясна — задай вопрос",
                    )
                ctx["finished"] = True
                return "Принято."
            raise EngineError("unknown_tool", "Здесь есть только turn_info, ask и finish")
        if ctx["kind"] == "researcher":
            if tool != "report":
                raise EngineError("unknown_tool", "У исследователя есть только turn_info и report")
            return self._tool_report(ctx, args)
        if tool == "graph_apply":
            return self._tool_apply(ctx, args)
        if tool == "ask":
            return self._tool_ask(ctx, args)
        if tool == "finish":
            ctx["run"].summary = _clip(args.get("summary"), 400)
            return "Принято."
        raise EngineError("unknown_tool", "Есть только graph_apply, ask, finish и turn_info")

    @staticmethod
    def _tool_report(ctx: dict, args: dict) -> str:
        findings = []
        for item in args.get("findings") or []:
            if not isinstance(item, dict) or not _clip(item.get("title"), 200):
                continue
            data = item.get("data")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    data = {}
            sources = item.get("sources") or []
            findings.append(
                {
                    "kind": _clip(item.get("kind"), 40),
                    "title": _clip(item.get("title"), 200),
                    "description": _clip(item.get("description"), 400),
                    "data": data if isinstance(data, dict) else {},
                    "sources": [
                        _clip(s, 300)
                        for s in (sources if isinstance(sources, list) else [sources])
                        if _clip(s, 300)
                    ][:5],
                }
            )
        if not findings:
            raise EngineError(
                "empty_report", "В отчёте нет находок. Найди хотя бы что-то подтверждённое"
            )
        ctx["report"] = {"findings": findings}
        return "Принято. Заверши ход."

    def _tool_ask(self, ctx: dict, args: dict) -> str:
        if ctx.get("auto"):
            raise EngineError(
                "auto_mode", "Сейчас автоматический режим: вопросов не задавай, реши сам"
            )
        if ctx["asked"]:
            raise EngineError("one_question", "За ход можно задать только один вопрос")
        options = [
            {"title": _clip(o.get("title"), 400), "consequence": ""}
            for o in args.get("options") or []
            if isinstance(o, dict) and _clip(o.get("title"), 400)
        ][:4]
        question = _clip(args.get("question"), 600)
        if not question or len(options) < 2:
            raise EngineError("bad_question", "Нужен вопрос и от двух вариантов ответа")
        long = rules.question_errors(question, [o["title"] for o in options])
        if long:
            raise EngineError("too_long", "; ".join(long))
        recommended = [
            i
            for i in (args.get("recommended") or [])
            if isinstance(i, int) and 1 <= i <= len(options)
        ]
        ctx["run"].question = {
            "id": secrets.token_urlsafe(6),
            "question": question,
            "options": options,
            "recommended": recommended,
            "reason": "",
        }
        ctx["asked"] = True
        return "Вопрос задан. Заверши ход: ответ придёт в следующем ходе."

    def _tool_apply(self, ctx: dict, args: dict) -> str:
        ops = args.get("ops")
        if not isinstance(ops, list) or not ops:
            raise EngineError("invalid_ops", "Ожидался непустой список операций")
        sphere = self.need_sphere()
        roles = {r["title"].lower(): r for r in self.cards(flow.ROLE)}
        build, skipped, synced = [], [], []
        for number, op in enumerate(ops, 1):
            if not isinstance(op, dict) or op.get("op") != "add_card":
                raise EngineError("bad_op", f"Операция {number}: разрешён только add_card")
            kind = str(op.get("kind") or "")
            if kind not in ctx["allowed"]:
                raise EngineError(
                    "bad_kind",
                    f"Операция {number}: сейчас можно создавать только карточки вида: {', '.join(sorted(ctx['allowed']))}",
                )
            title = _clip(op.get("title"), 200)
            if not title:
                raise EngineError("bad_title", f"Операция {number}: нужен title")
            parent = ctx["target"] or sphere["id"]
            replaces = (
                kind == flow.SOLUTION
            )  # диаграмма сценария одна: новая заменяет старую, а не считается дублем
            if not replaces and (
                title.lower() in self.rejected(kind)
                or any(
                    c["title"].lower() == title.lower()
                    for c in self.cards(kind, parent if ctx["target"] else None)
                )
            ):
                skipped.append(title)
                continue
            attrs, links, status = {}, [], PROPOSED
            raw = op.get("attrs") if isinstance(op.get("attrs"), dict) else {}
            if kind == flow.SCENARIO:
                try:
                    usecase = diagrams.normalize_usecase(raw.get("usecase"))
                except ValueError as error:
                    raise EngineError("bad_usecase", f"Операция {number}: {error}") from None
                if not usecase["cases"]:
                    raise EngineError(
                        "bad_usecase",
                        f"Операция {number}: в attrs.usecase нужны cases с name и actors (роли)",
                    )
                parts = sorted(
                    {
                        a
                        for c in usecase["cases"]
                        for a in c["actors"]
                        if a.lower() in roles
                        and self._role_type(roles[a.lower()]) == flow.COMPONENT_TYPE
                    }
                )
                if parts:
                    raise EngineError(
                        "bad_actor",
                        f"Операция {number}: {', '.join(parts)} — компоненты системы, а не актёры. "
                        "Актёры — только люди и внешние системы",
                    )
                unknown = sorted(
                    {a for c in usecase["cases"] for a in c["actors"] if a.lower() not in roles}
                )
                if unknown:
                    raise EngineError(
                        "bad_actor",
                        f"Операция {number}: актёры {', '.join(unknown)} — не роли. Роли: "
                        + ", ".join(r["title"] for r in roles.values()),
                    )
                usecase["system"] = usecase["system"] or title
                actors = {a.lower() for c in usecase["cases"] for a in c["actors"]}
                links = [{"to": roles[a]["id"], "kind": flow.PARTICIPATES} for a in actors]
                attrs = {"usecase": usecase}
            elif kind == flow.SOLUTION:
                try:
                    diagram = diagrams.normalize(
                        {
                            "participants": (raw.get("diagram") or {}).get("participants"),
                            "steps": (raw.get("diagram") or {}).get("steps"),
                        }
                    )
                except (ValueError, AttributeError) as error:
                    raise EngineError("bad_diagram", f"Операция {number}: {error}") from None
                if len(diagram["steps"]) < 2:
                    raise EngineError(
                        "bad_diagram",
                        f"Операция {number}: в attrs.diagram нужны participants и минимум два шага steps (from, to, text)",
                    )
                diagram = rules.canonical_names(
                    diagram, self._role_names()
                )  # регистр и пробелы приводятся к имени из списка
                broken = rules.diagram_errors(kind, diagram, self, parent)
                if broken:
                    raise EngineError("bad_diagram", f"Операция {number}: " + "; ".join(broken))
                attrs, status = (
                    {"diagram": diagram, "basis": check.basis(self, parent, kind)},
                    ACCEPTED,
                )
                synced.append((parent, diagram))
                for old in self.cards(kind, parent):
                    build.append({"op": "delete_card", "id": old["id"]})
            elif kind == flow.ROLE:
                if self.roles_confirmed:
                    raise EngineError(
                        "roles_locked",
                        "Список ролей подтверждён человеком и в этом прогоне не меняется",
                    )
                role_type = str(raw.get("type") or "").strip().lower()
                if role_type not in flow.ROLE_TYPES:
                    raise EngineError(
                        "bad_type",
                        f"Операция {number}: у роли обязателен attrs.type — один из: "
                        + ", ".join(flow.ROLE_TYPES),
                    )
                attrs = {"type": role_type}
            elif kind == flow.PROBLEM:
                attrs = {"importance": _clip(raw.get("importance"), 20).lower()}
            too_long = rules.text_errors(kind, str(op.get("body") or "").strip())
            if too_long:
                raise EngineError("too_long", f"Операция {number}: " + "; ".join(too_long))
            build.append(
                {
                    "op": "add_card",
                    "kind": kind,
                    "title": title,
                    "body": str(op.get("body") or "").strip()[:4000],
                    "status": status,
                    "attrs": attrs,
                    "parent": parent,
                    "links": links,
                }
            )
        created = 0
        adds = sum(1 for b in build if b["op"] == "add_card")
        if ctx.get("limit") and ctx["created"] + adds > ctx["limit"]:
            raise EngineError(
                "too_many",
                f"Сейчас можно создать не более {ctx['limit']} карточки: оставь одну, самую важную",
            )
        if build:
            try:
                result = self.store.apply(build, AGENT, note="Предложено помощником")
            except GraphError as error:
                raise EngineError(error.code, error.message) from None
            created = len(result["ids"])
            ctx["created"] += created
            for scenario_id, diagram in synced:
                self._sync_scenario_roles(scenario_id, diagram, AGENT)
            self._touch()
        return f"Добавлено карточек: {created}." + (
            f" Уже есть или отклонялись, пропущено: {', '.join(skipped)}." if skipped else ""
        )

    # ------------------------------------------------------------------ проверка согласованности перед выгрузкой

    def _tool_check_report(self, ctx: dict, args: dict) -> str:
        report, errors = check.parse_report(self, args, ctx["bounds"], ctx["found"])
        if errors:
            raise EngineError(
                "bad_report",
                "Отчёт не принят, исправь и вызови report снова: " + "; ".join(errors[:8]),
            )
        ctx["report"] = report
        return "Отчёт принят. Заверши ход."

    def _checker_slot(self, run: Run) -> dict:
        """Независимый проверяющий: модель из настройки «Проверяющий»; совпадение с той, что строит, — предупреждение."""
        slot = self.settings.data.get("checker") or {}
        for name, label in (("helper", "Помощник"), ("autopilot", "Автопилот")):
            other = self.settings.data.get(name) or {}
            if (other.get("agent"), other.get("model")) == (slot.get("agent"), slot.get("model")):
                run.notes.append(
                    f"Внимание: проверяющий совпадает с «{label}», проверка не независима. Выберите другую модель в «Настройках»."
                )
                break
        return slot

    def _exists(self, card_id: str) -> bool:
        try:
            self.store.card(card_id)
            return True
        except GraphError:
            return False

    def _checker_turn(
        self,
        run: Run,
        slot: dict,
        sphere: dict,
        bounds: list[str],
        found: list[dict],
        text: str | None = None,
    ) -> dict | None:
        """Один вызов проверяющего на весь граф. Возвращает отчёт (None — остановлено)."""
        note = flow.verify(
            sphere["title"], bounds, text if text is not None else check.graph_text(self), found
        )
        token = secrets.token_urlsafe(24)
        ctx = {
            "kind": "check",
            "run": run,
            "note": note,
            "report": None,
            "bounds": bounds,
            "found": found,
        }
        self.tokens[token] = ctx
        run.title = "Проверяющий читает граф…"
        self._touch()
        try:
            result = self._request(slot, "check", tools.CHECK_RULES, note, token, "", False, run)
        finally:
            self.tokens.pop(token, None)
        if run.stop:
            return None
        if result.error:
            raise EngineError("agent_error", result.error)
        if ctx["report"] is None:
            raise EngineError("no_report", "Проверяющий не сдал отчёт. Повторите выгрузку")
        return ctx["report"]

    def _ask_item(self, run: Run, item: dict, number: int, total: int) -> int | None:
        """Вопрос человеку по одному пункту; возвращает номер выбранного варианта (None — остановлено)."""
        while True:
            run.answered.clear()
            run.question = {
                "id": secrets.token_urlsafe(6),
                "question": item["question"],
                "single": True,
                "reason": "",
                "options": [{"title": o["title"], "consequence": ""} for o in item["options"]],
                "recommended": [item["recommended"]],
            }
            run.title = f"Проверка: вопрос {number} из {total}"
            run.status = "asking"
            self._touch()
            run.answered.wait()
            choice = run.answer or {}
            run.question = None
            if run.stop:
                return None
            picked = [
                i
                for i in choice.get("indexes", [])
                if isinstance(i, int) and 1 <= i <= len(item["options"])
            ]
            if picked:
                return picked[0]

    def _fix_diagrams(self, run: Run) -> None:
        """В конце, после всех правок: диаграммы без вопросов перестраиваются помощником по итоговым проблемам и решениям."""
        todo = list(
            dict.fromkeys(i["scenario"] for i in check.issues(self) if i["code"] == "diagram")
        )
        for index, sid in enumerate(todo, 1):
            if run.stop:
                return
            run.title = f"Перестраиваю диаграммы: {index} из {len(todo)}"
            self._touch()
            try:
                self._rebuild(run, self.card(sid))
            except EngineError as error:
                run.notes.append(f"Диаграммы не перестроились: {error.message}")

    def _job_export(self, run: Run) -> None:
        """Независимая проверка графа и файл. Модель-проверяющий читает граф один раз за проход; вопросы и варианты правок строит код.
        Дальше проходов столько, сколько нужно: пока проход что-то правит, граф проверяется снова; без правок — выгрузка."""
        sphere = self.need_sphere()
        if self._accepted_all(flow.SCENARIO):
            self._need_components()
        auto = run.kind == "export_auto"
        slot = self._checker_slot(run)
        seen: set = set()
        fixed = 0
        while True:
            bounds = [
                str((r["data"] or {}).get("text", ""))[len(flow.BOUNDARY) :]
                for r in self._boundary_records()
            ]
            with self.frozen():  # найденное кодом и текст графа для проверяющего — из одного снимка
                found = check.issues(self)
                text = check.graph_text(self)
            run.total = run.done = 0  # числа в заголовке — только пока идёт вопрос или правка
            report = self._checker_turn(run, slot, sphere, bounds, found, text)
            if report is None:
                return
            items = check.build_items(self, report, bounds, found)
            signature = tuple(sorted(item["question"] for item in items))
            if items and signature in seen:
                raise EngineError(
                    "no_progress",
                    "Проверка ходит по кругу: те же разногласия после правок. Разберите их вручную: "
                    + "; ".join(i["question"] for i in items[:3]),
                )
            seen.add(signature)
            applied = 0
            run.total = len(items)
            for number, item in enumerate(items, 1):
                if run.stop:
                    return
                if not all(self._exists(card_id) for card_id in item["cards"]):
                    continue  # карточку уже убрала предыдущая правка
                pick = (
                    item["recommended"] if auto else self._ask_item(run, item, number, len(items))
                )
                if pick is None:
                    return
                option = item["options"][pick - 1]
                for fix in option["fixes"]:
                    try:
                        self._apply_fix(run, fix)
                    except EngineError as error:
                        run.notes.append(f"Не вышло ({option['title']}): {error.message}")
                        break
                else:
                    applied += 1
                    run.notes.append(option["title"])
                run.status, run.done = "running", number
                self._touch()
            fixed += applied
            if items and not applied:
                raise EngineError(
                    "no_progress",
                    "Правки не применились: " + "; ".join(i["question"] for i in items[:3]),
                )
            self._fix_diagrams(run)
            if run.stop:
                return
            left = check.issues(self)
            if not items:
                if left:
                    raise EngineError(
                        "no_progress",
                        "Остались разногласия: " + "; ".join(i["text"] for i in left[:3]),
                    )
                break
        path = self.do_export_md({})["path"]
        run.summary = (
            f"Разногласий нет, документация записана: {path}"
            if not fixed
            else f"{'Автоматически исправлено' if auto else 'Исправлено'} правок: {fixed}. Документация записана: {path}"
        )

    def _apply_fix(self, run: Run, fix: dict) -> None:
        do, card_id = fix["do"], fix["id"]
        total, done = run.total, run.done  # фоновые работы помощника трогают счётчики прогона
        if do == "remove":
            self.do_remove({"id": card_id})
        elif do == "choose_variant":
            self.do_choose_variant({"id": card_id})
        elif do == "suggest_variants":
            self._job_variants(run, {"requirements": [self.card(card_id)]})
        elif do == "suggest_requirements":
            self._job_requirements(run, {"problems": [self.card(card_id)]})
        elif do == "edit":
            card = self.card(card_id)
            op = {"op": "update_card", "id": card_id}
            for field in ("title", "body"):
                if field in fix:
                    op[field] = fix[field]
            if "importance" in fix:
                op["attrs"] = {**(card.get("attrs") or {}), "importance": fix["importance"]}
            self._human([op], f"Правка при проверке: {card['title']}")
        elif do == "rebuild":
            self._rebuild(run, self.card(card_id))
        run.total, run.done = total, done

    def _rebuild(self, run: Run, scenario: dict) -> None:
        """Диаграмма «решение» сценария заново по выбранным решениям; если строить не из чего — убрать."""
        sid = scenario["id"]
        if self._solved(sid):
            self._job_scenarios(run, {"kind": "solution", "scenarios": [scenario]})
            return
        old = self.cards(flow.SOLUTION, sid)
        if old:
            self._human(
                [{"op": "delete_card", "id": c["id"]} for c in old],
                "Диаграмма убрана: строить не из чего",
            )

    # ------------------------------------------------------------------ автопилот

    def _stage(self, run: Run, text: str) -> None:
        run.title = "Автопилот: " + text
        self._touch()

    def _boundary_records(self) -> list[dict]:
        return [
            r
            for r in self.records("requirement")
            if str((r["data"] or {}).get("text", "")).startswith(flow.BOUNDARY)
        ]

    def _bounds(self, run: Run) -> None:
        """Сильная модель по одному задаёт закрытые вопросы о границах, пока они не станут понятны (не больше предела); ответы — в банке."""
        sphere = self.need_sphere()
        qa: list[tuple[str, str]] = []
        session, idle = "", 0
        while len(qa) < flow.BOUNDS_LIMIT:
            token = secrets.token_urlsafe(24)
            note = flow.bounds(sphere["title"], qa, flow.BOUNDS_LIMIT - len(qa))
            ctx = {"kind": "bounds", "run": run, "asked": False, "finished": False, "note": note}
            self.tokens[token] = ctx
            run.answered.clear()
            try:
                result = self._request(
                    self.settings.data["autopilot"],
                    "autopilot",
                    tools.BOUNDS_RULES,
                    note,
                    token,
                    session,
                    False,
                    run,
                )
            finally:
                self.tokens.pop(token, None)
            if run.stop:
                return
            if result.error:
                raise EngineError("agent_error", result.error)
            session = result.session_id or session
            if ctx["finished"]:
                return
            if not ctx["asked"]:
                idle += 1
                if idle >= 2:
                    raise EngineError(
                        "no_decision",
                        "Автопилот не задал вопроса и не завершил уточнение границ. Повторите",
                    )
                continue
            idle = 0
            run.status = "asking"
            self._touch()
            run.answered.wait()
            if run.stop:
                return
            question = run.question
            answer = self._answer_text(question, run.answer or {})
            qa.append((question["question"], answer))
            text = f"{flow.BOUNDARY}{question['question']} → {answer}"
            self.bank.put("requirement", {"text": text}, HUMAN, key=f"requirement|{text.lower()}")
            run.question = None
            run.done = len(qa)

    def _pilot_decide(
        self, run: Run, what: str, items: list[dict], rule: str, improve: bool = False
    ) -> tuple[set[str], set[str]]:
        """Сильная модель выбирает, что принять из предложенного (и, в режиме улучшения, что убрать из принятого)."""
        if not items:
            return set(), set()
        note = flow.review(what, self.knowledge(), items, rule, improve)
        token = secrets.token_urlsafe(24)
        ctx = {"kind": "pilot", "run": run, "note": note, "decision": None}
        self.tokens[token] = ctx
        try:
            result = self._request(
                self.settings.data["autopilot"],
                "autopilot",
                tools.PILOT_RULES,
                note,
                token,
                "",
                False,
                run,
            )
        finally:
            self.tokens.pop(token, None)
        if run.stop:
            return set(), set()
        if result.error:
            raise EngineError("agent_error", result.error)
        if ctx["decision"] is None:
            raise EngineError(
                "no_decision", f"Автопилот не принял решения ({what}). Попробуйте ещё раз"
            )
        fresh = {i["id"] for i in items if not i.get("have")}
        have = {i["id"] for i in items if i.get("have")}
        accept = {a for a in ctx["decision"]["accept"] if a in fresh}
        remove = {a for a in ctx["decision"]["remove"] if a in have} if improve else set()
        run.notes.append(
            f"{what}: принято {len(accept)} из {len(fresh)}"
            + (f", убрано {len(remove)}" if remove else "")
            + (f" — {ctx['decision']['reason']}" if ctx["decision"]["reason"] else "")
        )
        return accept, remove

    def _decide_cards(
        self,
        run: Run,
        what: str,
        kind: str,
        parent: str | None,
        rule: str,
        review_existing: bool = False,
    ) -> None:
        """Предложенное принимается или отклоняется; в режиме улучшения заодно убирается слабое из уже принятого."""
        cards = self.cards(kind, parent)
        pending = [c for c in cards if c["status"] == PROPOSED]
        existing = [c for c in cards if c["status"] == ACCEPTED] if review_existing else []
        items = [
            {"id": c["id"], "title": c["title"], "body": c["body"], "have": c in existing}
            for c in existing + pending
        ]
        accept, remove = self._pilot_decide(run, what, items, rule, review_existing)
        if run.stop:
            return
        for c in pending:
            (self.do_accept if c["id"] in accept else self.do_remove)({"id": c["id"]})
        for c in existing:
            if c["id"] in remove:
                self.do_remove({"id": c["id"]})
        self._touch()

    def _step_cards(
        self, run: Run, label: str, kind: str, parent: str | None, rule: str, generate
    ) -> None:
        """Один шаг цепочки в трёх режимах: нет — строим; есть — доводим до согласования; режим улучшения — ещё и дополняем."""
        cards = self.cards(kind, parent)
        have = [c for c in cards if c["status"] == ACCEPTED]
        pending = [c for c in cards if c["status"] == PROPOSED]
        if run.improve and have:
            self._decide_cards(
                run, label, kind, parent, rule, review_existing=True
            )  # оценить то, что есть
            if run.stop:
                return
            generate()  # дополнить недостающим
            if run.stop:
                return
            self._decide_cards(run, label, kind, parent, rule)
        else:
            if not have and not pending:
                generate()  # ничего нет — строим
                if run.stop:
                    return
            self._decide_cards(run, label, kind, parent, rule)  # доводим до согласования

    def _survey(self) -> str:
        """Что уже есть в проекте: по этому автопилот выбирает режим."""
        cards = self.store.snapshot()["cards"]

        def count(kind):
            return [
                sum(1 for c in cards if c["kind"] == kind and c["status"] == ACCEPTED),
                sum(1 for c in cards if c["kind"] == kind and c["status"] == PROPOSED),
            ]

        comps = self.records("competitor")
        parts = [
            (
                "конкуренты",
                [
                    sum(1 for r in comps if r["data"].get("state") == "accepted"),
                    sum(1 for r in comps if r["data"].get("state") == "proposed"),
                ],
            ),
            ("факты", [len(self.records("fact")), 0]),
        ] + [
            (n, count(k))
            for n, k in (
                ("роли", flow.ROLE),
                ("сценарии", flow.SCENARIO),
                ("проблемы", flow.PROBLEM),
                ("требования", flow.REQUIREMENT),
                ("варианты", flow.VARIANT),
                ("«решение»", flow.SOLUTION),
            )
        ]
        found = [
            f"{n}: {a} принято" + (f", {p} ждёт решения" if p else "")
            for n, (a, p) in parts
            if a or p
        ]
        return "; ".join(found)

    def _job_autopilot(self, run: Run) -> None:
        self.need_sphere()
        survey = self._survey()
        mode = (
            "построить"
            if not survey
            else ("улучшить" if run.improve else "довести до согласования")
        )
        run.improve = run.improve and bool(survey)
        run.notes.append(f"Уже есть: {survey or 'ничего'}. Режим: {mode}")
        if not self._boundary_records():
            self._bounds(run)
            if run.stop:
                return
        run.status = "running"
        self._stage(run, "конкуренты")
        self._pilot_competitors(run)
        if run.stop:
            return
        self._stage(run, "анализ конкурентов")
        try:
            plan = self._plan("analysis", [], False)
        except EngineError:
            plan = None
        if plan:
            self._job_analysis(run, plan)
        if run.stop:
            return
        if self._roles_locked() and not run.improve:
            run.notes.append("Список ролей подтверждён вами: автопилот его не меняет")
        else:
            self._pilot_roles(run)  # единый список имён определяется до сценариев и диаграмм
            if run.stop:
                return
        if not run.each:
            self._stage(run, "сценарии")
            self._step_cards(
                run,
                "сценарии",
                flow.SCENARIO,
                None,
                SCENARIO_RULE,
                lambda: self._job_global(run, "scenarios"),
            )
            if run.stop:
                return
            if not self._accepted_all(flow.SCENARIO):
                raise EngineError(
                    "pilot_stuck",
                    "Автопилот не принял ни одной карточки «сценарии»: добавьте свои или повторите",
                )
        if run.each:
            count = self._each_scenario(run)
            if not run.stop:
                run.summary = f"Автопилот ({mode}, по сценариям) прошёл цепочку: сценариев {count}"
            return
        scenarios = self._accepted_all(flow.SCENARIO)
        for number, scenario in enumerate(scenarios, 1):
            if run.stop:
                return
            self._scenario_pilot(run, scenario, f"{number}/{len(scenarios)} «{scenario['title']}»")
        if not run.stop:
            run.summary = f"Автопилот ({mode}) прошёл цепочку: сценариев {len(scenarios)}"

    def _pilot_roles(self, run: Run) -> None:
        """Шаг «роли» автопилота: единый список ролей и компонентов; по его итогам список считается подтверждённым и дальше не меняется."""
        self._set_confirmed(False)  # пока идёт шаг, список открыт
        self._stage(run, "роли")
        self._step_cards(
            run,
            "роли",
            flow.ROLE,
            None,
            "Принимай полный строгий список имён: люди, внешние системы, разрешённые границами, и компоненты системы (минимум один). "
            "Ничего лишнего, что границы не требуют.",
            lambda: self._job_global(run, "roles"),
        )
        if run.stop:
            return
        if not self._accepted_all(flow.ROLE):
            raise EngineError(
                "pilot_stuck",
                "Автопилот не принял ни одной карточки «роли»: добавьте свои или повторите",
            )
        if not self._components():
            self._stage(run, "компоненты системы")
            self._job_global(run, "roles")
            if run.stop:
                return
            self._decide_cards(
                run,
                "роли и компоненты",
                flow.ROLE,
                None,
                "Принимай компоненты системы (Сайт, Админка…) и недостающие роли строго по границам; минимум один компонент.",
            )
            if run.stop:
                return
            if not self._components():
                raise EngineError(
                    "pilot_stuck",
                    "Автопилот не определил компоненты системы. Добавьте их на шаге «Роли» вручную",
                )
        self._set_confirmed(True)

    def _each_scenario(self, run: Run) -> int:
        """Режим «по сценариям»: каждый сценарий целиком (проблемы → требования → варианты → «решение»), и только потом следующий.
        Сначала доводятся уже имеющиеся по одному, затем по одному добавляются новые, пока агент не скажет «основное покрыто»."""
        self._stage(run, "сценарии")
        cards = self.cards(flow.SCENARIO)
        if any(c["status"] == PROPOSED for c in cards) or (run.improve and cards):
            self._decide_cards(
                run, "сценарии", flow.SCENARIO, None, SCENARIO_RULE, review_existing=run.improve
            )
            if run.stop:
                return 0
        built, misses = set(), 0
        while not run.stop:
            waiting = [c for c in self._accepted_all(flow.SCENARIO) if c["id"] not in built]
            if waiting:
                scenario = waiting[0]
            else:
                if len(built) >= flow.SCENARIO_CAP:
                    run.notes.append(
                        f"Достигнут предел {flow.SCENARIO_CAP} сценариев за один прогон"
                    )
                    break
                state, scenario = self._next_scenario(run)
                if state == "enough":
                    break
                if state == "rejected":
                    misses += 1
                    if misses >= 2:
                        run.notes.append(
                            "Два предложенных сценария подряд отклонены: считаю, что основное покрыто"
                        )
                        break
                    continue
                misses = 0
            built.add(scenario["id"])
            self._scenario_pilot(run, scenario, f"{len(built)} «{scenario['title']}»")
            run.done = len(built)
            self._touch()
        if not self._accepted_all(flow.SCENARIO):
            raise EngineError(
                "pilot_stuck", "Автопилот не принял ни одного сценария: добавьте свои или повторите"
            )
        return len(built)

    def _next_scenario(self, run: Run) -> tuple[str, dict | None]:
        """Агент предлагает один следующий сценарий (или говорит «хватит»), автопилот принимает или отклоняет его."""
        self._stage(run, "следующий сценарий")
        known = [c["id"] for c in self.cards(flow.SCENARIO)]
        roles = [
            c["title"]
            for c in self._accepted_all(flow.ROLE)
            if self._role_type(c) != flow.COMPONENT_TYPE
        ]
        note = flow.next_scenario(
            self.knowledge(),
            roles,
            [c["title"] for c in self._accepted_all(flow.SCENARIO)],
            self.rejected(flow.SCENARIO),
        )
        self._converse(run, note, {flow.SCENARIO}, "", limit=1)
        if run.stop:
            return "stop", None
        fresh = [c for c in self.cards(flow.SCENARIO) if c["id"] not in known]
        if not fresh:
            return "enough", None
        self._decide_cards(run, "сценарий", flow.SCENARIO, None, SCENARIO_RULE)
        if run.stop:
            return "stop", None
        taken = [
            c for c in self._accepted_all(flow.SCENARIO) if c["id"] in {f["id"] for f in fresh}
        ]
        return ("accepted", taken[0]) if taken else ("rejected", None)

    def _pilot_competitors(self, run: Run) -> None:
        def decide(review_existing: bool) -> None:
            comps = self.records("competitor")
            pending = [r for r in comps if r["data"].get("state") == "proposed"]
            existing = (
                [r for r in comps if r["data"].get("state") == "accepted"]
                if review_existing
                else []
            )
            items = [
                {
                    "id": r["id"],
                    "title": r["data"].get("name", ""),
                    "have": r in existing,
                    "body": f"{r['data'].get('url', '')} {r['data'].get('why', '')}",
                }
                for r in existing + pending
            ]
            accept, remove = self._pilot_decide(
                run,
                "конкуренты",
                items,
                "Принимай только сайты настоящих бизнесов этой сферы: не конструкторы сайтов, не гиганты, не агрегаторы.",
                review_existing,
            )
            if run.stop:
                return
            for r in pending:
                if r["id"] in accept:
                    self.do_bank_update({"id": r["id"], "state": "accepted"})
                else:
                    self.do_bank_remove({"id": r["id"]})
            for r in existing:
                if r["id"] in remove:
                    self.do_bank_remove({"id": r["id"]})

        have = self.accepted_competitors()
        pending = [r for r in self.records("competitor") if r["data"].get("state") == "proposed"]
        if run.improve and have:
            decide(True)
            if run.stop:
                return
            self._job_competitors(run)
            if run.stop:
                return
            decide(False)
        else:
            if not have and not pending:
                self._job_competitors(run)
                if run.stop:
                    return
            decide(False)
        if not run.stop and not self.accepted_competitors():
            raise EngineError(
                "pilot_stuck",
                "Автопилот не принял ни одного конкурента: добавьте своих или повторите",
            )

    def _accepted_all(self, kind: str) -> list[dict]:
        return [c for c in self.cards(kind) if c["status"] == ACCEPTED]

    def _scenario_pilot(self, run: Run, scenario: dict, label: str) -> None:
        sid = scenario["id"]
        self._stage(run, f"проблемы, сценарий {label}")
        self._step_cards(
            run,
            "проблемы",
            flow.PROBLEM,
            sid,
            "Принимай существенные проблемы, которые стоит автоматизировать и которые в границах системы.",
            lambda: self._job_scenarios(run, {"kind": "problems", "scenarios": [scenario]}),
        )
        if run.stop:
            return
        if not self._accepted(flow.PROBLEM, sid):
            run.notes.append(f"Сценарий «{scenario['title']}»: проблем не принято, пропущен")
            return
        self._stage(run, f"требования к системе, сценарий {label}")
        for problem in self._accepted(flow.PROBLEM, sid):
            self._step_cards(
                run,
                f"требования к проблеме «{problem['title']}»",
                flow.REQUIREMENT,
                problem["id"],
                "Принимай требования «Система должна…», которые по-настоящему устраняют проблему, проверяемы, не называют способ "
                "реализации и лежат в границах системы.",
                lambda problem=problem: self._job_requirements(run, {"problems": [problem]}),
            )
            if run.stop:
                return
        if not self._requirements_of(sid):
            run.notes.append(f"Сценарий «{scenario['title']}»: требований не принято, пропущен")
            return
        self._stage(run, f"варианты решения, сценарий {label}")
        unsolved = self._unsolved_requirements(sid)
        fresh = [r for r in unsolved if not self.cards(flow.VARIANT, r["id"])]
        if fresh:
            self._job_variants(run, {"requirements": fresh})
            if run.stop:
                return
        for requirement in unsolved:
            pending = [
                c for c in self.cards(flow.VARIANT, requirement["id"]) if c["status"] == PROPOSED
            ]
            items = [{"id": c["id"], "title": c["title"], "body": c["body"]} for c in pending]
            accept, _ = self._pilot_decide(
                run,
                f"решение требования «{requirement['title']}»",
                items,
                "Выбери не более ОДНОГО варианта, который лучше всего выполняет требование в границах системы; "
                "если ни один не подходит — оставь accept пустым.",
            )
            if run.stop:
                return
            chosen = next((c for c in pending if c["id"] in accept), None)
            if chosen:
                self.do_choose_variant({"id": chosen["id"]})
            else:
                for c in pending:
                    self.do_remove({"id": c["id"]})
        for requirement in self._unsolved_requirements(sid):
            self.do_remove({"id": requirement["id"]})  # без подходящего решения требование не висит в графе
            run.notes.append(
                f"Требование «{requirement['title']}» убрано: подходящего решения в границах нет"
            )
        for problem in self._accepted(flow.PROBLEM, sid):
            if not self.cards(flow.REQUIREMENT, problem["id"]):
                self.do_remove({"id": problem["id"]})  # проблема без требований не остаётся в графе
                run.notes.append(
                    f"Проблема «{problem['title']}» убрана: требований к ней в границах нет"
                )
        if self._solved(sid) and (run.improve or not self.cards(flow.SOLUTION, sid)):
            self._stage(run, f"диаграмма «решение», сценарий {label}")
            self._job_scenarios(run, {"kind": "solution", "scenarios": [scenario]})
