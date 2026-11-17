"""Каталог ворот: что проверяет код, какие критерии у независимой оценки по умолчанию.

Ворота одинаковы на всех этапах и состоят из трёх слоёв (схема v3, пункт 1):
1. проверки кодом — точные, обязательные, обойти нельзя;
2. независимая оценка — другая модель оценивает КАЖДЫЙ критерий от 0 до 10, нужно не меньше 8
   по каждому (не среднее) и с доказательством; список критериев правится в Настройках студии;
3. ваше решение — принять, вернуть с замечанием или принять ниже порога (с пометкой).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import coverage as cov
from .bank import ProjectBank
from .coverage import Finding
from .errors import PipelineError

DEFAULT_THRESHOLD = 8


@dataclass(frozen=True)
class CriterionDef:
    key: str
    title: str
    hint: str  # что значит высокая оценка


@dataclass(frozen=True)
class GateDef:
    key: str
    stage: int
    title: str
    subject: tuple[str, ...]  # виды записей, которые ворота переводят в «принято»
    checks: tuple[str, ...]  # имена проверок кодом (см. CHECKS)
    criteria: tuple[CriterionDef, ...]
    facts: tuple[str, ...] = ()  # внешние факты: тесты прошли, моки убраны и т. п.
    final: bool = False


def _c(key: str, title: str, hint: str) -> CriterionDef:
    return CriterionDef(key, title, hint)


GATES: dict[str, GateDef] = {
    gate.key: gate
    for gate in (
        GateDef(
            "problems",
            1,
            "Проверка проблем",
            ("area", "question", "boundary", "constraint", "competitor", "domain", "problem"),
            ("orphans_1", "duplicates_1"),
            (
                _c("actuality", "Актуальность", "проблемы реальные и важные для этой системы"),
                _c(
                    "consistency",
                    "Согласованность с границами",
                    "проблемы не выходят за границы системы",
                ),
                _c("completeness", "Полнота", "учтены предметная область и конкуренты"),
                _c("distinct", "Различимость", "нет дублей и размытых формулировок"),
            ),
        ),
        GateDef(
            "requirements",
            1,
            "Приёмка требований",
            ("requirement", "criterion"),
            ("uncovered_problem", "uncovered_criterion", "orphans_req", "duplicates_req"),
            (
                _c(
                    "adequacy",
                    "Адекватность",
                    "требования решают проблемы и укладываются в границы и ограничения",
                ),
                _c(
                    "testability", "Проверяемость", "у каждого требования есть наблюдаемый критерий"
                ),
                _c("clarity", "Однозначность", "требование понимается одним способом"),
                _c("noncontradiction", "Непротиворечивость", "требования не спорят друг с другом"),
            ),
        ),
        GateDef(
            "scenarios",
            2,
            "Приёмка сценариев",
            ("term", "variant", "scenario", "criterion"),
            ("uncovered_scenario", "orphans_2a", "duplicates_2a", "variant_choice", "participants"),
            (
                _c("completeness", "Полнота", "основной ход и ошибки, исключения учтены"),
                _c("clarity", "Ясность", "сценарий понятен и однозначен"),
                _c("noncontradiction", "Непротиворечивость", "сценарии не противоречат друг другу"),
                _c("size", "Размер", "один сценарий — одна цель"),
            ),
        ),
        GateDef(
            "schema",
            2,
            "Приёмка схемы БД",
            ("table",),
            ("uncovered_table", "orphans_2b", "duplicates_2b", "schema_integrity", "stale_2"),
            (
                _c("normalization", "Нормализация", "данные не дублируются, связи верные"),
                _c(
                    "standards",
                    "Соответствие стандартам индустрии",
                    "ключи, имена, типы по общим правилам",
                ),
                _c("extensibility", "Расширяемость", "схема выдержит ближайшие изменения"),
                _c(
                    "data_rules", "Данные и хранение", "персональные данные и сроки хранения учтены"
                ),
            ),
        ),
        GateDef(
            "frontend",
            3,
            "Приёмка интерфейса",
            ("style", "page", "call", "test"),
            ("uncovered_page", "uncovered_test", "orphans_3", "stale_3", "facts"),
            (
                _c(
                    "scenario_fit",
                    "Соответствие сценариям",
                    "страницы делают то, что описано в сценариях",
                ),
                _c("states", "Состояния", "учтены пусто, загрузка и ошибка"),
                _c(
                    "accessibility",
                    "Доступность и языки",
                    "читаемо, управляется с клавиатуры, языки по ограничениям",
                ),
                _c(
                    "test_quality",
                    "Качество тестов",
                    "тесты проверяют границы и ошибки, а не только «счастливый путь»",
                ),
            ),
            facts=("tests_green",),
        ),
        GateDef(
            "backend",
            4,
            "Приёмка бэкенда",
            ("endpoint", "model", "test"),
            ("uncovered_endpoint", "uncovered_test_backend", "orphans_4", "stale_4", "facts"),
            (
                _c(
                    "permissions",
                    "Права по ролям",
                    "роль без права получает отказ, проверено тестами",
                ),
                _c("errors", "Обработка ошибок", "ошибки ожидаемы и понятны"),
                _c("security", "Безопасность", "нет секретов в коде, входные данные проверяются"),
                _c("test_quality", "Качество тестов", "тесты проверяют границы и ошибки"),
            ),
            facts=("tests_green", "contract_matches", "mocks_removed"),
        ),
        GateDef(
            "final",
            5,
            "Итоговая приёмка",
            ("finding", "release", "test"),
            ("broken_chains", "stale_all", "orphans_all", "facts"),
            (
                _c("idea_fit", "Соответствие идее", "система решает исходные проблемы"),
                _c("integrity", "Целостность", "части системы согласованы между собой"),
                _c("quality", "Качество", "код и документация в порядке"),
                _c("security", "Безопасность", "нет очевидных уязвимостей"),
                _c("documentation", "Документация", "Документация.md и карты актуальны"),
            ),
            facts=("e2e_green", "tests_green", "load_ok"),
            final=True,
        ),
    )
}

STAGE_GATES = {
    stage: tuple(g.key for g in GATES.values() if g.stage == stage) for stage in range(1, 6)
}

_ALL_KINDS = (
    "area",
    "question",
    "boundary",
    "constraint",
    "competitor",
    "domain",
    "problem",
    "requirement",
    "criterion",
    "term",
    "variant",
    "scenario",
    "table",
    "style",
    "page",
    "call",
    "test",
    "endpoint",
    "model",
)

CHECKS: dict[str, Callable[[ProjectBank, dict], list[Finding]]] = {
    "orphans_1": lambda b, f: cov.orphans(b, ("problem",)),
    "duplicates_1": lambda b, f: cov.duplicates(b, ("problem", "boundary", "constraint")),
    "uncovered_problem": lambda b, f: cov.uncovered(b, "problem", "requirement"),
    "uncovered_criterion": lambda b, f: cov.uncovered(b, "requirement", "criterion"),
    "orphans_req": lambda b, f: cov.orphans(b, ("requirement", "criterion")),
    "duplicates_req": lambda b, f: cov.duplicates(b, ("requirement",)),
    "uncovered_scenario": lambda b, f: cov.uncovered(b, "requirement", "scenario"),
    "orphans_2a": lambda b, f: cov.orphans(b, ("term", "variant", "scenario")),
    "duplicates_2a": lambda b, f: cov.duplicates(b, ("term", "scenario")),
    "variant_choice": lambda b, f: cov.variant_choice(b),
    "participants": lambda b, f: cov.unknown_participants(b),
    "uncovered_table": lambda b, f: cov.uncovered(b, "scenario", "table", skip=cov.no_data),
    "orphans_2b": lambda b, f: cov.orphans(b, ("table",)),
    "schema_integrity": lambda b, f: cov.schema_integrity(b),
    "duplicates_2b": lambda b, f: cov.duplicates(b, ("table",)),
    "stale_2": lambda b, f: cov.stale_in(b, ("term", "variant", "scenario", "table")),
    "uncovered_page": lambda b, f: cov.uncovered(b, "scenario", "page"),
    "uncovered_test": lambda b, f: cov.uncovered(
        b, "criterion", "test", skip=lambda c: cov.visual(c) or cov.layer_of(c) != "frontend"
    ),
    "uncovered_test_backend": lambda b, f: cov.uncovered(
        b, "criterion", "test", skip=lambda c: cov.visual(c) or cov.layer_of(c) != "backend"
    ),
    "orphans_3": lambda b, f: cov.orphans(b, ("page", "call", "test")),
    "stale_3": lambda b, f: cov.stale_in(b, ("style", "page", "call", "test")),
    "uncovered_endpoint": lambda b, f: cov.uncovered(b, "call", "endpoint"),
    "orphans_4": lambda b, f: cov.orphans(b, ("endpoint", "model")),
    "stale_4": lambda b, f: cov.stale_in(b, ("endpoint", "model")),
    "broken_chains": lambda b, f: cov.broken_chains(b),
    "stale_all": lambda b, f: cov.stale_in(b, _ALL_KINDS),
    "orphans_all": lambda b, f: cov.orphans(b, _ALL_KINDS),
}

FACT_TEXT = {
    "tests_green": "все тесты этапа зелёные",
    "contract_matches": "контракт совпал с эндпоинтами",
    "mocks_removed": "моки убраны из фронтенда",
    "e2e_green": "сквозные сценарии зелёные на реальном API",
    "load_ok": "нагрузка из ограничений в норме",
}


def gate(key: str) -> GateDef:
    try:
        return GATES[key]
    except KeyError:
        raise PipelineError("unknown_gate", f"Таких ворот нет: {key!r}") from None


def run_code_layer(bank: ProjectBank, gate_key: str, facts: dict | None = None) -> list[Finding]:
    """Слой 1: проверки кодом. Пустой список — слой пройден."""
    spec = gate(gate_key)
    facts = facts or {}
    found: list[Finding] = []
    for name in spec.checks:
        if name == "facts":
            for key in spec.facts:
                found += cov.fact(facts, key, FACT_TEXT[key])
        else:
            found += CHECKS[name](bank, facts)
    return found
