"""Контракт эндпоинта для фронтенда: запрос и ответ по коду бэкенда."""

from __future__ import annotations

from pathlib import Path

from studio.frontend.brief import _link_lines
from studio.frontend.contract import describe, endpoint_contract
from studio.frontend.links import check_links
from studio.metamodel import Edge, Graph, Node

ROUTE = '''from fastapi import APIRouter, Depends, Request
router = APIRouter()


class LeadIn(BaseModel):
    name: str
    message: Optional[str] = None


@router.post("/leads", status_code=201, response_model=LeadOut)
async def create_lead(req: LeadIn, request: Request, q: int = 5, user=Depends(auth)) -> dict:
    pass
'''

SCENARIO = '''def _row(lead):
    return {"id": lead.id, "stage": lead.stage}


def _skip(lead):
    return None


class CreateLead:
    def run(self, data):
        helper = _skip(data)
        if not data:
            return {"error": "empty"}
        return _row(data)

    async def other(self):
        def inner():
            return {"hidden": 1}
        return [_row(x) for x in range(3)]
'''


def _graph(tmp_path: Path, signature: str | None = None) -> Graph:
    (tmp_path / "routes.py").write_text(ROUTE, encoding="utf-8")
    (tmp_path / "scenario.py").write_text(SCENARIO, encoding="utf-8")
    signature = ROUTE[ROUTE.index("@router") :].replace("\n    pass\n", "\n") if (
        signature is None) else signature
    endpoint = Node(id="endpoint:r", kind="endpoint", label="POST /leads", file_path="routes.py",
                    details={"http_method": "post", "url_path": "/leads", "signature": signature})
    scenario = Node(id="scenario:s", kind="scenario", label="CreateLead", file_path="scenario.py",
                    line=13, end_line=25)
    graph = Graph(project_root=str(tmp_path), nodes=[endpoint, scenario])
    graph.edges = [Edge(id="d", from_node="endpoint:r", to_node="scenario:s", kind="dispatch")]
    return graph


def test_запрос_ответ_и_ключи_из_сценария(tmp_path: Path) -> None:
    contract = endpoint_contract(_graph(tmp_path), "endpoint:r")
    assert [a["name"] for a in contract["request"]] == ["req", "q"]  # Request и Depends пропущены
    assert [f["name"] for f in contract["request"][0]["fields"]] == ["name", "message"]
    assert contract["request"][1] == {"name": "q", "type": "int", "default": "5"}
    assert contract["status"] == 201 and contract["response_model"] == "LeadOut"
    # return-ы вложенной функции не считаются; список и вспомогательная функция разобраны
    assert contract["returns"] == ["{error}", "{id, stage}", "[{id, stage}]"]
    text = describe(contract)
    assert text.startswith("запрос: req: LeadIn {name: str, message: Optional[str]}; q: int = 5")
    assert "ответ: LeadOut, сценарий возвращает {error} | {id, stage}" in text
    assert text.endswith("код 201")


def test_нет_эндпоинта_кода_или_сигнатуры(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    assert endpoint_contract(graph, "endpoint:нет") is None
    assert endpoint_contract(graph, "scenario:s") is None  # не эндпоинт
    assert endpoint_contract(_graph(tmp_path, signature="не питон ("), "endpoint:r") is None
    assert endpoint_contract(_graph(tmp_path, signature=""), "endpoint:r") is None
    assert describe(None) == "" and describe({"request": [], "returns": []}) == ""
    plan = Node(id="endpoint:p", kind="endpoint", label="GET /x", file_path="")
    assert endpoint_contract(Graph(project_root=str(tmp_path), nodes=[plan]), "endpoint:p") is None


def test_битые_и_пропавшие_файлы_не_ломают(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    (tmp_path / "scenario.py").write_text("def broken(:\n", encoding="utf-8")
    contract = endpoint_contract(graph, "endpoint:r")
    assert contract["returns"] == [] and contract["request"][0]["name"] == "req"
    (tmp_path / "routes.py").unlink()
    assert endpoint_contract(graph, "endpoint:r")["request"][0].get("fields") is None
    (tmp_path / "scenario.py").write_text("x = 1\n", encoding="utf-8")
    assert endpoint_contract(graph, "endpoint:r")["returns"] == []  # класса сценария нет


def test_сигнатура_с_kwonly_и_без_аннотаций(tmp_path: Path) -> None:
    signature = "@router.get('/a', response_model=None)\nasync def a(self, x, *, y: str = 'k') -> None:\n"
    contract = endpoint_contract(_graph(tmp_path, signature=signature), "endpoint:r")
    assert contract["request"] == [
        {"name": "x", "type": "", "default": ""}, {"name": "y", "type": "str", "default": "'k'"}]
    assert contract["response_model"] == "None" and contract["status"] is None
    assert describe(contract).startswith("запрос: x; y: str = 'k'")


def test_контракт_в_сверке_и_в_тз(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    front = {"pages": [{"id": "page:p", "name": "p", "calls": ["POST /leads"], "blocks": []}]}
    links = check_links(front, graph, "")
    assert links["rows"][0]["status"] == "ok" and "запрос: req: LeadIn" in links["rows"][0]["contract"]
    lines = _link_lines(front["pages"], links)
    assert lines[0].endswith("эндпоинт в бэкенде есть")
    assert lines[1].startswith("  - контракт эндпоинта (по коду бэкенда): запрос: req")
    missing = check_links({"pages": [{"id": "page:q", "name": "q", "calls": ["GET /ghost"],
                                       "blocks": []}]}, graph, "")
    assert missing["rows"][0]["contract"] == ""
    assert len(_link_lines([{"id": "page:q", "name": "q", "calls": ["GET /ghost"],
                              "blocks": []}], missing)) == 1
