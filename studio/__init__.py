"""pacage-studio — офлайн-карта пути HTTP-запроса по AST.

Строит карту:

    endpoint (fastbase) → scenario (core-package) → io → model (event-infra)

Сканер только читает код. Метаданные узлов (описание, критерии, домен) и связи
хранятся в коде комментариями `# studio: <раздел> start/end`.
"""

__version__ = "3.2.0"
__all__ = ["__version__"]
