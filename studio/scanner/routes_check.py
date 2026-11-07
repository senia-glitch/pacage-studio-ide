"""Перекрытие маршрутов: статичный путь, объявленный позже параметрического, недостижим.

FastAPI сопоставляет запрос с маршрутами в порядке регистрации. Модули пакета роутов
подключаются по алфавиту, внутри файла — по порядку строк. Поэтому ``GET /leads/export`` из
``export_leads.py`` встанет раньше ``GET /leads/{id}`` из ``leads.py`` и будет работать, а в
файле, который идёт по алфавиту позже, его перехватит ``{id}`` (и ответит ошибкой 422): код при
этом выглядит правильно. Проверка находит такие пары заранее.
"""

from __future__ import annotations

from collections import defaultdict

from studio.diagnostics import Sink, mark_node
from studio.metamodel import GraphBuilder

NUMERIC_CONVERTERS = frozenset({"int", "float"})


def _segments(path: str) -> list[str]:
    text = str(path or "").strip("/")
    return text.split("/") if text else []


def _is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def _converter(segment: str) -> str:
    return segment[1:-1].partition(":")[2]


def _digits(segment: str) -> bool:
    return segment.replace(".", "", 1).lstrip("-").isdigit()


def shadows(early: str, late: str) -> bool:
    """Перехватывает ли ``early`` все запросы, которые должны попасть в ``late``."""
    left, right = _segments(early), _segments(late)
    if len(left) != len(right) or left == right:
        return False
    for a, b in zip(left, right, strict=True):
        if a == b:
            continue
        if not _is_param(a):
            return False
        if _converter(a) in NUMERIC_CONVERTERS and not _is_param(b) and not _digits(b):
            return False
    return True


def shadow_notes(
    method: str, path: str, file: str, existing: list[tuple[str, str, str]]
) -> list[str]:
    """Предупреждения для нового маршрута до того, как он написан.

    ``existing`` — ``(метод, путь, файл)`` уже известных маршрутов. Новый маршрут окажется в
    ``file``; порядок подключения — по алфавиту файлов (см. модульную документацию).
    """
    notes = []
    for other_method, other_path, other_file in existing:
        if other_method.upper() != method.upper() or other_file == file:
            continue
        if other_file < file and shadows(other_path, path):
            notes.append(
                f"{method.upper()} {path} будет перекрыт: {other_file} подключается раньше "
                f"и содержит {other_method.upper()} {other_path}"
            )
        elif file < other_file and shadows(path, other_path):
            notes.append(
                f"{method.upper()} {path} из {file} перекроет существующий "
                f"{other_method.upper()} {other_path} ({other_file})"
            )
    return notes


def _position(node, module_of, sites) -> tuple:
    """Место маршрута в порядке регистрации: цепочка ``include_router`` от корня к самому маршруту.

    Роутер, подключённый через ``include_router`` в середине другого файла, регистрируется там,
    где стоит этот вызов, а не по алфавиту своего файла.
    """
    chain = [(node.file_path, node.line)]
    module = module_of.get(node.file_path, "")
    for _ in range(5):
        site = sites.get(module)
        if site is None:
            break
        file, line, parent = site
        chain.insert(0, (file, line))
        module = parent
    return tuple(chain)


def add_route_shadow_diagnostics(
    builder: GraphBuilder, sink: Sink, sites: dict | None = None, module_of: dict | None = None
) -> None:
    sites = sites or {}
    module_of = module_of or {}
    by_method: dict[str, list] = defaultdict(list)
    for node in builder._nodes.values():
        details = node.details or {}
        if node.kind != "endpoint" or node.planned:
            continue
        method = str(details.get("http_method") or "").upper()
        path = str(details.get("url_path") or details.get("path") or "")
        if method and path:
            by_method[method].append((node, path))
    for method, items in sorted(by_method.items()):
        ordered = sorted(items, key=lambda item: _position(item[0], module_of, sites))
        for index, (late, late_path) in enumerate(ordered):
            for early, early_path in ordered[:index]:
                if shadows(early_path, late_path):
                    mark_node(
                        late,
                        sink,
                        "W_ROUTE_SHADOW",
                        f"{method} {late_path} недостижим: раньше зарегистрирован "
                        f"{method} {early_path} ({early.file_path}:{early.line})",
                        hint="Маршруты подключаются по алфавиту файлов: переименуйте файл так, "
                        "чтобы статичный путь шёл раньше, перенесите его выше в том же файле или "
                        "подключите его роутер через include_router до параметрического маршрута.",
                    )
                    break


__all__ = ["add_route_shadow_diagnostics", "shadow_notes", "shadows"]
