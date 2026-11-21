"""A standalone MCP v2 server for verifying tool connectivity."""

from mcp.server import MCPServer

mcp = MCPServer("Local Calculator")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Local calculator MCP server")
    parser.add_argument("--stdio", action="store_true", help="Start on demand without a listening port")
    args = parser.parse_args()
    if args.stdio:
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", host="127.0.0.1", port=9001)
