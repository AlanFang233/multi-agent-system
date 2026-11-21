"""Workflow state store: abstract interface + in-memory implementation.

Phase 2 keeps scheduling state behind this interface so Phase 3 can swap in a
SQLite-backed store (DDL already specified in docs/PHASE1_API_AND_DATA_DESIGN
§5.1) WITHOUT touching scheduler/service logic (single-writer contract: only
the WorkflowService/scheduler main loop mutates state; workers return outcomes).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import NodeEvent, Workflow, WorkflowNode


class WorkflowStore(ABC):
    """Persistence boundary for orchestration state."""

    @abstractmethod
    def save_workflow(self, workflow: Workflow) -> None: ...

    @abstractmethod
    def get_workflow(self, workflow_id: str) -> Workflow | None: ...

    @abstractmethod
    def list_workflows(self, limit: int = 50) -> list[Workflow]: ...

    @abstractmethod
    def save_node(self, node: WorkflowNode) -> None: ...

    @abstractmethod
    def get_node(self, node_id: str) -> WorkflowNode | None: ...

    @abstractmethod
    def list_nodes(self, workflow_id: str) -> list[WorkflowNode]: ...

    @abstractmethod
    def append_event(
        self, workflow_id: str, event_type: str, *,
        node_id: str | None = None, payload: dict | None = None,
    ) -> NodeEvent: ...

    @abstractmethod
    def list_events(
        self, workflow_id: str, *, event_type: str | None = None
    ) -> list[NodeEvent]: ...

    @abstractmethod
    def get_flag(self, workflow_id: str, flag: str) -> bool:
        """Cooperative control flags (cancel_requested; pause_requested in P3)."""

    @abstractmethod
    def set_flag(self, workflow_id: str, flag: str, value: bool) -> None: ...


class InMemoryWorkflowStore(WorkflowStore):
    """Dict-backed store for Phase 2 (process-local; Phase 3 adds SQLiteStore)."""

    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}
        self._nodes: dict[str, WorkflowNode] = {}
        self._events: list[NodeEvent] = []
        self._flags: dict[tuple[str, str], bool] = {}

    def save_workflow(self, workflow: Workflow) -> None:
        self._workflows[workflow.workflow_id] = workflow

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        return self._workflows.get(workflow_id)

    def list_workflows(self, limit: int = 50) -> list[Workflow]:
        items = sorted(self._workflows.values(), key=lambda w: w.created_at, reverse=True)
        return items[:limit]

    def save_node(self, node: WorkflowNode) -> None:
        self._nodes[node.node_id] = node

    def get_node(self, node_id: str) -> WorkflowNode | None:
        return self._nodes.get(node_id)

    def list_nodes(self, workflow_id: str) -> list[WorkflowNode]:
        return sorted(
            (n for n in self._nodes.values() if n.workflow_id == workflow_id),
            key=lambda n: (n.created_at, n.node_id),
        )

    def append_event(
        self, workflow_id: str, event_type: str, *,
        node_id: str | None = None, payload: dict | None = None,
    ) -> NodeEvent:
        event = NodeEvent(
            workflow_id=workflow_id, node_id=node_id,
            event_type=event_type, payload=payload or {},
        )
        self._events.append(event)
        return event

    def list_events(
        self, workflow_id: str, *, event_type: str | None = None
    ) -> list[NodeEvent]:
        return [
            e for e in self._events
            if e.workflow_id == workflow_id and (event_type is None or e.event_type == event_type)
        ]

    def get_flag(self, workflow_id: str, flag: str) -> bool:
        return self._flags.get((workflow_id, flag), False)

    def set_flag(self, workflow_id: str, flag: str, value: bool) -> None:
        self._flags[(workflow_id, flag)] = value
