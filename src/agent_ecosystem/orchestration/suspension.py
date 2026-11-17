"""Workflow suspension signal: raised inside agent's tool loop when a
high-risk MCP operation needs human approval (Phase 3).

Caught by a2a_server.RoleExecutor which translates it into
TASK_STATE_INPUT_REQUIRED with the approval metadata.
"""
from __future__ import annotations


class WorkflowSuspension(Exception):
    """Signal the A2A server to suspend the current workflow node.

    The executor unwinds execution cleanly (no partial tool effects) and
    sets the A2A task to INPUT_REQUIRED with a payload that the Coordinator
    displays in the dashboard.
    """

    def __init__(self, kind: str, *, reason: str = "", details: dict | None = None) -> None:
        super().__init__(reason)
        self.kind = kind   # "approval" | "clarification"
        self.reason = reason
        self.details = details or {}