"""Общие типы создания плана сценария.

Старый конструктор узлов удалён: план создаётся одной операцией в
`studio.creator.plan`, а здесь остались только ошибка, результат и список
HTTP-методов, которые используют план и сервер.
"""

from __future__ import annotations

from dataclasses import dataclass

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


class CreateError(Exception):
    """Создание отклонено: понятное пользователю сообщение и машинный код."""

    def __init__(self, message: str, code: str = "create_rejected"):
        self.code = code
        super().__init__(message)


@dataclass
class CreatedFile:
    path: str
    kind: str
    name: str


__all__ = [
    "CreateError",
    "CreatedFile",
    "HTTP_METHODS",
]
