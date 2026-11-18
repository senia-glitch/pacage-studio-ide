"""Поддельный агент для тестов конвейера: отвечает заранее заготовленным JSON по ключевому слову."""

import json

from studio.pipeline.llm import Agent, Agents, Reply


class FakeAgent(Agent):
    name = "claude"
    label = "fake"
    exe = "fake"

    def __init__(self, rules):
        self.rules = rules  # список (ключевое слово в подсказке, ответ: объект или текст)
        self.calls = []

    def found(self):
        return "fake"

    def run(self, system, prompt, *, workdir, model="", web=False, timeout=0):
        self.calls.append({"system": system, "prompt": prompt, "model": model, "web": web})
        for keyword, answer in self.rules:
            if keyword in prompt:
                if callable(answer):
                    answer = answer(prompt)
                text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
                return Reply(text=text)
        return Reply(error=f"нет заготовленного ответа для подсказки: {prompt[:80]!r}")


def agents_with(rules):
    agent = FakeAgent(rules)
    registry = Agents()
    registry.add(agent)
    return registry, agent


ALL_KEYS = {
    key: {"score": 9, "evidence": "см. c1"}
    for key in (
        "actuality", "consistency", "completeness", "distinct", "adequacy", "testability",
        "clarity", "noncontradiction",
    )
}  # fmt: skip


GATE_KEYS_2 = {
    key: {"score": 9, "evidence": "см. c1"}
    for key in ("completeness", "clarity", "noncontradiction", "size", "normalization", "standards",
                "extensibility", "data_rules", "scenario_fit", "states", "accessibility",
                "test_quality", "stack_fit", "permissions", "errors", "security", "idea_fit", "integrity",
                "quality", "documentation")
}  # fmt: skip


def full_rules(evaluator=None):
    """Ответы на все шаги этапов 1–2. ``evaluator`` — функция (подсказка) → оценки."""
    import re

    def first_id(prompt, after):
        tail = prompt.split(after, 1)[1]
        return re.search(r"(c\d+):", tail).group(1)

    def ids(prompt, after, pattern):
        return re.findall(pattern, prompt.split(after, 1)[1])

    def requirements(prompt):
        pid = first_id(prompt, "для которых нужны требования:")
        return {
            "requirements": [
                {
                    "problem": pid,
                    "text": "Система должна записывать пациента на приём.",
                    "priority": "обязательно",
                    "options": True,
                    "criteria": [{"text": "Когда выбрано время, запись создана", "check": "auto"}],
                },
                {
                    "problem": pid,
                    "text": "Система должна напоминать о записи.",
                    "options": False,
                    "criteria": [{"text": "Когда до приёма сутки, пациент получает напоминание"}],
                },
            ]
        }

    def variants(prompt):
        rid = first_id(prompt, "реализации с плюсами и минусами:")
        return {
            "variants": [
                {"requirement": rid, "title": "Онлайн-форма", "body": "просто"},
                {"requirement": rid, "title": "Звонок", "body": "дорого"},
            ]
        }

    def choose(prompt):
        rid = first_id(prompt, "Выбери по одному варианту:")
        vid = re.search(r"\n\s+(c\d+): Онлайн-форма", prompt).group(1)
        return {"choices": [{"requirement": rid, "variant": vid, "why": "проще"}]}

    def scenarios(prompt):
        rids = ids(prompt, "покрыть сценариями:", r"(c\d+): Система")
        vid = re.search(r"(c\d+) \(для", prompt)
        return {
            "scenarios": [
                {
                    "title": "Записаться на приём",
                    "requirements": rids[:1],
                    "variant": vid.group(1) if vid else "",
                    "no_data": False,
                    "diagram": {
                        "participants": ["Пациент", "Система"],
                        "steps": [
                            {
                                "from": "Пациент",
                                "to": "Система",
                                "text": "выбирает время",
                                "tag": "screen",
                            },
                            {
                                "from": "Система",
                                "to": "Запись",
                                "text": "сохраняет запись",
                                "tag": "data",
                            },
                        ],
                    },
                    "criteria": [{"text": "Когда выбрано время, запись создана", "check": "auto"}],
                },
                {
                    "title": "Получить напоминание",
                    "requirements": rids[1:] or rids[:1],
                    "no_data": True,
                    "diagram": {
                        "participants": ["Система", "Пациент"],
                        "steps": [
                            {
                                "from": "Система",
                                "to": "Пациент",
                                "text": "шлёт напоминание",
                                "tag": "call",
                            }
                        ],
                    },
                    "criteria": [],
                },
                {"title": "Сценарий без требования", "requirements": ["c999"], "diagram": {}},
            ]
        }

    def merge(prompt):
        sids = re.findall(r"^(c\d+) Записаться", prompt, re.M)
        return {
            "tables": [
                {
                    "name": "Patient",
                    "columns": [
                        {"name": "id", "type": "int", "key": "pk"},
                        {"name": "name", "type": "text"},
                    ],
                    "from": sids,
                },
                {
                    "name": "appointment",
                    "description": "запись",
                    "columns": [
                        {"name": "id", "type": "int", "key": "pk"},
                        {"name": "patient_id", "type": "int", "key": "fk", "ref": "patient.id"},
                    ],
                    "from": sids,
                },
            ]
        }

    rules = stage1_rules_dynamic(requirements) + [
        (
            "Составь единый словарь имён",
            {
                "terms": [
                    {"name": "Пациент", "type": "role", "text": "записывается", "from": ["c13"]},
                    {"name": "Система", "type": "component", "text": "наш сервис", "from": []},
                    {"name": "Запись", "type": "entity", "text": "приём у врача", "from": []},
                    {"name": "Робот", "type": "weird", "text": "x"},
                ]
            },
        ),
        ("реализации с плюсами и минусами", variants),
        ("Выбери по одному варианту", choose),
        ("покрыть сценариями", scenarios),
        (
            "Спроектируй таблицы, нужные ЭТОМУ сценарию",
            {
                "tables": [
                    {
                        "name": "Appointment",
                        "columns": [{"name": "id", "type": "int", "key": "pk"}],
                    },
                ]
            },
        ),
        ("Слей схемы сценариев", merge),
        ("Замечания ворот к твоей работе", {"revise": [], "reject": []}),
        (
            "не пройдены.\nЗамечания:",
            {
                "summary": "формулировки расплывчаты",
                "items": [
                    {"criterion": "clarity", "cause": "here", "defect": "неясно", "fix": "перепиши"}
                ],
            },
        ),
        ("Ворота «", evaluator or (lambda p: {**ALL_KEYS, **GATE_KEYS_2})),
    ]
    return rules


def interview_rule(prompt):
    """Интервью «Идеи» в тестах: два вопроса о границах, по остальным темам вопросов нет."""
    if "Тема сейчас: «Границы системы»" in prompt:
        if "Кто пользуется системой?" not in prompt:
            return {
                "done": False,
                "question": {"text": "Кто пользуется системой?", "options": ["пациенты", "врачи"]},
            }
        if "Нужна ли оплата?" not in prompt:
            return {
                "done": False,
                "question": {"text": "Нужна ли оплата?", "options": ["нет", "да"]},
            }
    return {"done": True, "why": "всё ясно"}


def interview_questions(pl, topic="questions"):
    """Пройти интервью темы: отвечаем первым вариантом, пока студия не скажет «всё ясно»."""
    from studio.pipeline import flow, interview

    for _ in range(30):
        out = interview.step(pl, topic)
        if out is None:
            return
        for qid in out.get("asked") or out.get("waiting") or []:
            options = pl.bank.get(qid)["attrs"].get("options") or ["да"]
            pl.bank.mark(qid, "human", answer=options[0], answered_by="human")
            flow.on_answer(pl, qid)


def confirm_all(pl):
    """Человек подтверждает всё найденное в сети («Да, верно»)."""
    from studio.pipeline import clarify, flow

    for q in [q for q in clarify.pending(pl) if q["attrs"].get("source") == "confirm"]:
        pl.bank.mark(q["id"], "human", answer=q["attrs"]["options"][0], answered_by="human")
        flow.on_answer(pl, q["id"])


def drive(pl, **kwargs):
    """Автопилот «Идеи» ждёт ответов человека: отвечаем первым вариантом и запускаем дальше."""
    from studio.pipeline import clarify, flow

    log = []
    for _ in range(60):
        part = flow.autopilot(pl, **kwargs)
        waiting = clarify.waiting_human(pl)
        log += [
            e
            for e in part
            if not (e.get("stop") or "").startswith(("нужен ваш ответ", "ворота не пройдены: студия ждёт"))
        ]
        if not waiting:
            return log
        question = waiting[0]
        options = question["attrs"].get("options") or ["да"]
        pl.bank.mark(question["id"], "human", answer=options[0], answered_by="human")
        flow.on_answer(pl, question["id"])
    return log


def stage1_rules_dynamic(requirements):
    return [
        ("Тема сейчас:", interview_rule),
        ("Критерии ворот расплывчаты", {"done": True, "why": "вопросов нет"}),
        (
            "Ответь на вопросы по правилам студии",
            {"answers": [{"id": "c2", "answer": "пациенты"}, {"id": "c3", "answer": "нет"}]},
        ),
        (
            "Сформулируй границы системы",
            {"boundaries": [{"text": "Входит: запись пациента к врачу", "from": ["c2"]}]},
        ),
        (
            "Темы:",
            {
                "constraints": [
                    {
                        "topic": "Доступ по ролям",
                        "text": "Врач видит своих",
                        "criterion": "Когда врач просит чужого, система отказывает",
                    }
                ]
            },
        ),
        ("реальных конкурентов", {"competitors": []}),
        (
            "Опиши предметную область",
            {"facts": [{"type": "role", "name": "Пациент", "text": "записывается"}]},
        ),
        (
            "Найди проблемы пользователей",
            {
                "problems": [
                    {
                        "title": "Не дозвониться в регистратуру",
                        "body": "долго",
                        "importance": "высокая",
                        "from": ["c4"],
                    }
                ],
                "questions": [],
            },
        ),
        ("Для каждой проблемы сформулируй", requirements),
    ]


# ------------------------------------------------------------------ этапы 3–4


class FakeStack:
    """Проверки стека без сканеров: по умолчанию замечаний нет (настоящие — ``stackcheck``)."""

    def __init__(self, back=None, front=None):
        self.back = list(back or [])
        self.front = list(front or [])

    def backend(self, pl):
        return list(self.back)

    def frontend(self, pl):
        return list(self.front)


def fake_stack(pl, back=None, front=None):
    """Подменить проверки стека и генераторы стека в проекте конвейера."""
    pl.stack = FakeStack(back, front)
    pl.stack_init = lambda project: []
    return pl.stack


class FakeCoder:
    """Исполнитель без модели: пишет файл «реализации» и, если просят, портит тест."""

    def __init__(self, tamper=None):
        self.tasks = []
        self.tamper = tamper

    def run(self, role, task, project):
        from studio.pipeline.coder import CodeResult

        self.tasks.append((role, task))
        target = project / "frontend" / "src" / "ui" / "pages" / "Record.vue"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("<template>запись</template>", encoding="utf-8")
        if self.tamper and (project / self.tamper).exists():
            (project / self.tamper).write_text("// исполнитель подогнал тест", encoding="utf-8")
        return CodeResult(summary="сделано")


class FakeTests:
    """Запуск тестов по сценарию: на слой — очередь результатов, последний повторяется."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.runs = []

    def run(self, layer):
        self.runs.append(layer)
        queue = self.script.get(layer, [])
        return queue.pop(0) if len(queue) > 1 else queue[0]


def red_then_green(failed_files=()):
    from studio.pipeline.coder import TestResult

    return [
        TestResult(True, False, list(failed_files), "красный старт"),
        TestResult(True, True, [], "всё зелёное"),
    ]


def rules_stage34(extra=()):
    import re

    def ids(prompt, after, pattern):
        return re.findall(pattern, prompt.split(after, 1)[1])

    def pages(prompt):
        first = ids(prompt, "вызов системы):", r"(c\d+): Записаться")[0]
        second = ids(prompt, "вызов системы):", r"(c\d+): Получить")
        out = [
            {
                "title": "Запись",
                "route": "/appointment",
                "scenarios": [first],
                "description": "форма записи",
                "calls": [{"title": "создать запись"}],
            },
            {"title": "Плохая", "scenarios": ["c999"]},
        ]
        if second:
            out.append({"title": "Напоминания", "scenarios": [second[0]], "calls": []})
        return {"pages": out}

    def contract(prompt):
        cid = ids(prompt, "Вызовы без контракта:", r"(c\d+): создать запись")[0]
        return {
            "calls": [
                {
                    "id": cid,
                    "method": "post",
                    "path": "/api/appointments",
                    "request": {"doctor_id": 1},
                    "response": {"id": 1},
                },
                {"id": "c999", "method": "GET", "path": "/x"},
                {"id": cid, "method": "FLY", "path": "/y"},
            ]
        }

    def backend(prompt):
        cid = ids(prompt, "для которых нужны эндпоинты:", r"(c\d+): создать запись")[0]
        return {
            "endpoints": [
                {"call": cid, "summary": "создаёт запись", "tables": ["appointment"]},
                {"call": "c999", "summary": "лишний"},
            ]
        }

    def tests(layer):
        def build(prompt):
            cids = re.findall(r"^(c\d+): ", prompt.split("нужны тесты:", 1)[1], re.M)
            base = re.search(r"Файлы — в ([\w/]+/)", prompt).group(1)
            ext = ".test.ts" if layer == "frontend" else ".py"
            out = [
                {
                    "criterion": c,
                    "title": f"тест {c}",
                    "file": f"{base}t_{c}{ext}",
                    "code": f"// тест критерия {c}",
                }
                for c in cids
            ]
            out.append({"criterion": cids[0], "title": "в код", "file": "app/evil.py", "code": "x"})
            return {"tests": out}

        return build

    return [
        (
            "Предложи стиль и каркас интерфейса",
            {
                "theme": "dark",
                "notes": "строго",
                "navigation": [{"role": "Пациент", "items": ["Запись"]}],
            },
        ),
        ("Спроектируй страницы интерфейса", pages),
        ("Для каждого вызова опиши контракт API", contract),
        ("Спланируй бэкенд", backend),
        ("Напиши тесты (Vitest", tests("frontend")),
        ("Напиши тесты (pytest", tests("backend")),
        *extra,
    ]


class FakeLauncher:
    def __init__(self):
        self.started = 0

    def start(self, project):
        self.started += 1
        return {"running": True, "note": "фейковый запуск"}


def rules_stage5():
    import re

    def reconstruct(prompt):
        endpoint = re.search(r"(c\d+) \[endpoint\]", prompt)
        table = re.search(r"(c\d+) \[table\]", prompt)
        return {
            "features": [
                {
                    "problem": "Нужно видеть состояние сервиса",
                    "requirement": "Система должна отвечать на проверку",
                    "scenario": "Проверить здоровье сервиса",
                    "endpoints": [endpoint.group(1)] if endpoint else [],
                    "tables": [table.group(1)] if table else [],
                    "pages": [],
                },
                {"problem": "", "requirement": "x", "scenario": "пусто"},
            ]
        }

    return [
        (
            "Придумай небольшие правдоподобные демо-данные",
            {"rows": {"patient": [{"id": 1, "name": "Анна"}], "ghost": [{"id": 1}]}},
        ),
        ("Восстанови замысел", reconstruct),
    ]
