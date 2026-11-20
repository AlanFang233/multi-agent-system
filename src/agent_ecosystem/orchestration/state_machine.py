"""Strict node status transitions.

Adapted from AgentForge@7cee900 src/agentforge/orchestration/state_machine.py,
extended with WAITING_INPUT (Phase 3 HITL reservation):
RUNNING -> WAITING_INPUT and WAITING_INPUT -> {PENDING, CANCELLED, FAILED}
(reject drives WAITING_INPUT -> FAILED so the blocked node never executes).

The scheduler main loop is the ONLY caller that mutates node status
(single-writer principle); dispatch workers return outcomes instead.
"""
from __future__ import annotations

from .errors import OrchestrationError
from .models import NodeStatus, WorkflowNode, utc_now


class InvalidNodeTransition(OrchestrationError):
    pass


ALLOWED_TRANSITIONS: dict[NodeStatus, frozenset[NodeStatus]] = {
    NodeStatus.PENDING: frozenset(
        {NodeStatus.RUNNING, NodeStatus.BLOCKED, NodeStatus.FAILED, NodeStatus.CANCELLED}
    ),
    NodeStatus.RUNNING: frozenset(
        {
            NodeStatus.COMPLETED,
            NodeStatus.FAILED,
            NodeStatus.BLOCKED,
            NodeStatus.PENDING,          # retry / crash recovery (Phase 3)
            NodeStatus.WAITING_INPUT,    # HITL suspension (Phase 3)
            NodeStatus.CANCELLED,
        }
    ),
    NodeStatus.WAITING_INPUT: frozenset(
        {NodeStatus.PENDING, NodeStatus.CANCELLED, NodeStatus.FAILED}
    ),
    NodeStatus.BLOCKED: frozenset(
        {NodeStatus.RUNNING, NodeStatus.FAILED, NodeStatus.CANCELLED}
    ),
    NodeStatus.COMPLETED: frozenset(),
    NodeStatus.FAILED: frozenset(),
    NodeStatus.CANCELLED: frozenset(),
}


def transition_node(
    node: WorkflowNode,
    new_status: NodeStatus,
    *,
    result: str | None = None,
    error: str | None = None,
) -> WorkflowNode:
    """Validate and apply one status transition; mutates and returns the node."""
    allowed = ALLOWED_TRANSITIONS.get(node.status, frozenset())
    if new_status not in allowed:
        raise InvalidNodeTransition(
            f"cannot transition node {node.node_id} from {node.status.value} "
            f"to {new_status.value}"
        )
    node.status = new_status
    if new_status == NodeStatus.RUNNING:
        if node.started_at is None:
            node.started_at = utc_now()
        node.finished_at = None
        node.error = None
    elif new_status in (NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.CANCELLED):
        node.finished_at = utc_now()
    elif new_status in (NodeStatus.BLOCKED, NodeStatus.WAITING_INPUT, NodeStatus.PENDING):
        node.finished_at = None
    if result is not None:
        node.result = result
    if error is not None:
        node.error = error
    if new_status == NodeStatus.COMPLETED and error is None:
        node.error = None
    return node
