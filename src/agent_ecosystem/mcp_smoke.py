"""Smoke test against local MCP Calculator."""

import asyncio
from mcp import Client


async def main():
    async with Client("http://127.0.0.1:9001/mcp") as client:
        result = await client.call_tool("add", {"a": 2, "b": 3})
        print(result.structured_content)


if __name__ == "__main__":
    asyncio.run(main())
