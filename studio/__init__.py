"""Studio v4 — карта проекта для AI-разработки (fastbase / core-package / event-infra).

Строит граф одного запроса:

    HTTP → endpoint (fastbase) → scenario (core) → _db.<method> (io) → model (event-infra)

Сканирование (AST), plan-патчи, scaffold, export. Изменяющие операции пишут
только `.studio/*` и файлы-скелеты с маркером.
"""

__version__ = "2.3.0"
__all__ = ["__version__"]
