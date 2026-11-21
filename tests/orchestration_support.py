"""Shared fakes/fixtures for Phase 2 orchestration tests (no LLM, no network)."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Callable

from agent_ecosystem.orchestration.assignment import (
    AgentCapabilityCard,
    CapabilityAssigner,
)
from agent_ecosystem.orchestration.models import (
    ExecutionPlan,
    Workflow,
    WorkflowNode,
    WorkflowStatus,
)
from agent_ecosystem.orchestration.planner import (
    PlanningResult,
    check_capability_coverage,
    validate_plan,
)
from agent_ecosystem.orchestration.scheduler import DispatchOutcome
from agent_ecosystem.orchestration.store import InMemoryWorkflowStore

TEST_CARDS = [
    AgentCapabilityCard("research", "research agent", ("web_search", "evidence_synthesis"), "http://127.0.0.1:8001"),
    AgentCapabilityCard("coding", "coding agent", ("sandbox_execution", "file_editing", "python_debugging"), "http://127.0.0.1:8002"),
    AgentCapabilityCard("analysis", "analysis agent", ("statistics", "numerical_analysis"), "http://127.0.0.1:8003"),
]


def make_assigner(cards: list[AgentCapabilityCard] | None = None) -> CapabilityAssigner:
    return CapabilityAssigner(cards or TEST_CARDS)


def make_plan(subtasks: list[dict]) -> ExecutionPlan:
    return ExecutionPlan.model_validate({"goal_summary": "test", "subtasks": subtasks})


def make_workflow(goal: str = "test goal") -> Workflow:
    return Workflow(goal=goal, status=WorkflowStatus.PENDING, metadata={"trace_id": "t" * 32})


def make_node(
    workflow: Workflow,
    key: str,
    *,
    deps: list[str] | None = None,
    agent: str | None = "research",
    caps: list[str] | None = None,
    kind: str = "generic",
    priority: int = 0,
) -> WorkflowNode:
    return WorkflowNode(
        workflow_id=workflow.workflow_id,
        plan_key=key,
        description=f"task {key}",
        required_capabilities=caps if caps is not None else ["web_search"],
        assigned_agent=agent,
        dependencies=list(deps or []),
        kind=kind,
        priority=priority,
    )


class ScriptedDispatcher:
    """Fake A2A dispatcher: scripted outcomes, records calls and task ids."""

    def __init__(
        self,
        script: Callable[[WorkflowNode, str, int], DispatchOutcome | Exception] | None = None,
        *,
        default_result: str = "done",
        delay: float = 0.0,
    ) -> None:
        self.script = script
        self.default_result = default_result
        self.delay = delay
        self.calls: list[tuple[str, str, str, int]] = []  # (node_key, agent, task_id, seq)
        self.task_ids: dict[str, list[str]] = {}

    async def __call__(
        self, node: WorkflowNode, workflow: Workflow, agent_id: str, task_id: str
    ) -> DispatchOutcome:
        self.calls.append((node.plan_key, agent_id, task_id, len(self.calls)))
        self.task_ids.setdefault(node.plan_key, []).append(task_id)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.script is not None:
            outcome = self.script(node, agent_id, len(self.task_ids[node.plan_key]))
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return DispatchOutcome(
            node_id=node.node_id, ok=True, agent_id=agent_id,
            result=f"{self.default_result}:{node.plan_key}",
            evidence_refs=[f"ref-{node.plan_key}"],
            evidence={"executed_tools": [], "tool_events": [], "result_ids": [f"ref-{node.plan_key}"]},
        )


class FakePlanner:
    """Bypasses the LLM; validates exactly like the real planner would."""

    def __init__(self, subtasks: list[dict], *, max_subtasks: int = 20, max_depth: int = 6) -> None:
        self.subtasks = subtasks
        self.max_subtasks = max_subtasks
        self.max_depth = max_depth
        self.calls = 0

    async def plan_goal(self, goal: str, catalog: list, assigner) -> PlanningResult:
        self.calls += 1
        plan = make_plan(self.subtasks)
        ordered = validate_plan(plan, max_subtasks=self.max_subtasks, max_depth=self.max_depth)
        assignments = check_capability_coverage(ordered, assigner)
        return PlanningResult(
            plan=plan, ordered_subtasks=ordered, assignments=assignments,
            attempts=[{"raw_preview": "fake", "error": None}], output_mode="fake",
        )


class FakeLLMClient:
    """Scripted chat.completions.create; responses are dicts or Exceptions.

    dict keys: content (str|None), arguments (str|None -> tool_call),
    finish_reason (default "stop").
    """

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    @property
    def chat(self):
        return SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("FakeLLMClient exhausted")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        finish = item.get("finish_reason", "stop")
        tool_calls = None
        if item.get("arguments") is not None:
            tool_calls = [SimpleNamespace(
                id="call_1", type="function",
                function=SimpleNamespace(name="submit_plan", arguments=item["arguments"]),
            )]
        message = SimpleNamespace(content=item.get("content"), tool_calls=tool_calls)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


def plan_json(subtasks: list[dict], goal_summary: str = "g") -> str:
    return json.dumps({"goal_summary": goal_summary, "subtasks": subtasks}, ensure_ascii=False)


def seed_store(
    nodes_spec: list[tuple[str, list[str]]],
    *,
    agent: str = "research",
    caps: list[str] | None = None,
) -> tuple[InMemoryWorkflowStore, Workflow, dict[str, WorkflowNode]]:
    """Seed store with one workflow and nodes from (key, dep_keys) pairs."""
    store = InMemoryWorkflowStore()
    workflow = make_workflow()
    store.save_workflow(workflow)
    by_key: dict[str, WorkflowNode] = {}
    for key, deps in nodes_spec:
        node = make_node(
            workflow, key,
            deps=[by_key[d].node_id for d in deps], agent=agent, caps=caps,
        )
        by_key[key] = node
        store.save_node(node)
    return store, workflow, by_key
