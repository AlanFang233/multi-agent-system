import asyncio
from pathlib import Path

import pytest

from agent_ecosystem.agent_skill_registry import AgentSkillRegistry


class FakeDelegate:
    async def refresh(self):
        return {}

    def list_for_agent(self, agent_id):
        return [{"tool_id": "fake:ping", "description": "ping", "input_schema": {"type": "object"}}]

    async def call(self, agent_id, tool_id, arguments):
        return {"tool_id": tool_id, "is_error": False, "structured_content": {"ok": True}, "content": []}


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "agent_skills.yaml"


def make_registry():
    return AgentSkillRegistry(FakeDelegate(), CONFIG_PATH)


def test_own_skill_catalog_only():
    reg = make_registry()
    research = reg.list_for_agent("research")
    analysis = reg.list_for_agent("analysis")
    assert any(t["tool_id"] == "skills:load" for t in research)
    assert {t["tool_id"] for t in analysis} == {"fake:ping", "skills:load", "skills:loss_metrics"}
    research_names = next(t for t in research if t["tool_id"] == "skills:load")["input_schema"]["properties"]["name"]["enum"]
    assert research_names == ["evidence-synthesis"]
    assert "skills:loss_metrics" not in {t["tool_id"] for t in research}


def test_skills_load_returns_role_instructions():
    reg = make_registry()
    result = asyncio.run(reg.call("coding", "skills:load", {"name": "git-audit"}))
    assert "git:git_status" in result["structured_content"]["instructions"]


def test_cross_agent_skill_blocked():
    reg = make_registry()
    with pytest.raises(Exception):
        asyncio.run(reg.call("research", "skills:load", {"name": "git-audit"}))
    with pytest.raises(PermissionError):
        asyncio.run(reg.call("coding", "skills:loss_metrics", {"series_a": [1, 0.5], "series_b": [1, 0.25]}))


def test_metrics_deterministic():
    reg = make_registry()
    result = asyncio.run(reg.call("analysis", "skills:loss_metrics", {
        "series_a": [0.9, 0.7, 0.5],
        "series_b": [0.8, 0.5, 0.3],
        "label_a": "GD",
        "label_b": "ADN",
    }))
    content = result["structured_content"]
    assert content["series_a"]["strictly_decreasing"]
    assert content["series_a"]["mean"] == pytest.approx(0.7)
    assert content["series_b"]["total_decrease"] == pytest.approx(0.5)
    assert content["comparison"]["b_lower_than_a_count"] == 3


def test_invalid_numbers_rejected():
    reg = make_registry()
    with pytest.raises(ValueError):
        asyncio.run(reg.call("analysis", "skills:loss_metrics", {
            "series_a": [0.9, float('inf')], "series_b": [0.8, 0.4],
        }))


def test_underlying_mcp_and_a2a_untouched():
    reg = make_registry()
    assert asyncio.run(reg.refresh()) == {}
    result = asyncio.run(reg.call("coding", "fake:ping", {}))
    assert result["structured_content"]["ok"]
