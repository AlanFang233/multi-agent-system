"""Planner LLM loop: structured output, bounded repair, capability detection,
explicit failure (never a silent fallback plan). All offline via FakeLLMClient."""

import pytest

from agent_ecosystem.orchestration.errors import PlanningError
from agent_ecosystem.orchestration.planner import (
    Planner,
    build_planner_messages,
    extract_json_payload,
)
from orchestration_support import FakeLLMClient, make_assigner, plan_json

GOOD = [
    {"key": "gather", "description": "搜索资料", "required_capabilities": ["web_search"]},
    {"key": "summarize", "description": "总结", "required_capabilities": ["statistics"],
     "depends_on": ["gather"]},
]


def planner(responses, **kw):
    client = FakeLLMClient(responses)
    defaults = dict(output_mode="auto", max_repairs=1, max_subtasks=8, max_depth=6)
    defaults.update(kw)
    return Planner(client, "mock-model", **defaults), client


async def test_valid_plan_first_attempt_function_mode():
    p, client = planner([{"arguments": plan_json(GOOD)}])
    result = await p.plan_goal("查资料并总结", [], make_assigner())
    assert [s.key for s in result.ordered_subtasks] == ["gather", "summarize"]
    assert [a.card.agent_id for a in result.assignments] == ["research", "analysis"]
    assert result.output_mode == "function"
    # auto mode starts with native function calling
    assert client.calls[0]["tool_choice"]["function"]["name"] == "submit_plan"
    assert result.attempts[-1]["error"] is None


async def test_content_json_also_accepted_in_function_mode():
    p, _ = planner([{"content": plan_json(GOOD)}])
    result = await p.plan_goal("g", [], make_assigner())
    assert len(result.ordered_subtasks) == 2


async def test_invalid_json_then_repair_succeeds():
    p, client = planner([{"content": "sure! here is the plan: not json {{"},
                         {"content": plan_json(GOOD)}])
    result = await p.plan_goal("g", [], make_assigner())
    assert len(result.ordered_subtasks) == 2
    assert len(result.attempts) == 2 and result.attempts[0]["error"]
    repair_msg = client.calls[1]["messages"][-1]["content"]
    assert "[REPAIR]" in repair_msg


async def test_repair_budget_is_bounded():
    p, _ = planner([{"content": "garbage one"}, {"content": "garbage two"}], max_repairs=1)
    with pytest.raises(PlanningError, match="failed after 2 attempt"):
        await p.plan_goal("g", [], make_assigner())


async def test_schema_violation_explicit_failure():
    bad = [{"key": "a", "description": "x"}]  # missing required_capabilities
    p, _ = planner([{"content": plan_json(bad)}, {"content": plan_json(bad)}], max_repairs=1)
    with pytest.raises(PlanningError, match="schema validation"):
        await p.plan_goal("g", [], make_assigner())


async def test_cycle_plan_rejected_not_repaired_forever():
    cyclic = [
        {"key": "a", "description": "x", "required_capabilities": ["web_search"], "depends_on": ["b"]},
        {"key": "b", "description": "y", "required_capabilities": ["statistics"], "depends_on": ["a"]},
    ]
    p, _ = planner([{"content": plan_json(cyclic)}], max_repairs=0)
    with pytest.raises(PlanningError, match="cycle"):
        await p.plan_goal("g", [], make_assigner())


async def test_oversized_plan_rejected():
    many = [{"key": f"k{i}", "description": "d", "required_capabilities": ["web_search"]}
            for i in range(9)]
    p, _ = planner([{"content": plan_json(many)}], max_repairs=0)
    with pytest.raises(PlanningError, match="exceeding max"):
        await p.plan_goal("g", [], make_assigner())


async def test_capability_gap_rejected_without_burning_repairs():
    impossible = [{"key": "a", "description": "x", "required_capabilities": ["quantum_fusion"]}]
    p, client = planner([{"content": plan_json(impossible)}], max_repairs=1)
    with pytest.raises(PlanningError, match="plan rejected"):
        await p.plan_goal("g", [], make_assigner())
    assert len(client.calls) == 1  # structural failure: no repair retry


async def test_truncated_response_retries_without_echo():
    p, client = planner([
        {"content": plan_json(GOOD), "finish_reason": "length"},
        {"content": plan_json(GOOD)},
    ])
    result = await p.plan_goal("g", [], make_assigner())
    assert len(result.ordered_subtasks) == 2
    last_user = client.calls[1]["messages"][-1]
    assert "[RETRY]" in last_user["content"]
    # the truncated assistant text must NOT be echoed back (prompt-size lesson)
    assert all(m.get("role") != "assistant" for m in client.calls[1]["messages"][2:])


async def test_provider_capability_downgrade_function_to_json():
    class BadRequest(Exception):
        pass

    p, client = planner([
        BadRequest("Error code: 400 - unsupported parameter: 'tools'"),
        {"content": plan_json(GOOD)},
    ])
    result = await p.plan_goal("g", [], make_assigner())
    assert result.output_mode == "json"
    assert "response_format" in client.calls[1] and "tools" not in client.calls[1]


async def test_transport_error_is_explicit_no_silent_fallback():
    p, client = planner([RuntimeError("connection reset by peer")])
    with pytest.raises(PlanningError, match="planner LLM call failed"):
        await p.plan_goal("g", [], make_assigner())
    assert len(client.calls) == 1  # no hidden retry with a canned plan


def test_extract_json_payload_levels():
    assert extract_json_payload('{"a": 1}') == {"a": 1}
    assert extract_json_payload('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json_payload('noise before {"a": 3} noise after') == {"a": 3}
    with pytest.raises(Exception):
        extract_json_payload("no json at all")
    with pytest.raises(Exception):
        extract_json_payload("")


def test_planner_prompt_contains_goal_catalog_and_limits():
    msgs = build_planner_messages("研究RAG", make_assigner().cards, 8, 6)
    assert msgs[0]["role"] == "system" and "planning module" in msgs[0]["content"]
    user = msgs[1]["content"]
    assert "研究RAG" in user and "max_subtasks=8" in user
    assert "research" in user and "web_search" in user
    assert "Do NOT add synthesis" in msgs[0]["content"]
