"""The add-on should be usable by the existing Chat Completions tool loop."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from agent_ecosystem.agent_delegation import AgentDelegationRegistry
from agent_ecosystem.tool_agent import run_tool_loop


class DummyMCP:
    async def refresh(self):
        return {}

    def list_for_agent(self, _):
        return []

    async def call(self, **kwargs):
        raise AssertionError("MCP should not be called in delegation test")


class FakeModel:
    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            functions = [s["function"] for s in kwargs["tools"]]
            delegate = next(f for f in functions if "a2a:delegate_task" in f["description"])
            msg = SimpleNamespace(content=None, tool_calls=[
                SimpleNamespace(
                    id="call1",
                    function=SimpleNamespace(
                        name=delegate["name"],
                        arguments=json.dumps({"agent_id": "coding", "task": "Check git status"}),
                    ),
                )
            ])
        else:
            assert kwargs["messages"][-1]["role"] == "tool"
            assert "coding report" in kwargs["messages"][-1]["content"]
            msg = SimpleNamespace(content="根据 Coding 报告，仓库尚无提交。", tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_a2a_tool_in_llm_loop():
    with TemporaryDirectory() as tmp:
        config = Path(tmp, "registry.yaml")
        config.write_text(
            "agents:\n  research:\n    url: http://127.0.0.1:8001\n"
            "  coding:\n    url: http://127.0.0.1:8002\n"
            "delegation:\n  research: [coding]\n  coding: []\n",
            encoding="utf-8",
        )

        async def sender(url, prompt):
            assert url == "http://127.0.0.1:8002"
            assert prompt == "Check git status"
            return "coding report"

        registry = AgentDelegationRegistry(DummyMCP(), config, sender=sender)
        client = FakeModel()
        answer, used = asyncio.run(run_tool_loop(
            role="research",
            prompt="帮我分析仓库状况，请让其他 Agent 协助",
            registry=registry, client=client, model="fake",
        ))
        assert used == ["a2a:delegate_task"]
        assert "仓库尚无提交" in answer
