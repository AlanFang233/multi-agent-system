"""Orchestration data models (pydantic v2, JSON-serializable).

Adapted from AgentForge@7cee900 src/agentforge/models/task.py and
models/workflow.py, trimmed to the A2A architecture and extended with a
per-node ``priority`` (scheduler tie-break) required by Phase 2.

These models are transport-agnostic: they are stored via WorkflowStore and
their JSON projections travel inside A2A message metadata (``x-workflow``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"  # reserved for Phase 3 HITL suspension
    BLOCKED = "blocked"              # reserved: parent of delegated sub-work
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_NODE_STATUSES = frozenset(
    {NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.CANCELLED}
)


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"      # reserved for Phase 3
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _clean_capabilities(values: list[str]) -> list[str]:
    cleaned = [v.strip().lower().replace(" ", "_").replace("-", "_") for v in values]
    cleaned = [v for v in cleaned if v]
    if not cleaned:
        raise ValueError("required_capabilities must contain at least one non-empty item")
    return cleaned


class SubtaskSpec(BaseModel):
    """One LLM-planned DAG node, before platform assignment/materialization."""

    model_config = {"extra": "forbid"}  # unknown fields (e.g. agent picks) fail schema

    key: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1)
    required_capabilities: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0, le=9)
    kind: str = Field(default="generic")

    @field_validator("required_capabilities")
    @classmethod
    def _normalize_caps(cls, values: list[str]) -> list[str]:
        return _clean_capabilities(values)


class ExecutionPlan(BaseModel):
    """Validated planner output; duplicate keys are rejected here, cycles in planner."""

    model_config = {"extra": "forbid"}

    goal_summary: str = Field(default="", max_length=2000)
    subtasks: list[SubtaskSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_keys(self) -> "ExecutionPlan":
        keys = [s.key for s in self.subtasks]
        if len(keys) != len(set(keys)):
            duplicates = sorted({k for k in keys if keys.count(k) > 1})
            raise ValueError(f"duplicate subtask keys: {duplicates}")
        return self


class WorkflowNode(BaseModel):
    """A materialized DAG node bound to one agent endpoint."""

    node_id: str = Field(default_factory=lambda: new_id("node"))
    workflow_id: str
    plan_key: str = ""
    description: str = Field(min_length=1)
    required_capabilities: list[str] = Field(default_factory=list)
    assigned_agent: str | None = None
    dependencies: list[str] = Field(default_factory=list)  # node_id list
    status: NodeStatus = NodeStatus.PENDING
    attempt: int = 0
    priority: int = 0
    kind: str = "generic"          # generic | synthesis
    result: str | None = None
    error: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)  # memory tool_results ids
    depth: int = Field(default=1, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_NODE_STATUSES

    def duration_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at or utc_now()
        return max(0.0, (end - self.started_at).total_seconds())


class WorkflowMetrics(BaseModel):
    """P2 subset of the AgentForge 32-field metrics; extended in later phases."""

    total_nodes: int = 0
    completed_nodes: int = 0
    failed_nodes: int = 0
    cancelled_nodes: int = 0
    node_completion_rate: float = 0.0
    max_depth: int = 0
    a2a_calls: int = 0
    retries: int = 0
    reassignments: int = 0
    plan_attempts: int = 0
    llm_calls: int = 0
    total_execution_seconds: float = 0.0
    wall_clock_seconds: float = 0.0


class Workflow(BaseModel):
    workflow_id: str = Field(default_factory=lambda: new_id("wf"))
    goal: str = Field(min_length=1)
    status: WorkflowStatus = WorkflowStatus.PENDING
    final_result: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    metrics: WorkflowMetrics = Field(default_factory=WorkflowMetrics)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return max(0.0, (self.finished_at - self.created_at).total_seconds())


class NodeEvent(BaseModel):
    """Audit line for one node/workflow decision (stored, also mirrored to JSONL)."""

    workflow_id: str
    node_id: str | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
