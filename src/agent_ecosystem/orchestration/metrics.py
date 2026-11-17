"""Workflow metrics computed ONLY from persisted store state (never LLM prose).

Field naming follows AgentForge@7cee900 evaluation/metrics.py where semantics
match; A2A-specific counters (a2a_calls) are added.
"""
from __future__ import annotations

from .models import NodeStatus, Workflow, WorkflowMetrics, WorkflowNode


def compute_workflow_metrics(
    workflow: Workflow,
    nodes: list[WorkflowNode],
    *,
    a2a_calls: int = 0,
    retries: int = 0,
    reassignments: int = 0,
    plan_attempts: int = 0,
    wall_clock_seconds: float = 0.0,
) -> WorkflowMetrics:
    work = [n for n in nodes if n.kind != "root"]
    completed = [n for n in work if n.status == NodeStatus.COMPLETED]
    failed = [n for n in work if n.status == NodeStatus.FAILED]
    cancelled = [n for n in work if n.status == NodeStatus.CANCELLED]
    total_seconds = sum(n.duration_seconds() or 0.0 for n in work)
    return WorkflowMetrics(
        total_nodes=len(work),
        completed_nodes=len(completed),
        failed_nodes=len(failed),
        cancelled_nodes=len(cancelled),
        node_completion_rate=(len(completed) / len(work)) if work else 0.0,
        max_depth=max((n.depth for n in work), default=0),
        a2a_calls=a2a_calls,
        retries=retries,
        reassignments=reassignments,
        plan_attempts=plan_attempts,
        llm_calls=plan_attempts + (1 if any(n.kind == "synthesis" for n in work) else 0),
        total_execution_seconds=round(total_seconds, 3),
        wall_clock_seconds=round(wall_clock_seconds, 3),
    )


def first_root_failure(nodes: list[WorkflowNode]) -> str | None:
    """Root-cause failure: a FAILED node without FAILED dependencies (AgentForge
    _first_failure semantics), earliest first."""
    by_id = {n.node_id: n for n in nodes}
    failed = [n for n in nodes if n.status == NodeStatus.FAILED]
    roots = []
    for node in failed:
        dep_failed = any(
            by_id[d].status == NodeStatus.FAILED
            for d in node.dependencies if d in by_id
        )
        if not dep_failed and "dependency failed" not in (node.error or ""):
            roots.append(node)
    if not roots:
        roots = failed
    roots.sort(key=lambda n: (n.created_at, n.node_id))
    if not roots:
        return None
    node = roots[0]
    return f"{node.kind} node '{node.plan_key or node.node_id[:12]}' failed: {node.error}"
