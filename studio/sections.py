"""Выбор разделов документа (ТЗ, отчёт): общая проверка для бэкенда и фронтенда."""

from __future__ import annotations


def pick_sections(sections, allowed: tuple[str, ...], what: str) -> set[str]:
    """Набор разделов: None — все; неизвестное имя — ошибка ввода, а не молчаливый пропуск."""
    if sections is None:
        return set(allowed)
    if isinstance(sections, str) or not isinstance(sections, (list, tuple, set)):
        raise ValueError("Разделы нужно передать списком")
    chosen = {str(item) for item in sections}
    unknown = sorted(chosen - set(allowed))
    if unknown:
        raise ValueError(f"Неизвестные разделы {what}: {', '.join(unknown)}")
    return chosen
