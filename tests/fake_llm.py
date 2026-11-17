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
                "extensibility", "data_rules")
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
        ("Ворота «", evaluator or (lambda p: {**ALL_KEYS, **GATE_KEYS_2})),
    ]
    return rules


def stage1_rules_dynamic(requirements):
    return [
        (
            "Задай до",
            {
                "questions": [
                    {"text": "Кто пользуется системой?", "options": ["пациенты", "врачи"]},
                    {"text": "Нужна ли оплата?", "options": ["да", "нет"]},
                ]
            },
        ),
        (
            "Ответь на вопросы наиболее разумным допущением",
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
