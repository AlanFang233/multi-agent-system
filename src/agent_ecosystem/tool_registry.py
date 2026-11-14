"""MCP Tool Registry MVP: discovery, agent-specific allowlists and dispatch.

Uses the high-level MCP Python SDK v2 Client. For each discovery/call a
connection is opened and closed; move to persistent per-server sessions when
throughput warrants it. This is a trusted-process library, NOT a secure network
Gateway or an authenticated identity provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import validate
from mcp import Client
from mcp.client.stdio import StdioServerParameters


@dataclass(frozen=True)
class ToolRecord:
    tool_id: str
    server_id: str
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolRegistry:
    def __init__(self, config_path: str | Path = "config/mcp_registry.yaml"):
        config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        self.servers: dict[str, dict[str, Any]] = config.get("servers", {})
        self.permissions: dict[str, list[str]] = config.get("permissions", {})
        self.tools: dict[str, ToolRecord] = {}
        self.server_errors: dict[str, str] = {}

    def _client(self, server_id: str) -> Client:
        cfg = self.servers[server_id]
        if cfg.get("transport") == "http":
            return Client(cfg["url"])
        if cfg.get("transport") == "stdio":
            return Client(
                StdioServerParameters(
                    command=cfg["command"],
                    args=cfg.get("args", []),
                    env=cfg.get("env"),
                )
            )
        raise ValueError(f"Invalid transport for {server_id}: {cfg.get('transport')!r}")

    async def refresh(self) -> dict[str, str]:
        """Discover all tools. Offline servers produce errors, never stale grants."""
        catalog: dict[str, ToolRecord] = {}
        errors: dict[str, str] = {}
        for server_id in self.servers:
            try:
                async with self._client(server_id) as client:
                    cursor: str | None = None
                    seen_cursors: set[str] = set()
                    while True:
                        page = await client.list_tools(cursor=cursor)
                        for t in page.tools:
                            tool_id = f"{server_id}:{t.name}"
                            catalog[tool_id] = ToolRecord(
                                tool_id=tool_id,
                                server_id=server_id,
                                name=t.name,
                                description=t.description or "",
                                input_schema=t.input_schema,
                            )
                        cursor = page.next_cursor
                        if cursor is None:
                            break
                        if cursor in seen_cursors:
                            raise RuntimeError("Repeated tool-list cursor from MCP server")
                        seen_cursors.add(cursor)
            except Exception as exc:
                errors[server_id] = f"{type(exc).__name__}: {exc}"
                # Do not expose partially discovered tools from an unhealthy server.
                catalog = {k: v for k, v in catalog.items() if v.server_id != server_id}
        self.tools = catalog
        self.server_errors = errors
        return errors

    def list_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        allowed = set(self.permissions.get(agent_id, []))
        return [
            {
                "tool_id": t.tool_id,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in sorted(self.tools.values(), key=lambda x: x.tool_id)
            if t.tool_id in allowed
        ]

    async def call(
        self, agent_id: str, tool_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """In-process enforcement for trusted callers; agent_id isn't authentication."""
        if tool_id not in set(self.permissions.get(agent_id, [])):
            raise PermissionError(f"{agent_id!r} is not allowed to call {tool_id!r}")
        record = self.tools.get(tool_id)
        if record is None:
            raise KeyError(f"Tool not discovered: {tool_id} (call refresh first)")
        validate(instance=arguments, schema=record.input_schema)
        async with self._client(record.server_id) as client:
            result = await client.call_tool(record.name, arguments)
        return {
            "tool_id": tool_id,
            "is_error": bool(result.is_error),
            "structured_content": result.structured_content,
            "content": [block.model_dump(mode="json", exclude_none=True) for block in result.content],
        }
