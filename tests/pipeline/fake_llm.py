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
