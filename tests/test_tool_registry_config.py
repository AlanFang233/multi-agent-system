from pathlib import Path

from agent_ecosystem.tool_registry import ToolRecord, ToolRegistry, _expand


def test_expand_resolves_env_vars_and_home(monkeypatch):
    monkeypatch.setenv("MAS_TEST_ROOT", "/opt/mas")
    assert _expand("$MAS_TEST_ROOT/bin/python") == "/opt/mas/bin/python"
    assert _expand("${MAS_TEST_ROOT}/x") == "/opt/mas/x"
    assert _expand("~/x") == str(Path.home() / "x")
    assert _expand("-m") == "-m"


def test_shipped_config_has_no_foreign_absolute_paths():
    text = Path("config/mcp_registry.yaml").read_text(encoding="utf-8")
    assert "/home/alan_fang" not in text


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
