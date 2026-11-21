"""A2A x-workflow metadata parsing (Phase 2) — backward compatibility and
protobuf Struct support, building on the Phase 0 _normalize_metadata fix."""
from types import SimpleNamespace

from google.protobuf import struct_pb2

from agent_ecosystem.a2a_server import _normalize_metadata, _workflow_context


def _struct(payload: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(payload)
    return s


FULL = {
    "x-agent-delegation": {"trace_id": "abc", "route": ["research"]},
    "x-workflow": {
        "workflow_id": "wf_1",
        "node_id": "node_1",
        "attempt": 2,
        "task_id": "wf_1:node_1:2",
        "idempotency_key": "wf_1:node_1:2",
        "parent_task_id": "wf_1",
        "budget": {"max_tool_calls": 12, "deadline_ts": None},
        "replay_mode": False,
        "approval_tokens": [],
    },
}


def test_workflow_context_from_plain_dict():
    ctx = _workflow_context(FULL)
    assert ctx == {"task_id": "wf_1:node_1:2", "parent_task_id": "wf_1", "max_tool_calls": 12}


def test_workflow_context_from_protobuf_struct_message():
    msg = SimpleNamespace(metadata=_struct(FULL))
    ctx = _workflow_context(_normalize_metadata(msg))
    assert ctx["task_id"] == "wf_1:node_1:2"
    assert ctx["max_tool_calls"] == 12


def test_absent_workflow_metadata_keeps_legacy_behavior():
    assert _workflow_context({}) == {}
    assert _workflow_context({"x-agent-delegation": {"route": ["research"]}}) == {}
    msg = SimpleNamespace(metadata=None)
    assert _workflow_context(_normalize_metadata(msg)) == {}


def test_invalid_workflow_metadata_is_ignored_not_crashed():
    assert _workflow_context({"x-workflow": "not-a-dict"}) == {}
    assert _workflow_context({"x-workflow": {"task_id": 42, "budget": "x"}}) == {}
    assert _workflow_context({"x-workflow": {"task_id": "", "budget": {"max_tool_calls": 0}}}) == {}
    # delegation metadata still parses alongside
    normalized = _normalize_metadata(SimpleNamespace(metadata=_struct(FULL)))
    assert normalized["x-agent-delegation"]["route"] == ["research"]
