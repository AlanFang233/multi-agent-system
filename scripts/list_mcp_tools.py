import asyncio
from collections import defaultdict
from agent_ecosystem.tool_registry import ToolRegistry

async def main():
    registry = ToolRegistry("config/mcp_registry.yaml")
    errors = await registry.refresh()

    print("\n=== MCP Server 连接情况 ===")
    if errors:
        for server, error in errors.items():
            print(f"[失败] {server}: {error}")
    else:
        print("所有已配置 Server 均连接成功")

    tools_by_server = defaultdict(list)

    for tool_id in sorted(registry.tools):
        server_id = tool_id.split(":", 1)[0]
        tools_by_server[server_id].append(tool_id)

    print("\n=== 全部已发现 MCP Tools ===")

    for server_id, tools in sorted(tools_by_server.items()):
        print(f"\n[{server_id}] 共 {len(tools)} 个工具")
        for tool_id in tools:
            print(f"  - {tool_id}")

    print(f"\n工具总数: {len(registry.tools)}")

    print("\n=== Agent 权限列表 ===")
    for agent_id in ["research", "coding", "analysis"]:
        allowed = registry.list_for_agent(agent_id)
        print(f"\n[{agent_id}] 可用工具: {len(allowed)}")
        for tool in allowed:
            print(f"  - {tool['tool_id']}")

asyncio.run(main())
