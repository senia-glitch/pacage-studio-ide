import pytest

from studio.pipeline import GATES, ROLES, PipelineError, PipelineSettings
from studio.pipeline.kinds import KINDS, kind_of, kinds_of_stage, stage_of, title_of
from studio.pipeline.roles import AGENT_LABELS, TIER_TOP, role


def test_defaults_are_filled_for_every_gate(tmp_path):
    settings = PipelineSettings(tmp_path)
    assert settings.mode == "gates"
    for key, spec in GATES.items():
        crits = settings.criteria(key)
        assert [c["key"] for c in crits] == [c.key for c in spec.criteria]
        assert all(c["threshold"] == 8 and c["enabled"] for c in crits)


def test_criteria_are_independent_per_gate_and_persist(tmp_path):
    settings = PipelineSettings(tmp_path)
    settings.set_criterion("schema", "extensibility", threshold=9, enabled=True)
    settings.set_criterion("requirements", "clarity", enabled=False)
    again = PipelineSettings(tmp_path)
    assert (
        next(c for c in again.criteria("schema") if c["key"] == "extensibility")["threshold"] == 9
    )
    assert [c["key"] for c in again.enabled_criteria("requirements")] == [
        "adequacy",
        "testability",
        "noncontradiction",
    ]
    assert all(c["threshold"] == 8 for c in again.criteria("problems"))


def test_the_gate_has_one_threshold_that_can_be_any_number_from_0_to_10(tmp_path):
    settings = PipelineSettings(tmp_path)
    assert settings.gate_threshold("problems") == 8
    settings.set_criterion("schema", "extensibility", threshold=9)
    key = settings.add_criterion("schema", "Простота", "", threshold=3)
    settings.set_gate_threshold("schema", 5)
    again = PipelineSettings(tmp_path)
    assert again.gate_threshold("schema") == 5 and again.gate_threshold("problems") == 8
    assert all(c["threshold"] == 5 for c in again.criteria("schema"))  # личные пороги сброшены
    again.set_criterion("schema", "extensibility", threshold=9)  # а потом можно задать свой
    assert next(c for c in again.criteria("schema") if c["key"] == "extensibility")["threshold"] == 9
    new = again.add_criterion("schema", "Своё")
    assert next(c for c in again.criteria("schema") if c["key"] == new)["threshold"] == 5
    assert key.startswith("custom")
    for bad in (-1, 11, "x"):
        with pytest.raises(PipelineError):
            again.set_gate_threshold("schema", bad)
    again.set_gate_threshold("schema", 0)  # любую: хоть «принимаем что угодно»
    assert again.gate_threshold("schema") == 0
    again.reset_gate("schema")
    assert again.gate_threshold("schema") == 8


def test_custom_criteria_add_edit_remove_and_reset(tmp_path):
    settings = PipelineSettings(tmp_path)
    key = settings.add_criterion("schema", "Простота миграций", "легко менять", threshold=9)
    assert key == "custom-1"
    assert settings.criteria("schema")[-1]["custom"] is True
    settings.set_criterion("schema", key, threshold=7, title="Миграции")
    assert settings.criteria("schema")[-1]["threshold"] == 7
    settings.remove_criterion("schema", key)
    assert not settings.criteria("schema")[-1]["custom"]
    with pytest.raises(PipelineError):
        settings.remove_criterion("schema", "normalization")  # стандартный можно только отключить
    settings.set_criterion("schema", "normalization", enabled=False)
    settings.reset_gate("schema")
    assert all(c["enabled"] for c in settings.criteria("schema"))


def test_validation(tmp_path):
    settings = PipelineSettings(tmp_path)
    with pytest.raises(PipelineError):
        settings.set_mode("хаос")
    with pytest.raises(PipelineError):
        settings.set_criterion("schema", "normalization", threshold=11)
    with pytest.raises(PipelineError):
        settings.set_criterion("schema", "нет", enabled=False)
    with pytest.raises(PipelineError):
        settings.add_criterion("schema", "  ")
    with pytest.raises(PipelineError):
        settings.criteria("нет_ворот")
    with pytest.raises(PipelineError):
        settings.set_number("wave_threshold", 0)
    with pytest.raises(PipelineError):
        settings.set_number("что-то", 3)
    settings.set_number("wave_threshold", 12)
    assert PipelineSettings(tmp_path).wave_threshold == 12
    assert settings.quick_fix_limit == 2 and settings.stall_rounds == 3 and settings.max_rounds == 12


def test_roles_catalog_and_assignment(tmp_path):
    assert len(ROLES) == 11
    assert ROLES["coordinator"].tier == TIER_TOP and ROLES["assistant"].tier == 1
    assert set(AGENT_LABELS.values()) == {"Claude Code Pro", "OpenCode Go Plus"}
    settings = PipelineSettings(tmp_path)
    assert settings.role("executor") == {"agent": "", "model": ""}
    settings.set_role("executor", "claude", "opus")
    assert PipelineSettings(tmp_path).role("executor") == {"agent": "claude", "model": "opus"}
    with pytest.raises(PipelineError):
        settings.set_role("executor", "gpt-agent")
    with pytest.raises(PipelineError):
        settings.set_role("нет", "claude")
    with pytest.raises(PipelineError):
        role("нет")
    assert set(settings.roles()) == set(ROLES)


def test_evaluator_must_differ_from_authors(tmp_path):
    settings = PipelineSettings(tmp_path)
    assert settings.evaluator_warnings() == []
    settings.set_role("evaluator", "claude", "opus")
    settings.set_role("analyst", "claude", "opus")
    warnings = settings.evaluator_warnings()
    assert warnings and "Аналитик" in warnings[0] and warnings[0].startswith("Этап 1")
    settings.set_role("analyst", "opencode", "other")
    assert settings.evaluator_warnings() == []


def test_kinds_catalog():
    assert stage_of("page") == 3 and title_of("problem") == "Проблема"
    assert "requirement" in kinds_of_stage(1) and "endpoint" in kinds_of_stage(4)
    assert KINDS["boundary"].constitution and not KINDS["problem"].constitution
    with pytest.raises(PipelineError):
        kind_of("нет")
