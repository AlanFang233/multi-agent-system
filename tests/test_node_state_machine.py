"""Node state machine: strict transitions incl. Phase 3 HITL reservations."""
import pytest

from agent_ecosystem.orchestration.models import NodeStatus
from agent_ecosystem.orchestration.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidNodeTransition,
    transition_node,
)
from orchestration_support import make_node, make_workflow


def _node():
    return make_node(make_workflow(), "a")


def test_happy_path_sets_timestamps():
    node = _node()
    transition_node(node, NodeStatus.RUNNING)
    assert node.started_at is not None
    transition_node(node, NodeStatus.COMPLETED, result="ok")
    assert node.finished_at is not None and node.result == "ok" and node.error is None
    assert node.is_terminal


def test_cannot_skip_running():
    node = _node()
    with pytest.raises(InvalidNodeTransition):
        transition_node(node, NodeStatus.COMPLETED)


def test_terminal_states_are_final():
    for terminal in (NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.CANCELLED):
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()
    node = _node()
    transition_node(node, NodeStatus.RUNNING)
    transition_node(node, NodeStatus.FAILED, error="boom")
    with pytest.raises(InvalidNodeTransition):
        transition_node(node, NodeStatus.RUNNING)
    assert node.error == "boom"


def test_running_to_pending_allowed_for_retry_and_recovery():
    node = _node()
    transition_node(node, NodeStatus.RUNNING)
    transition_node(node, NodeStatus.PENDING)
    started_first = node.started_at
    transition_node(node, NodeStatus.RUNNING)
    assert node.started_at == started_first  # first start preserved


def test_waiting_input_reserved_transitions():
    node = _node()
    transition_node(node, NodeStatus.RUNNING)
    transition_node(node, NodeStatus.WAITING_INPUT)
    assert node.finished_at is None
    transition_node(node, NodeStatus.PENDING)  # resume path
    node2 = _node()
    transition_node(node2, NodeStatus.RUNNING)
    transition_node(node2, NodeStatus.WAITING_INPUT)
    transition_node(node2, NodeStatus.CANCELLED)
    # reject path: a blocked node awaiting approval must be able to FAIL
    node3 = _node()
    transition_node(node3, NodeStatus.RUNNING)
    transition_node(node3, NodeStatus.WAITING_INPUT)
    transition_node(node3, NodeStatus.FAILED, error="approval rejected")
    assert node3.error == "approval rejected"
    assert node3.finished_at is not None
    with pytest.raises(InvalidNodeTransition):
        transition_node(_node(), NodeStatus.WAITING_INPUT)  # PENDING -> WAITING invalid


def test_failure_records_error_and_completion_clears_it():
    node = _node()
    transition_node(node, NodeStatus.RUNNING)
    transition_node(node, NodeStatus.PENDING, error="retry later")
    assert node.error == "retry later"
    transition_node(node, NodeStatus.RUNNING)
    assert node.error is None
    transition_node(node, NodeStatus.COMPLETED, result="done")
    assert node.error is None


def test_pending_can_be_blocked_or_cancelled():
    node = _node()
    transition_node(node, NodeStatus.BLOCKED)
    transition_node(node, NodeStatus.RUNNING)
    other = _node()
    transition_node(other, NodeStatus.CANCELLED)
    assert other.is_terminal
