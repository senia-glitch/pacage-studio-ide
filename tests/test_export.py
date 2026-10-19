"""Что проверяется: экспорт графа в markdown и JSON.

Описание словами: to_markdown не падает на любом графе (включая узлы плана
без label/деталей — прежний крах из-за Node(kind="")), содержит разделы
«Реализовано»/«План»/«На удаление», to_json отдаёт валидный JSON той же схемы.
"""

import json

from studio.exporter import to_json, to_markdown
from studio.metamodel import Edge, Graph, Node
from studio.scanner import scan


class TestMarkdown:
    def test_демо_граф_экспортируется(self, demo):
        graph = scan(demo)
        text = to_markdown(graph)
        assert "## 1. Уже реализовано" in text
        assert "## 2. План (ещё не реализовано)" in text
        assert "## 3. На удаление" in text
        assert "## 4. Инструкции для AI-агента" in text
        assert "CreateOrderScenario" in text

    def test_url_path_в_экспорте(self, demo):
        """P1-1: полный URL endpoint'а виден в markdown-экспорте."""
        graph = scan(demo)
        text = to_markdown(graph)
        assert "URL: /api/v1/orders/create" in text

    def test_узел_плана_без_label_не_роняет(self, demo):
        graph = scan(demo)
        graph.nodes.append(
            Node(
                id="scenario:app/scenarios/x.py:Ghost",
                kind="scenario",
                layer="logic",
                label="",
                file_path="app/scenarios/x.py",
                origin="user",
            )
        )
        text = to_markdown(graph)
        assert "(класс не создан)" in text
        assert "## 2. План" in text

    def test_граф_без_узлов_не_роняет(self):
        graph = Graph(version=4, project_root=".")
        text = to_markdown(graph)
        assert "## 1. Уже реализовано" in text
        assert "(нет)" in text


class TestJson:
    def test_демо_граф_экспортируется(self, demo):
        graph = scan(demo)
        data = json.loads(to_json(graph))
        assert data["version"] == 4
        assert len(data["nodes"]) == len(graph.nodes)
        assert len(data["edges"]) == len(graph.edges)

    def test_план_переживает_экспорт(self, demo):
        graph = scan(demo)
        graph.nodes.append(
            Node(id="model:models.py:NewOne", kind="model", layer="representation", label="NewOne",
                 file_path="models.py", origin="user")
        )
        graph.edges.append(
            Edge(id="a::uses::b", from_node="a", to_node="b", kind="uses", origin="user")
        )
        data = json.loads(to_json(graph))
        assert any(n["id"] == "model:models.py:NewOne" for n in data["nodes"])
        assert any(e["id"] == "a::uses::b" for e in data["edges"])
