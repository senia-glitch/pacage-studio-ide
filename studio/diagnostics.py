"""Диагностики скана: единый формат и список кодов.

Диагностика никогда не бросает исключение заменой данных: она объясняет
пользователю, что именно не поддержано или сломано, и что с этим делать.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Level = Literal["error", "warning", "info"]

CODES: dict[str, str] = {
    "E_ENCODING": "файл не в UTF-8",
    "E_SYNTAX": "файл не разбирается как Python",
    "E_INTERNAL": "внутренний сбой фазы скана",
    "W_FILE_TOO_BIG": "файл больше лимита и пропущен",
    "W_ENV_INVALID": "некорректная переменная .env.fastbase",
    "W_NO_ROUTES": "пакет роутов не найден",
    "W_NO_SCENARIOS": "пакет сценариев не найден",
    "W_NO_MODELS": "файл моделей не найден",
    "W_UNCLOSED_BLOCK": "блок метаданных не закрыт",
    "W_NESTED_BLOCK": "вложенный блок метаданных",
    "W_ORPHAN_BLOCK": "блок метаданных без якоря",
    "W_EDGE_UNRESOLVED": "связь из метаданных не разрешена",
    "W_EDGE_AMBIGUOUS": "связь из метаданных неоднозначна",
    "W_EDGE_SELF": "связь узла с самим собой",
    "W_DUP_ANCHOR": "одинаковый якорь у двух узлов",
    "W_DISPATCH_DYNAMIC": "run() с нелитеральным именем",
    "W_ROUTE_DYNAMIC": "путь маршрута не литерал",
    "W_IO_DYNAMIC": "SQL или таблица не литерал",
    "W_IO_SAME_LINE": "несколько вызовов порта в одной строке",
    "W_TABLE_UNKNOWN": "таблица из io не найдена среди моделей",
    "I_EMPTY_PROJECT": "источники не найдены",
}


class Diagnostic(BaseModel):
    level: Level
    code: str
    message: str
    file: str = ""
    line: int = 0
    hint: str = ""

    def key(self) -> tuple[str, str, int, str]:
        return (self.code, self.file, self.line, self.message)


def code_level(code: str) -> Level:
    if code.startswith("E_"):
        return "error"
    if code.startswith("I_"):
        return "info"
    return "warning"


class Sink:
    """Накопитель диагностик за один скан (без глобального состояния)."""

    def __init__(self) -> None:
        self.items: list[Diagnostic] = []
        self._seen: set[tuple[str, str, int, str]] = set()

    def add(
        self,
        code: str,
        message: str,
        *,
        file: str = "",
        line: int = 0,
        hint: str = "",
        level: Level | None = None,
    ) -> Diagnostic:
        diag = Diagnostic(
            level=level or code_level(code),
            code=code,
            message=message,
            file=file,
            line=line,
            hint=hint,
        )
        if diag.key() not in self._seen:
            self._seen.add(diag.key())
            self.items.append(diag)
        return diag

    def extend(self, diagnostics) -> None:
        for diag in diagnostics or ():
            if isinstance(diag, Diagnostic):
                self.add(
                    diag.code,
                    diag.message,
                    file=diag.file,
                    line=diag.line,
                    hint=diag.hint,
                    level=diag.level,
                )

    def diagnostics(self) -> list[Diagnostic]:
        return list(self.items)


def diagnostic_for_exception(phase: str, exc: BaseException) -> Diagnostic:
    return Diagnostic(
        level="error",
        code="E_INTERNAL",
        message=f"Сбой фазы «{phase}»: {type(exc).__name__}: {exc}",
        hint="Сообщите об ошибке и приложите studio.log; карта построена частично.",
    )


__all__ = [
    "CODES",
    "Diagnostic",
    "Sink",
    "code_level",
    "diagnostic_for_exception",
]
