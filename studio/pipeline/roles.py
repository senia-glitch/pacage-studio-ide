"""Роли агентов: кто что делает в конвейере и какая «крутость» модели рекомендуется.

Модель каждой роли пользователь выбирает в Настройках студии. Рекомендация — подсказка, а не
ограничение. Поддерживаются агенты Claude Code Pro и OpenCode Go Plus.
"""

from __future__ import annotations

from dataclasses import dataclass

from studio.agent.adapters import AGENTS

AGENT_LABELS = {"claude": "Claude Code Pro", "opencode": "OpenCode Go Plus"}
TIER_TOP, TIER_STRONG, TIER_FAST = 3, 2, 1
TIER_NAMES = {
    TIER_TOP: "топ — самая сильная",
    TIER_STRONG: "сильная — рабочая",
    TIER_FAST: "быстрая — дешёвая",
}


@dataclass(frozen=True)
class Role:
    key: str
    title: str
    where: str  # «этап 1», «слой 2 всех ворот»
    description: str
    tier: int  # рекомендуемая «крутость» модели
    why: str
    stages: tuple[int, ...] = ()  # в каких этапах участвует (пусто — во всех)


_ROLES = (
    Role(
        "coordinator",
        "Координатор",
        "все этапы",
        "ведёт этап по режиму участия, раздаёт задачи, считает круги, маршрутизирует запросы",
        TIER_TOP,
        "нужна надёжность рассуждений",
    ),
    Role(
        "evaluator",
        "Независимый оценщик",
        "слой 2 всех ворот",
        "оценивает по критериям ворот с доказательствами; всегда другая модель, чем автор",
        TIER_TOP,
        "строгий и непредвзятый",
    ),
    Role(
        "analyst",
        "Аналитик",
        "этап 1",
        "вопросы, границы, проблемы, функциональные требования и критерии приёмки",
        TIER_TOP,
        "качество формулировок",
        (1,),
    ),
    Role(
        "researcher",
        "Исследователь",
        "этап 1",
        "конкуренты и предметная область; несколько штук параллельно, нужен веб-поиск",
        TIER_STRONG,
        "факты проверяются потом",
        (1,),
    ),
    Role(
        "scenarist",
        "Аналитик сценариев",
        "этап 2",
        "словарь, варианты, сценарии, диаграммы и критерии сценария",
        TIER_TOP,
        "ошибка расходится на три этапа",
        (2,),
    ),
    Role(
        "db_designer",
        "Проектировщик БД",
        "этап 2",
        "схема БД на сценарий и слияние в общую схему",
        TIER_TOP,
        "согласованность и стандарты",
        (2,),
    ),
    Role(
        "ui_designer",
        "Дизайнер интерфейса",
        "этап 3",
        "стиль и каркас, план страниц, моки и контракт API",
        TIER_STRONG,
        "вкус и аккуратность",
        (3,),
    ),
    Role(
        "backend_designer",
        "Проектировщик бэкенда",
        "этап 4",
        "план эндпоинтов, замена моков на реальный API",
        TIER_STRONG,
        "хватает сильной модели",
        (4,),
    ),
    Role(
        "test_author",
        "Автор тестов",
        "этапы 3–4",
        "тесты из критериев до реализации; кода не видит; не тот, кто исполнитель",
        TIER_STRONG,
        "точность важнее скорости",
        (3, 4),
    ),
    Role(
        "executor",
        "Исполнитель",
        "этапы 3–4",
        "пишет код по ТЗ; тесты не правит; параллельно по сценариям",
        TIER_TOP,
        "главная роль по качеству кода",
        (3, 4),
    ),
    Role(
        "assistant",
        "Помощник",
        "этап 5 и итоги",
        "демо-данные по сценариям, Документация.md, отчёт",
        TIER_FAST,
        "текст и рутина",
        (5,),
    ),
)

ROLES: dict[str, Role] = {role.key: role for role in _ROLES}

# Кто «автор» для независимого оценщика на воротах каждого этапа.
AUTHORS_BY_STAGE = {
    1: ("analyst", "researcher"),
    2: ("scenarist", "db_designer"),
    3: ("ui_designer", "executor", "test_author"),
    4: ("backend_designer", "executor", "test_author"),
    5: ("assistant",),
}


def role(key: str) -> Role:
    from .errors import PipelineError

    try:
        return ROLES[key]
    except KeyError:
        raise PipelineError("unknown_role", f"Такой роли нет: {key!r}") from None


def clean_agent(agent: str) -> str:
    """Допустимый агент или пустая строка («по умолчанию»)."""
    from .errors import PipelineError

    agent = (agent or "").strip().lower()
    if agent and agent not in AGENTS:
        raise PipelineError(
            "unknown_agent", "Поддерживаются агенты: " + ", ".join(AGENT_LABELS.values())
        )
    return agent
