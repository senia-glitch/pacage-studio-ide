"""Ошибки ядра. У каждой есть короткий код: по нему оболочка и агент понимают, что делать."""

from __future__ import annotations


class GraphError(Exception):
    """Операция отклонена: граф не изменился."""

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict:
        return {"error": self.code, "message": self.message, **self.details}


class ConflictError(GraphError):
    """Агент правит то, что человек изменил после последнего чтения графа агентом."""

    def __init__(self, ids: list[str], revision: int):
        super().__init__(
            "human_changed",
            "Человек изменил эти объекты после твоего последнего чтения графа: "
            + ", ".join(ids)
            + ". Правка человека главнее: перечитай граф и реши заново.",
            ids=ids,
            revision=revision,
        )
