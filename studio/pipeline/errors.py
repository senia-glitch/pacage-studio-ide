"""Ошибки конвейера. Наследуют ошибку ядра, поэтому оболочка отвечает на них одинаково."""

from __future__ import annotations

from studio.pipeline.graph.errors import GraphError


class PipelineError(GraphError):
    """Действие отклонено правилами конвейера: банк не изменился."""
