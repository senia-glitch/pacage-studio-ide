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
    "W_DIR_UNREADABLE": "каталог не читается и пропущен",
    "W_ENV_INVALID": "некорректная переменная .env.fastbase",
    "W_NO_ROUTES": "пакет роутов не найден",
    "W_NO_SCENARIOS": "пакет сценариев не найден",
    "W_NO_MODELS": "файл моделей не найден",
    "W_EDGE_UNRESOLVED": "связь из метаданных не разрешена",
    "W_EDGE_AMBIGUOUS": "связь из метаданных неоднозначна",
    "W_EDGE_SELF": "связь узла с самим собой",
    "W_DUP_ANCHOR": "одинаковый якорь у двух узлов",
    "W_DISPATCH_DYNAMIC": "run() с нелитеральным именем",
    "W_ROUTE_DYNAMIC": "путь маршрута не литерал",
    "W_IO_DYNAMIC": "SQL или таблица не литерал",
    "W_IO_SAME_LINE": "несколько вызовов порта в одной строке",
    "W_TABLE_UNKNOWN": "таблица из io не найдена среди моделей",
    "W_NO_EXECUTE": "у сценария нет execute",
    "W_IMPORT_UNRESOLVED": "импорт проекта не разрешается",
    "W_METADATA_INVALID": "metadata.json не читается",
    "W_METADATA_ORPHAN": "метаданные без узла в коде",
    "W_METADATA_EDGE": "связь из metadata.json не добавлена",
    "W_PLAN_INVALID": "запись плана не читается",
    "W_PLAN_CONFLICT": "имя или файл плана заняты",
    "W_PLAN_NO_DESCRIPTION": "у плана нет описания",
    "I_EMPTY_PROJECT": "источники не найдены",
}


class Diagnostic(BaseModel):
    level: Level
    code: str
    message: str
    file: str = ""
    line: int = 0
    hint: str = ""
    node_id: str = ""
    edge_id: str = ""

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
        node_id: str = "",
        edge_id: str = "",
    ) -> Diagnostic:
        diag = Diagnostic(
            level=level or code_level(code),
            code=code,
            message=message,
            file=file,
            line=line,
            hint=hint,
            node_id=node_id,
            edge_id=edge_id,
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
                    node_id=diag.node_id,
                    edge_id=diag.edge_id,
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


def mark_node(
    node,
    sink: Sink | None,
    code: str,
    message: str,
    *,
    hint: str = "",
    level: Level | None = None,
) -> None:
    """Жёлтая пометка узла: причина видна и в узле, и в панели диагностики."""
    note = {"code": code, "message": message, "hint": hint}
    notes = getattr(node, "notes", None)
    if notes is None:
        notes = []
        node.notes = notes
    if note not in notes:
        notes.append(note)
    if sink is not None:
        sink.add(
            code,
            message,
            file=getattr(node, "file_path", ""),
            line=getattr(node, "line", 0),
            hint=hint,
            level=level,
            node_id=getattr(node, "id", ""),
        )


def mark_edge(edge, sink: Sink | None, code: str, message: str, *, hint: str = "") -> None:
    """Жёлтая пометка ребра: причина видна на ребре и в диагностике."""
    note = {"code": code, "message": message, "hint": hint}
    notes = getattr(edge, "notes", None)
    if notes is None:
        notes = []
        edge.notes = notes
    if note not in notes:
        notes.append(note)
    if sink is not None:
        sink.add(
            code,
            message,
            file="",
            line=getattr(edge, "line", 0),
            hint=hint,
            edge_id=getattr(edge, "id", ""),
        )


__all__ = [
    "CODES",
    "Diagnostic",
    "Sink",
    "code_level",
    "diagnostic_for_exception",
    "mark_edge",
    "mark_node",
]
