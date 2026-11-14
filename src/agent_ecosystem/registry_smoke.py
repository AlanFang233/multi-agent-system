"""Try: python -m agent_ecosystem.registry_smoke"""
import asyncio
import json

from agent_ecosystem.tool_registry import ToolRegistry


async def main() -> None:
    registry = ToolRegistry("config/mcp_registry.yaml")
    errors = await registry.refresh()
    if errors:
        print("Unavailable MCP servers:", errors)
    print("Tools visible to Coding Agent:")
    print(json.dumps(registry.list_for_agent("coding"), indent=2, ensure_ascii=False))
    result = await registry.call("coding", "calculator:add", {"a": 2, "b": 3})
    print("Tool call result:", result["structured_content"])
    assert not result["is_error"], result
    assert result["structured_content"] == {"result": 5}, result
    try:
        await registry.call("research", "calculator:add", {"a": 2, "b": 3})
    except PermissionError:
        print("Research Agent permission denied as expected")
    else:
        raise AssertionError("Access control failed: research was allowed")
    print("MCP Tool Registry smoke test PASSED")


if __name__ == "__main__":
    asyncio.run(main())
