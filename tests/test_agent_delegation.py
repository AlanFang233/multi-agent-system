import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from agent_ecosystem.agent_delegation import AgentDelegationRegistry


class DummyMCP:
    async def refresh(self):
        return {}

    def list_for_agent(self, role):
        return [{"tool_id": "calculator:add", "description": "add", "input_schema": {"type": "object"}}]

    async def call(self, agent_id, tool_id, arguments):
        return {"tool_id": tool_id, "is_error": False, "structured_content": {"result": 5}, "content": []}


@pytest.fixture
def config_path():
    with TemporaryDirectory() as tmp:
        path = Path(tmp, "agent_registry.yaml")
        path.write_text(
            "agents:\n"
            "  research:\n    url: http://127.0.0.1:8001\n    description: research\n"
            "  coding:\n    url: http://127.0.0.1:8002\n    description: coding\n"
            "delegation:\n  research: [coding]\n  coding: []\n",
            encoding="utf-8",
        )
        yield path


def test_permission_discovery(config_path):
    rg = AgentDelegationRegistry(DummyMCP(), config_path)
    assert "a2a:delegate_task" in [t["tool_id"] for t in rg.list_for_agent("research")]
    assert "a2a:delegate_task" not in [t["tool_id"] for t in rg.list_for_agent("coding")]


def test_delegation_works(config_path):
    calls = []

    async def fake_send(url, task):
        calls.append((url, task))
        return "coding report"

    rg = AgentDelegationRegistry(DummyMCP(), config_path, sender=fake_send)
    r = asyncio.run(rg.call("research", "a2a:delegate_task", {"agent_id": "coding", "task": "Check git"}))
    assert r["structured_content"] == {"agent_id": "coding", "result": "coding report"}
    assert calls == [("http://127.0.0.1:8002", "Check git")]


def test_unauthorized(config_path):
    rg = AgentDelegationRegistry(DummyMCP(), config_path, sender=lambda *_: None)
    with pytest.raises(PermissionError):
        asyncio.run(rg.call("coding", "a2a:delegate_task", {"agent_id": "research", "task": "Hi"}))
    with pytest.raises(Exception):
        asyncio.run(rg.call("research", "a2a:delegate_task", {"agent_id": "research", "task": "Hi"}))


def test_cycle_rejected(config_path):
    txt = config_path.read_text(encoding="utf-8")
    config_path.write_text(txt.replace("  coding: []", "  coding: [research]"), encoding="utf-8")
    with pytest.raises(ValueError, match="cycle"):
        AgentDelegationRegistry(DummyMCP(), config_path)


def test_mcp_passthrough(config_path):
    rg = AgentDelegationRegistry(DummyMCP(), config_path)
    r = asyncio.run(rg.call("coding", "calculator:add", {"a": 2, "b": 3}))
    assert r["structured_content"]["result"] == 5
