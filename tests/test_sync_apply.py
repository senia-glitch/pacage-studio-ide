"""Приём JSON-ответа агента по синхронизации описаний (бэкенд)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio.agent.sync import build_sync_prompt
from studio.metadata import MetadataError
from studio.server.state import ProjectState


def _state(tmp_path: Path) -> ProjectState:
    from conftest import build_demo

    state = ProjectState(build_demo(tmp_path))
    state.rescan("start")
    return state


def _first_fact(state: ProjectState, kind: str = "scenario"):
    return next(n for n in state.graph.nodes if n.kind == kind and not n.planned)


def test_промпт_требует_json_и_запрещает_правку_файлов(tmp_path: Path) -> None:
    state = _state(tmp_path)
    text = build_sync_prompt(state.graph, "all")
    assert '"updates"' in text and '"not_synced"' in text
    assert "НЕ редактируй" in text


def test_предпросмотр_не_пишет_файл(tmp_path: Path) -> None:
    state = _state(tmp_path)
    node = _first_fact(state)
    payload = {"updates": [{"id": node.id, "description": "Новое", "acceptance": ["А"]}]}
    report = state.sync_apply({"json": json.dumps(payload)})
    assert report["applied"] == 0
    assert report["counts"]["change"] == 1
    assert report["rows"][0]["after"]["description"] == "Новое"
    meta = state.root / ".studio" / "metadata.json"
    assert not meta.exists() or "Новое" not in meta.read_text(encoding="utf-8")


def test_применение_пишет_только_описание_и_критерии(tmp_path: Path) -> None:
    state = _state(tmp_path)
    endpoint = _first_fact(state, "endpoint")
    scenario = _first_fact(state)
    domain_before = endpoint.domain
    payload = {
        "updates": [
            {"id": endpoint.id, "description": "Эндпоинт", "acceptance": ["201"]},
            {"id": scenario.id, "description": "Сценарий", "acceptance": []},
        ]
    }
    text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    report = state.sync_apply({"json": text, "apply": True, "ids": [endpoint.id]})
    assert report["applied"] == 1
    graph = state.graph
    after = next(n for n in graph.nodes if n.id == endpoint.id)
    other = next(n for n in graph.nodes if n.id == scenario.id)
    assert after.description == "Эндпоинт" and after.acceptance == ["201"]
    assert after.domain == domain_before
    assert other.description == (scenario.description or "")
    assert state.sync_apply({"json": text})["counts"]["same"] == 1


def test_плановые_и_чужие_id_отклоняются(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload = {"updates": [{"id": "studio:nope", "description": "x", "acceptance": []}]}
    report = state.sync_apply({"json": payload, "apply": True})
    assert report["applied"] == 0
    assert report["rows"][0]["status"] == "rejected"


def test_некорректный_ответ_даёт_ошибку_ввода(tmp_path: Path) -> None:
    state = _state(tmp_path)
    for bad in ("", "не json", '{"updates": "нет"}', "[1, 2]"):
        with pytest.raises(MetadataError) as info:
            state.sync_apply({"json": bad, "apply": True})
        assert info.value.code == "invalid_input"


# ---------------------------------------------------------- отчёт по ТЗ-плану

PLAN_DATA = {
    "title": "Возврат заказа",
    "slug": "refund_request",
    "description": "Оформляет возврат заказа целиком.",
    "acceptance": ["Возврат сохранён"],
    "domain": "users",
    "http_method": "post",
    "path": "/refunds",
    "uses_db": True,
    "with_test": True,
}


def _implement(root: Path) -> None:
    (root / "app/api/v1/routes/refund_request.py").write_text(
        "from fastapi import APIRouter\nfrom core import run\n\n"
        "router = APIRouter()\n\n"
        '@router.post("/refunds")\nasync def refund_request():\n'
        '    return await run("refund_request")\n',
        encoding="utf-8",
    )
    (root / "app/scenarios/refund_request.py").write_text(
        "from core import BaseScenario, register_scenario\n\n"
        '@register_scenario("refund_request")\n'
        "class RefundRequestScenario(BaseScenario):\n"
        "    async def execute(self):\n"
        '        await self._db.create("orders", {})\n',
        encoding="utf-8",
    )
    (root / "tests/test_refund_request.py").write_text(
        'def test_refund_request(client):\n    client.post("/api/v1/refunds")\n',
        encoding="utf-8",
    )


def _plan_state(tmp_path: Path) -> ProjectState:
    from studio.creator.plan import create_plan
    from studio.env import read_env

    state = _state(tmp_path)
    create_plan(PLAN_DATA, read_env(state.root))
    state.rescan("plan")
    return state


def _report(*updates: dict, **extra) -> dict:
    return {"updates": list(updates), "not_synced": [], **extra}


def test_отчёт_по_плану_до_реализации_отклоняется(tmp_path: Path) -> None:
    state = _plan_state(tmp_path)
    report = state.sync_apply(
        {
            "json": _report(
                {
                    "plan": "refund_request",
                    "layer": "scenario",
                    "description": "Возврат",
                    "acceptance": [],
                }
            ),
            "apply": True,
        }
    )
    assert report["applied"] == 0
    row = report["rows"][0]
    assert row["status"] == "rejected" and "не найден в коде" in row["reason"]
    assert row["ref"] == "refund_request / scenario"


def test_отчёт_по_плану_после_реализации_пишет_описание_слоя(tmp_path: Path) -> None:
    state = _plan_state(tmp_path)
    _implement(state.root)
    state.rescan("code")
    payload = _report(
        {
            "plan": "refund_request",
            "layer": "scenario",
            "description": "Создаёт запись возврата",
            "acceptance": ["Запись создана"],
        },
        {"plan": "refund_request", "layer": "endpoint", "description": "POST /refunds",
         "acceptance": []},
        {"plan": "refund_request", "layer": "test", "description": "x", "acceptance": []},
        {"plan": "ghost", "layer": "scenario", "description": "x", "acceptance": []},
        {"plan": "refund_request", "layer": "cache", "description": "x", "acceptance": []},
        {"plan": "refund_request", "description": "x", "acceptance": []},
        {"id": "x", "plan": "refund_request", "layer": "scenario",
         "description": "x", "acceptance": []},
        report={
            "tests": "pytest: 3 passed",
            "decisions": ["Метод _db.create"],
            "questions": [],
            "unknown": 1,
        },
    )
    preview = state.sync_apply({"json": payload})
    statuses = [row["status"] for row in preview["rows"]]
    assert statuses == ["change", "change", "rejected", "rejected", "rejected", "rejected",
                        "rejected"]
    assert "описания не хранятся" in preview["rows"][2]["reason"]
    assert preview["report"]["tests"] == "pytest: 3 passed"
    assert preview["report"]["decisions"] == ["Метод _db.create"]
    assert any("report" in w and "unknown" in w for w in preview["warnings"])

    done = state.sync_apply({"json": payload, "apply": True})
    assert done["applied"] == 2
    scenario = next(n for n in state.graph.nodes if n.id.endswith(":RefundRequestScenario"))
    assert scenario.description == "Создаёт запись возврата"
    assert scenario.acceptance == ["Запись создана"]
    assert state.sync_apply({"json": payload})["counts"]["same"] == 2


def test_report_не_объект_даёт_предупреждение(tmp_path: Path) -> None:
    state = _state(tmp_path)
    result = state.sync_apply({"json": {"updates": [], "report": "текст"}})
    assert result["report"] is None
    assert any("report" in w for w in result["warnings"])


def test_тз_содержит_json_формат_отчёта(tmp_path: Path) -> None:
    state = _plan_state(tmp_path)
    from studio.agent import BriefOptions, build_brief
    from studio.env import read_env
    from studio.source import load_sources

    brief = build_brief(
        state.graph, load_sources(read_env(state.root)), BriefOptions(map_mode="none")
    )
    assert '"plan": "<slug плана>"' in brief.markdown
    assert "ОДИН JSON-блок" in brief.markdown


# -------------------------------------------------------- отмена применения


def test_отмена_возвращает_прежние_описания(tmp_path: Path) -> None:
    state = _state(tmp_path)
    scenario = _first_fact(state)
    before = (scenario.description, list(scenario.acceptance))
    payload = {"updates": [{"id": scenario.id, "description": "Новое", "acceptance": ["А"]}]}
    done = state.sync_apply({"json": payload, "apply": True})
    assert done["applied"] == 1 and len(done["undo"]) == 1
    assert done["undo"][0]["before"] == {"description": before[0], "acceptance": before[1]}

    undone = state.sync_undo({"undo": done["undo"]})
    assert undone["restored"] == 1 and undone["skipped"] == []
    node = next(n for n in state.graph.nodes if n.id == scenario.id)
    assert (node.description, list(node.acceptance)) == before


def test_отмена_не_трогает_узел_изменённый_после_применения(tmp_path: Path) -> None:
    state = _state(tmp_path)
    scenario = _first_fact(state)
    payload = {"updates": [{"id": scenario.id, "description": "Новое", "acceptance": []}]}
    done = state.sync_apply({"json": payload, "apply": True})
    state.save_metadata({"node_id": scenario.id, "description": "Правка руками"})
    undone = state.sync_undo({"undo": done["undo"]})
    assert undone["restored"] == 0 and undone["skipped"] == [scenario.id]
    node = next(n for n in state.graph.nodes if n.id == scenario.id)
    assert node.description == "Правка руками"


def test_отмена_плохой_ввод(tmp_path: Path) -> None:
    state = _state(tmp_path)
    for bad in (None, [], [1], [{"id": "x"}], [{"id": "x", "before": {}, "after": {}}]):
        with pytest.raises(MetadataError) as info:
            state.sync_undo({"undo": bad})
        assert info.value.code == "invalid_input"
