"""pacage-studio — офлайн-карта пути HTTP-запроса по AST.

Строит карту:

    endpoint (fastbase) → scenario (core-package) → io → model (event-infra)

Сканер только читает код. Метаданные узлов (описание, критерии, домен, связи,
планы) хранятся в `.studio/metadata.json`; код проекта не изменяется.
"""

import sys

MIN_PYTHON = (3, 12)


def require_python(version_info=None) -> None:
    """Старый Python: понятное сообщение вместо трассировки (этот файл совместим с 3.8+)."""
    current = tuple(version_info if version_info is not None else sys.version_info)[:3]
    if current[:2] >= MIN_PYTHON:
        return
    need = ".".join(str(part) for part in MIN_PYTHON)
    have = ".".join(str(part) for part in current)
    raise SystemExit(
        f"pacage-studio требует Python {need} или новее, а запущен Python {have}.\n"
        f"Установите Python {need}+ и поставьте пакет заново в его окружении: "
        'python -m pip install "pacage-studio @ '
        'git+https://github.com/senia-glitch/pacage-studio-ide.git"'
    )


require_python()

__version__ = "7.4.1"
__all__ = ["MIN_PYTHON", "__version__", "require_python"]
