"""Orchestration error hierarchy.

Adapted from AgentForge@7cee900 src/agentforge/orchestration/errors.py.
WorkflowPauseRequested/WorkflowCancelledRequested use exceptions as internal
control flow (caught by WorkflowService.run); they never escape to callers.
"""
from __future__ import annotations


class OrchestrationError(RuntimeError):
    """Base class for all orchestration failures."""


class PlanningError(OrchestrationError):
    """The LLM planner did not produce a usable plan within its attempt budget."""

    def __init__(self, message: str, *, attempts: list | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts or []


class PlanValidationError(PlanningError):
    """The produced plan failed program-side DAG/schema validation."""


class TaskAssignmentError(OrchestrationError):
    """No registered agent fully covers a subtask's required capabilities."""


class WorkflowCancelledRequested(OrchestrationError):
    """Raised internally to unwind the scheduler when a workflow is cancelled."""

    def __init__(self, reason: str, *, node_id: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.node_id = node_id


class WorkflowPauseRequested(OrchestrationError):
    """Reserved for Phase 3 HITL suspension (interface parity with cancel)."""

    def __init__(self, reason: str, *, node_id: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.node_id = node_id
