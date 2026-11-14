"""Capability-based A2A delegation for trusted, local agent processes.

Requires send_a2a_text(..., metadata=...) support in a2a_delegate.py.
Delegation routes are carried in A2A message metadata, not model-written prompts.
This is an MVP for trusted loopback A2A peers, NOT a public authentication layer.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import yaml
from jsonschema import validate

Sender = Callable[..., Awaitable[str]]


class AgentDelegationRegistry:
    TOOL_ID = "a2a:delegate_task"
    DISCOVER_ID = "a2a:discover_agents"
    META_KEY = "x-agent-delegation"

    def __init__(
        self,
        mcp_registry: Any,
        config_path: str | Path = "config/agent_registry.yaml",
        *,
        sender: Sender | None = None,
        route: list[str] | tuple[str, ...] | None = None,
        trace_id: str | None = None,
    ) -> None:
        self._mcp = mcp_registry
        self._sender = sender
        data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        self.agents: dict[str, dict[str, Any]] = data.get("agents", {})
        self.policy: dict[str, Any] = data.get("policy", {})
        if not isinstance(self.agents, dict) or not self.agents:
            raise ValueError("agents must be a nonempty YAML mapping")
        if not isinstance(self.policy, dict):
            raise ValueError("policy must be a YAML mapping")
        if "delegation" in data:
            raise ValueError("Remove obsolete 'delegation' allowlist: use capabilities + policy")
        self.max_hops = int(self.policy.get("max_hops", 2))
        self.max_calls = int(self.policy.get("max_delegations_per_task", 3))
        self.timeout = float(self.policy.get("timeout_seconds", 120))
        if not (0 <= self.max_hops <= 8 and 1 <= self.max_calls <= 20 and 1 <= self.timeout <= 600):
            raise ValueError("Invalid delegation budget in policy")
        for name, cfg in self.agents.items():
            if not isinstance(name, str) or not isinstance(cfg, dict):
                raise ValueError("Invalid agent configuration")
            url = cfg.get("url")
            if not isinstance(url, str):
                raise ValueError(f"Missing URL for {name}")
            parsed = urlparse(url)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError(f"Only loopback A2A HTTP endpoints permitted: {name}")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(f"Unexpected URL fields for {name}")
            if not isinstance(cfg.get("description", name), str):
                raise ValueError(f"Invalid description for {name}")
            caps = cfg.get("capabilities", [])
            if not isinstance(caps, list) or not all(isinstance(x, str) for x in caps):
                raise ValueError(f"Invalid capabilities for {name}")
        self.route = tuple(route or ())
        if self.route:
            if any(a not in self.agents for a in self.route):
                raise ValueError("Unknown agent in route")
            if len(self.route) != len(set(self.route)):
                raise ValueError("Repeated agent in delegation route")
            if len(self.route) - 1 > self.max_hops:
                raise ValueError("Delegation route exceeds hop limit")
        self.trace_id = str(uuid.UUID(trace_id)) if trace_id else uuid.uuid4().hex
        if self.route:
            print(f"[A2A 路由上下文] trace={self.trace_id} route={' -> '.join(self.route)}", flush=True)
        self._calls = 0
        self._lock = asyncio.Lock()

    def _current_route(self, agent_id: str) -> tuple[str, ...]:
        if agent_id not in self.agents:
            raise ValueError(f"Unknown agent: {agent_id}")
        route = self.route or (agent_id,)
        if route[-1] != agent_id:
            raise PermissionError("Delegation route/current-agent mismatch")
        return route

    def _targets(self, agent_id: str) -> list[str]:
        route = self._current_route(agent_id)
        if len(route) - 1 >= self.max_hops or self._calls >= self.max_calls:
            return []
        return [name for name in self.agents if name not in route]

    async def refresh(self) -> dict[str, str]:
        return await self._mcp.refresh()

    def list_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        result = list(self._mcp.list_for_agent(agent_id))
        targets = self._targets(agent_id)
        result.append({
            "tool_id": self.DISCOVER_ID,
            "description": "Discover other registered agent capabilities; use results to decide whether delegation is justified.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        })
        if targets:
            catalog = "; ".join(
                f"{name}: {self.agents[name].get('description', name)} "
                f"(capabilities={','.join(self.agents[name].get('capabilities', []))})"
                for name in targets
            )
            result.append({
                "tool_id": self.TOOL_ID,
                "description": (
                    "Delegate only a genuinely specialized subtask to a different agent. "
                    "Choose the target based on capability, not a fixed route. "
                    "Do not send secrets or ask for unauthorized changes. "
                    f"Available agents: {catalog}"
                )[:1024],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string", "enum": targets},
                        "task": {"type": "string", "minLength": 1, "maxLength": 3000},
                    },
                    "required": ["agent_id", "task"],
                    "additionalProperties": False,
                },
            })
        return result

    async def call(self, agent_id: str, tool_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_id not in (self.TOOL_ID, self.DISCOVER_ID):
            return await self._mcp.call(agent_id=agent_id, tool_id=tool_id, arguments=arguments)
        if tool_id == self.DISCOVER_ID:
            validate(instance=arguments, schema={"type": "object", "properties": {}, "additionalProperties": False})
            targets = self._targets(agent_id)
            return {
                "tool_id": tool_id, "is_error": False, "content": [],
                "structured_content": {
                    "trace_id": self.trace_id,
                    "available_agents": [{
                        "agent_id": name,
                        "description": self.agents[name].get("description", name),
                        "capabilities": self.agents[name].get("capabilities", []),
                    } for name in targets],
                },
            }
        async with self._lock:
            targets = self._targets(agent_id)
            if not targets:
                raise PermissionError("No delegation targets remain under current route/budget")
            spec = next(s for s in self.list_for_agent(agent_id) if s["tool_id"] == self.TOOL_ID)
            validate(instance=arguments, schema=spec["input_schema"])
            target = arguments["agent_id"]
            next_route = [*self._current_route(agent_id), target]
            self._calls += 1

        endpoint = self.agents[target]["url"]
        metadata = {self.META_KEY: {"trace_id": self.trace_id, "route": next_route}}
        print(f"[A2A 动态委托] trace={self.trace_id} {' -> '.join(next_route)}", flush=True)
        if self._sender is None:
            from agent_ecosystem.a2a_delegate import send_a2a_text
            coro = send_a2a_text(endpoint, arguments["task"], metadata=metadata)
        else:
            # Compatibility with legacy two-argument injected sender test doubles.
            # Production always uses send_a2a_text and transports metadata.
            params = inspect.signature(self._sender).parameters
            if "metadata" in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
                coro = self._sender(endpoint, arguments["task"], metadata=metadata)
            else:
                coro = self._sender(endpoint, arguments["task"])
        answer = await asyncio.wait_for(coro, timeout=self.timeout)
        return {
            "tool_id": tool_id,
            "is_error": False,
            "structured_content": {"agent_id": target, "result": answer},
            "content": [],
        }
