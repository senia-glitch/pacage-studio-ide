"""Studio v2 — визуализатор пути сценария в проекте на fastbase / core-package / event-infra.

Строит граф одного запроса:

    HTTP → endpoint (fastbase) → scenario (core) → _db.<method> (io) → model (event-infra)

Только чтение: студия сканирует код (AST) и показывает его, ничего не записывая
в проект — кроме служебных `.studio/graph.json` и `.studio/studio.log`.
"""

__version__ = "2.1.0"
__all__ = ["__version__"]
