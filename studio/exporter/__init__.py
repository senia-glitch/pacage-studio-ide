"""Устаревшее имя экспорта: документ теперь собирает studio.agent (WP11).

`build_document` оставлен для совместимости и возвращает ТЗ в режиме
«вся карта» — то же, что `studio brief --map full`.
"""

from __future__ import annotations

from studio.agent.brief import BriefOptions, build_brief
from studio.metamodel import Graph
from studio.source import SourceSet

EXPORT_FILENAME = "STUDIO_METADATA.md"


def build_document(graph: Graph, sources: SourceSet | None = None) -> str:
    brief = build_brief(graph, sources, BriefOptions(map_mode="full"))
    return brief.markdown


__all__ = ["EXPORT_FILENAME", "build_document"]
