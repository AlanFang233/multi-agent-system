from pathlib import Path

from agent_ecosystem.tool_registry import ToolRecord, ToolRegistry


def test_policy_filters_tools():
    registry = ToolRegistry(Path("config/mcp_registry.yaml"))
    registry.tools["calculator:add"] = ToolRecord(
        tool_id="calculator:add",
        server_id="calculator",
        name="add",
        description="Add two integers",
        input_schema={"type": "object"},
    )
    assert [x["tool_id"] for x in registry.list_for_agent("coding")] == ["calculator:add"]
    assert registry.list_for_agent("research") == []
    assert registry.list_for_agent("unknown") == []
