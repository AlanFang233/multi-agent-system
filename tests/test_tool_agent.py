from types import SimpleNamespace

import pytest

from agent_ecosystem.tool_agent import build_model_tools, run_tool_loop


class Registry:
    def __init__(self):
        self.calls = []

    def list_for_agent(self, role):
        if role != "coding":
            return []
        return [{
            "tool_id": "calculator:add", "description": "Add two integers",
            "input_schema": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]},
        }]

    async def call(self, *, agent_id, tool_id, arguments):
        self.calls.append((agent_id, tool_id, arguments))
        return {"is_error": False, "structured_content": {"result": 5}, "content": []}


class Completion:
    def __init__(self, messages):
        self.messages = messages
        self.request_log = []

    async def create(self, **kwargs):
        self.request_log.append(kwargs)
        message = self.messages.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def fake_message(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


@pytest.mark.asyncio
async def test_model_calls_mcp_and_gets_result():
    registry = Registry()
    definitions, _ = build_model_tools(registry.list_for_agent("coding"))
    name = definitions[0]["function"]["name"]
    call = SimpleNamespace(id="call_1", type="function", function=SimpleNamespace(name=name, arguments='{"a":2,"b":3}'))
    completion = Completion([fake_message(tool_calls=[call]), fake_message(content="结果是5")])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completion))
    answer, executed = await run_tool_loop(role="coding", prompt="2+3", registry=registry, client=client, model="demo")
    assert answer == "结果是5"
    assert executed == ["calculator:add"]
    assert registry.calls == [("coding", "calculator:add", {"a": 2, "b": 3})]
    assert '"result": 5' in completion.request_log[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_denied_role_cannot_force_tool():
    registry = Registry()
    completion = Completion([])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completion))
    with pytest.raises(PermissionError):
        await run_tool_loop(role="research", prompt="2+3", registry=registry, client=client, model="demo", force_tool="calculator:add")
    assert registry.calls == []
