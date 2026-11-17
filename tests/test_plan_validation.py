"""DAG plan validation: topological order, cycles, limits, depth (Phase 2).

Semantics adapted from AgentForge tests/test_planner.py, extended with the
max-dependency-depth cap and schema strictness required by Phase 2.
"""
import pytest
from pydantic import ValidationError

from agent_ecosystem.orchestration.errors import PlanValidationError
from agent_ecosystem.orchestration.models import ExecutionPlan
from agent_ecosystem.orchestration.planner import validate_plan
from orchestration_support import make_plan


def spec(key, deps=(), caps=("web_search",), **kw):
    return {"key": key, "description": f"task {key}",
            "required_capabilities": list(caps), "depends_on": list(deps), **kw}


def test_topological_order_respects_dependencies():
    plan = make_plan([spec("third", ["second"]), spec("second", ["first"]), spec("first")])
    ordered = validate_plan(plan, max_subtasks=8)
    assert [s.key for s in ordered] == ["first", "second", "third"]


def test_deterministic_tie_break_by_original_order():
    plan = make_plan([spec("b"), spec("a"), spec("c")])
    ordered = validate_plan(plan, max_subtasks=8)
    assert [s.key for s in ordered] == ["b", "a", "c"]


def test_cycle_is_rejected():
    plan = make_plan([spec("a", ["b"]), spec("b", ["a"])])
    with pytest.raises(PlanValidationError, match="cycle"):
        validate_plan(plan, max_subtasks=8)


def test_self_dependency_is_rejected():
    plan = make_plan([spec("a", ["a"])])
    with pytest.raises(PlanValidationError, match="itself"):
        validate_plan(plan, max_subtasks=8)


def test_unknown_dependency_is_rejected():
    plan = make_plan([spec("a", ["ghost"])])
    with pytest.raises(PlanValidationError, match="unknown"):
        validate_plan(plan, max_subtasks=8)


def test_subtask_limit_enforced():
    plan = make_plan([spec("a"), spec("b")])
    with pytest.raises(PlanValidationError, match="exceeding"):
        validate_plan(plan, max_subtasks=1)


def test_depth_limit_enforced():
    chain = [spec("n0")] + [spec(f"n{i}", [f"n{i-1}"]) for i in range(1, 7)]
    plan = make_plan(chain)
    with pytest.raises(PlanValidationError, match="depth"):
        validate_plan(plan, max_subtasks=10, max_depth=6)
    # 6-chain passes when depth cap is 7
    ordered = validate_plan(plan, max_subtasks=10, max_depth=7)
    assert len(ordered) == 7


def test_duplicate_keys_rejected_at_model_level():
    with pytest.raises(ValidationError, match="duplicate"):
        ExecutionPlan.model_validate(
            {"goal_summary": "g", "subtasks": [spec("a"), spec("a")]}
        )


def test_empty_plan_rejected():
    with pytest.raises(ValidationError):
        ExecutionPlan.model_validate({"goal_summary": "g", "subtasks": []})


def test_extra_fields_forbidden_no_agent_self_assignment():
    # The LLM must not smuggle agent assignments or unknown fields into a node.
    with pytest.raises(ValidationError):
        ExecutionPlan.model_validate(
            {"goal_summary": "g", "subtasks": [spec("a", assigned_agent="coding")]}
        )


def test_priority_bounds_and_capability_normalization():
    plan = make_plan([spec("a", caps=["Web Search", "EVIDENCE-synthesis"], priority=3)])
    ordered = validate_plan(plan, max_subtasks=8)
    assert ordered[0].priority == 3
    assert ordered[0].required_capabilities == ["web_search", "evidence_synthesis"]
    with pytest.raises(ValidationError):
        make_plan([spec("a", priority=42)])
