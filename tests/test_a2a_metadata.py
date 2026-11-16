"""Regression: A2A delegation metadata must parse from dict AND protobuf Struct.

a2a-sdk>=1.3 delivers incoming message metadata as a google.protobuf Struct
(which lacks .get); the live research->coding->analysis delegation path broke
with AttributeError: get. _normalize_metadata must handle both shapes.
"""
from types import SimpleNamespace

from google.protobuf import struct_pb2

from agent_ecosystem.a2a_server import _normalize_metadata


def _struct(payload: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(payload)
    return s


def test_dict_metadata_passthrough():
    msg = SimpleNamespace(metadata={"x-agent-delegation": {"trace_id": "t", "route": ["research"]}})
    out = _normalize_metadata(msg)
    assert out["x-agent-delegation"]["route"] == ["research"]
    assert out["x-agent-delegation"]["trace_id"] == "t"


def test_protobuf_struct_metadata_normalized():
    msg = SimpleNamespace(metadata=_struct(
        {"x-agent-delegation": {"trace_id": "abc", "route": ["research", "coding"]}}
    ))
    out = _normalize_metadata(msg)
    assert isinstance(out, dict)
    assert out["x-agent-delegation"]["route"] == ["research", "coding"]
    assert out["x-agent-delegation"]["trace_id"] == "abc"


def test_empty_and_none_metadata_yield_empty_dict():
    assert _normalize_metadata(SimpleNamespace(metadata=None)) == {}
    assert _normalize_metadata(SimpleNamespace(metadata={})) == {}
    assert _normalize_metadata(SimpleNamespace(metadata=_struct({}))) == {}
    assert _normalize_metadata(SimpleNamespace()) == {}
