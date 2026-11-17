"""Ошибки движка: у каждой есть короткий код, по нему интерфейс и агент понимают, что делать."""

from __future__ import annotations


class EngineError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict:
        return {"error": self.code, "message": self.message}
