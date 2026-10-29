"""pacage-studio — офлайн-карта пути HTTP-запроса по AST.

Строит карту:

    endpoint (fastbase) → scenario (core-package) → io → model (event-infra)

Сканер только читает код. Метаданные узлов (описание, критерии, домен, связи,
планы) хранятся в `.studio/metadata.json`; код проекта не изменяется.
"""

__version__ = "5.1.1"
__all__ = ["__version__"]