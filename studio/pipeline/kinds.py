"""Виды записей банка знаний и правила цепочки «происходит из» (схема v3, пункт 2).

У каждой записи при создании указан родитель: проблема — из источника, требование — из проблемы,
сценарий — из требования и так до теста. Цепочка нужна, чтобы на воротах проверять покрытие,
в запросе на изменение считать затронутое, а на карте объяснять, зачем запись существует.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import PipelineError

SOURCE = "происходит из"  # связь «потомок → родитель»

STAGE_NAMES = {
    1: "Идея",
    2: "Сценарии и схема БД",
    3: "Frontend",
    4: "Backend",
    5: "Анализ",
}


@dataclass(frozen=True)
class Kind:
    """Вид записи: этап-хозяин раздела, возможные родители и обязателен ли родитель."""

    key: str
    title: str
    stage: int
    parents: tuple[str, ...] = ()
    required: bool = False
    constitution: bool = False  # «Конституция»: входит в каждое ТЗ


_KINDS = (
    # ---- этап 1 «Идея»
    Kind("area", "Область", 1),
    Kind("question", "Вопрос", 1, ("area", "boundary", "problem")),
    Kind("boundary", "Граница", 1, ("question", "area"), constitution=True),
    Kind("constraint", "Ограничение", 1, ("boundary", "area", "question"), constitution=True),
    Kind("competitor", "Конкурент", 1, ("area",)),
    Kind("domain", "Предметная область", 1, ("area",)),
    Kind("problem", "Проблема", 1, ("boundary", "domain", "competitor"), required=True),
    Kind("requirement", "Функциональное требование", 1, ("problem",), required=True),
    Kind("criterion", "Критерий приёмки", 1, ("requirement", "constraint", "scenario"), True),
    # ---- этап 2 «Сценарии и схема БД»
    Kind("term", "Слово словаря", 2, ("domain", "requirement", "boundary"), True, True),
    Kind("variant", "Вариант решения", 2, ("requirement",), required=True),
    Kind("scenario", "Сценарий", 2, ("requirement", "variant"), required=True),
    Kind("table", "Таблица БД", 2, ("scenario",), required=True),
    # ---- этап 3 «Frontend на моках»
    Kind("style", "Стиль и каркас", 3, constitution=True),
    Kind("page", "Страница", 3, ("scenario",), required=True),
    Kind("call", "Вызов API", 3, ("page",), required=True),
    Kind("test", "Тест", 3, ("criterion",), required=True),
    # ---- этап 4 «Backend»
    Kind("endpoint", "Эндпоинт", 4, ("call",), required=True),
    Kind("model", "Модель", 4, ("table",), required=True),
    # ---- этап 5 «Общий анализ и правки»
    Kind("finding", "Замечание", 5),
    Kind("release", "Итог итерации", 5),
)

KINDS: dict[str, Kind] = {kind.key: kind for kind in _KINDS}
CONSTITUTION = tuple(kind.key for kind in _KINDS if kind.constitution)


def kind_of(key: str) -> Kind:
    try:
        return KINDS[key]
    except KeyError:
        raise PipelineError("unknown_kind", f"Такого вида записи нет: {key!r}") from None


def stage_of(key: str) -> int:
    return kind_of(key).stage


def kinds_of_stage(stage: int) -> tuple[str, ...]:
    return tuple(kind.key for kind in _KINDS if kind.stage == stage)


def title_of(key: str) -> str:
    return kind_of(key).title
