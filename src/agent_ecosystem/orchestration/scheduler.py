"""True-parallel DAG scheduler (asyncio).

Concurrency-safety contract (single writer):
- ONLY this scheduler's main loop mutates node/workflow state via the store;
- per-node worker coroutines perform pure I/O (A2A dispatch) and RETURN a
  DispatchOutcome — they never touch the store. This removes the need for
  locks and makes state transitions deterministic regardless of timing.

Semantics adapted from AgentForge@7cee900 TaskOrchestrator._run_graph
(dependency waiting, cascade failure, deadlock guard, budget checks) with the
missing piece added: real parallel execution of independent branches under a
concurrency cap (AgentForge executes strictly serially).

Retry/reassignment policy:
- timeout / downstream failure  -> bounded retry, SAME agent and SAME task_id
  (idempotency key unchanged, so an agent that actually finished meanwhile
  answers from its checkpoint instead of re-executing side effects);
- agent unreachable (connect)   -> bounded reassignment to the next
  fully-capable agent first (retrying a down agent rarely helps);
- retries and reassignments are each bounded; exhaustion -> node FAILED and
  its downstream cascade FAILED while unrelated branches keep running.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .assignment import CapabilityAssigner
from .errors import OrchestrationError
from .models import NodeStatus, Workflow, WorkflowNode
from .state_machine import transition_node
from .store import WorkflowStore


@dataclass
class DispatchOutcome:
    node_id: str
    ok: bool
    agent_id: str
    result: str = ""
    error: str = ""
    error_kind: str = ""  # "" | connect | timeout | failed_state | empty | exception
    a2a_calls: int = 1
    duration_seconds: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    evidence: dict[str, Any] | None = None


class NodeDispatcher(Protocol):
    async def __call__(
        self, node: WorkflowNode, workflow: Workflow, agent_id: str, task_id: str
    ) -> DispatchOutcome: ...


class DagScheduler:
    def __init__(
        self,
        store: WorkflowStore,
        dispatcher: NodeDispatcher,
        *,
        assigner: CapabilityAssigner | None = None,
        max_concurrency: int = 3,
        node_timeout: float = 300.0,
        max_retries: int = 1,
        max_reassignments: int = 1,
        max_a2a_calls: int = 24,
        deadline_seconds: float = 900.0,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        prepare_hook: Callable[[Workflow, WorkflowNode], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.store = store
        self.dispatcher = dispatcher
        self.assigner = assigner
        self.max_concurrency = max_concurrency
        self.node_timeout = node_timeout
        self.max_retries = max_retries
        self.max_reassignments = max_reassignments
        self.max_a2a_calls = max_a2a_calls
        self.deadline_seconds = deadline_seconds
        self._emit = event_sink or (lambda name, payload: None)
        self._prepare_hook = prepare_hook
        self._clock = clock
        # run-scoped counters (read by WorkflowService for metrics)
        self.a2a_calls = 0
        self.retries = 0
        self.reassignments = 0

    # -- public -------------------------------------------------------------

    async def run(self, workflow: Workflow) -> None:
        """Drive all nodes of one workflow to terminal states (single writer)."""
        started = self._clock()
        running: dict[str, asyncio.Task[DispatchOutcome]] = {}
        while True:
            wf_id = workflow.workflow_id
            if self.store.get_flag(wf_id, "cancel_requested"):
                await self._abort_all(running, workflow, NodeStatus.CANCELLED, "workflow cancelled")
                return
            elapsed = self._clock() - started
            if elapsed > self.deadline_seconds:
                await self._abort_all(
                    running, workflow, NodeStatus.FAILED,
                    f"workflow deadline exceeded ({elapsed:.0f}s > {self.deadline_seconds:.0f}s)",
                )
                return
            if self.a2a_calls >= self.max_a2a_calls:
                await self._abort_all(
                    running, workflow, NodeStatus.FAILED,
                    f"A2A call budget exceeded ({self.a2a_calls} >= {self.max_a2a_calls})",
                )
                return

            nodes = self.store.list_nodes(wf_id)
            by_id = {n.node_id: n for n in nodes}
            self._cascade_sweep(workflow, nodes, by_id)
            self._launch_ready(workflow, nodes, by_id, running)

            if not running:
                nonterminal = [n for n in nodes if not n.is_terminal]
                if not nonterminal:
                    return
                waiting = [n for n in nonterminal
                           if n.status in (NodeStatus.WAITING_INPUT, NodeStatus.BLOCKED)]
                if waiting:
                    # HITL suspension: nodes wait for human input; poll until the
                    # approval/clarification endpoint transitions them back to PENDING.
                    waiting_key = tuple(sorted(n.node_id for n in waiting))
                    if waiting_key != getattr(self, "_last_waiting", None):
                        self._last_waiting = waiting_key
                        self._emit("scheduler_waiting_input", {
                            "workflow_id": wf_id,
                            "waiting_nodes": [n.node_id for n in waiting],
                        })
                    await asyncio.sleep(1.5)
                    continue
                waiters = ", ".join(
                    f"{n.node_id[:8]}({n.status.value})" for n in nonterminal[:10]
                )
                raise OrchestrationError(
                    f"task graph made no progress; possible dependency deadlock: {waiters}"
                )

            done, _ = await asyncio.wait(
                set(running.values()), return_when=asyncio.FIRST_COMPLETED
            )
            finished_ids = [nid for nid, task in running.items() if task in done]
            for node_id in finished_ids:
                task = running.pop(node_id)
                outcome = task.result()
                self._apply_outcome(workflow, outcome)

    # -- main-loop internals (the ONLY place that mutates state) -------------

    def _cascade_sweep(
        self,
        workflow: Workflow,
        nodes: list[WorkflowNode],
        by_id: dict[str, WorkflowNode],
    ) -> None:
        """Fail PENDING/BLOCKED nodes whose dependencies died (idempotent).

        Covers nodes orphaned by out-of-band transitions (e.g. the HITL reject
        endpoint failing a WAITING_INPUT node directly)."""
        for node in nodes:
            if node.status not in (NodeStatus.PENDING, NodeStatus.BLOCKED):
                continue
            failed_deps = [
                by_id[d] for d in node.dependencies
                if d in by_id and by_id[d].status in (NodeStatus.FAILED, NodeStatus.CANCELLED)
            ]
            if failed_deps:
                dep_label = failed_deps[0].plan_key or failed_deps[0].node_id[:12]
                transition_node(
                    node, NodeStatus.FAILED,
                    error=f"dependency failed: {dep_label}",
                )
                self.store.save_node(node)
                self._emit("node_cascade_failed", {
                    "workflow_id": workflow.workflow_id, "node_id": node.node_id,
                    "because_of": failed_deps[0].node_id,
                })

    def _launch_ready(
        self,
        workflow: Workflow,
        nodes: list[WorkflowNode],
        by_id: dict[str, WorkflowNode],
        running: dict[str, asyncio.Task[DispatchOutcome]],
    ) -> None:
        ready = []
        for node in nodes:
            if node.status != NodeStatus.PENDING or node.node_id in running:
                continue
            deps = [by_id[d] for d in node.dependencies if d in by_id]
            if any(d.status in (NodeStatus.FAILED, NodeStatus.CANCELLED) for d in deps):
                continue  # cascade already applied by _fail_downstream
            if all(d.status == NodeStatus.COMPLETED for d in deps):
                ready.append(node)
        ready.sort(key=lambda n: (-n.priority, n.created_at, n.node_id))
        for node in ready:
            if len(running) >= self.max_concurrency:
                break
            if self.a2a_calls + len(running) >= self.max_a2a_calls:
                break  # dispatch budget exhausted; loop head will abort the rest
            if node.assigned_agent is None:
                transition_node(node, NodeStatus.FAILED, error="node has no assigned agent")
                self.store.save_node(node)
                self._fail_downstream(workflow, node)
                continue
            transition_node(node, NodeStatus.RUNNING)
            if self._prepare_hook is not None:
                # Runs in the main loop (single writer): inject dependency
                # results / evidence summaries captured so far.
                self._prepare_hook(workflow, node)
            self.store.save_node(node)
            task_id = self.task_id_for(workflow, node)
            self._emit("node_dispatched", {
                "workflow_id": workflow.workflow_id, "node_id": node.node_id,
                "plan_key": node.plan_key, "agent": node.assigned_agent,
                "task_id": task_id, "kind": node.kind,
            })
            running[node.node_id] = asyncio.create_task(
                self._run_node(workflow, node, node.assigned_agent, task_id),
                name=f"node-{node.node_id[:12]}",
            )

    def task_id_for(self, workflow: Workflow, node: WorkflowNode) -> str:
        """Idempotency key: stable across same-agent retries, changes on reassignment."""
        return f"{workflow.workflow_id}:{node.node_id}:{node.attempt}"

    async def _run_node(
        self, workflow: Workflow, node: WorkflowNode, agent_id: str, task_id: str
    ) -> DispatchOutcome:
        """Worker coroutine: pure I/O, returns an outcome, never writes state."""
        started = self._clock()
        try:
            outcome = await asyncio.wait_for(
                self.dispatcher(node, workflow, agent_id, task_id), timeout=self.node_timeout
            )
        except asyncio.TimeoutError:
            outcome = DispatchOutcome(
                node_id=node.node_id, ok=False, agent_id=agent_id,
                error=f"node timed out after {self.node_timeout:g}s", error_kind="timeout",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            outcome = DispatchOutcome(
                node_id=node.node_id, ok=False, agent_id=agent_id,
                error=f"{type(exc).__name__}: {exc}", error_kind="exception",
            )
        outcome.duration_seconds = round(self._clock() - started, 3)
        return outcome

    def _apply_outcome(self, workflow: Workflow, outcome: DispatchOutcome) -> None:
        self.a2a_calls += outcome.a2a_calls
        node = self.store.get_node(outcome.node_id)
        if node is None or node.status != NodeStatus.RUNNING:
            return  # aborted/cancelled meanwhile
        node.metadata["dispatches"] = int(node.metadata.get("dispatches", 0)) + 1
        node.metadata["last_agent"] = outcome.agent_id
        if outcome.ok:
            transition_node(node, NodeStatus.COMPLETED, result=outcome.result)
            node.evidence_refs = list(outcome.evidence_refs)
            if outcome.evidence is not None:
                node.metadata["evidence"] = outcome.evidence
            self.store.save_node(node)
            self._emit("node_completed", {
                "workflow_id": workflow.workflow_id, "node_id": node.node_id,
                "agent": outcome.agent_id, "duration_s": outcome.duration_seconds,
                "evidence_refs": len(node.evidence_refs),
                "tool_events": len((outcome.evidence or {}).get("tool_events", [])),
            })
            return

        self._emit("node_dispatch_failed", {
            "workflow_id": workflow.workflow_id, "node_id": node.node_id,
            "agent": outcome.agent_id, "error_kind": outcome.error_kind,
            "error": outcome.error[:300],
        })
        if outcome.error_kind == "input_required":
            # HITL suspension: park the node; the approve/answer endpoint will
            # transition it back to PENDING with the token/answer in metadata.
            suspension = outcome.evidence if isinstance(outcome.evidence, dict) else {}
            transition_node(node, NodeStatus.WAITING_INPUT)
            node.metadata["suspension"] = suspension
            if suspension.get("approval_id"):
                node.metadata["pending_approval_id"] = suspension["approval_id"]
            if suspension.get("clarification_id"):
                node.metadata["pending_clarification_id"] = suspension["clarification_id"]
            self.store.save_node(node)
            self._emit("node_waiting_input", {
                "workflow_id": workflow.workflow_id, "node_id": node.node_id,
                "suspend": suspension.get("suspend", "?"),
                "approval_id": suspension.get("approval_id"),
                "clarification_id": suspension.get("clarification_id"),
            })
            return
        retries = int(node.metadata.get("retries", 0))
        reassignments = int(node.metadata.get("reassignments", 0))
        alternate = (
            self._alternate_agent(node)
            if outcome.error_kind == "connect" or retries >= self.max_retries
            else None
        )
        if alternate is not None and reassignments < self.max_reassignments:
            self.reassignments += 1
            node.metadata["reassignments"] = reassignments + 1
            node.metadata.setdefault("reassigned_from", []).append(outcome.agent_id)
            node.assigned_agent = alternate
            node.attempt += 1  # new idempotency key for the new agent
            transition_node(node, NodeStatus.PENDING, error=None)
            node.error = f"reassigned from {outcome.agent_id}: {outcome.error[:200]}"
            self.store.save_node(node)
            self._emit("node_reassigned", {
                "workflow_id": workflow.workflow_id, "node_id": node.node_id,
                "from": outcome.agent_id, "to": alternate,
            })
            return
        if retries < self.max_retries:
            self.retries += 1
            node.metadata["retries"] = retries + 1
            node.metadata["retry_reason"] = outcome.error[:300]
            transition_node(node, NodeStatus.PENDING, error=None)
            node.error = None
            self.store.save_node(node)
            self._emit("node_retry_scheduled", {
                "workflow_id": workflow.workflow_id, "node_id": node.node_id,
                "agent": outcome.agent_id, "retry": retries + 1,
                "same_task_id": True,
            })
            return
        transition_node(node, NodeStatus.FAILED, error=outcome.error[:500] or "node failed")
        self.store.save_node(node)
        self._emit("node_failed", {
            "workflow_id": workflow.workflow_id, "node_id": node.node_id,
            "agent": outcome.agent_id, "error": outcome.error[:300],
        })
        self._fail_downstream(workflow, node)

    def _alternate_agent(self, node: WorkflowNode) -> str | None:
        if self.assigner is None or not node.required_capabilities:
            return None
        tried = set(node.metadata.get("reassigned_from", []) or [])
        if node.assigned_agent:
            tried.add(node.assigned_agent)
        for candidate in self.assigner.candidates(node.required_capabilities):
            if candidate.score >= 1.0 and candidate.card.agent_id not in tried:
                return candidate.card.agent_id
        return None

    def _fail_downstream(self, workflow: Workflow, failed: WorkflowNode) -> None:
        """Cascade-fail transitive dependents that are not running/terminal."""
        nodes = self.store.list_nodes(workflow.workflow_id)
        dependents: dict[str, list[str]] = {}
        for n in nodes:
            for dep in n.dependencies:
                dependents.setdefault(dep, []).append(n.node_id)
        by_id = {n.node_id: n for n in nodes}
        queue = list(dependents.get(failed.node_id, []))
        seen: set[str] = set()
        while queue:
            nid = queue.pop(0)
            if nid in seen:
                continue
            seen.add(nid)
            node = by_id.get(nid)
            if node is None or node.is_terminal or node.status == NodeStatus.RUNNING:
                continue
            transition_node(
                node, NodeStatus.FAILED,
                error=f"dependency failed: {failed.node_id[:12]}",
            )
            self.store.save_node(node)
            self._emit("node_cascade_failed", {
                "workflow_id": workflow.workflow_id, "node_id": nid,
                "because_of": failed.node_id,
            })
            queue.extend(dependents.get(nid, []))

    async def _abort_all(
        self,
        running: dict[str, asyncio.Task[DispatchOutcome]],
        workflow: Workflow,
        status: NodeStatus,
        reason: str,
    ) -> None:
        for task in running.values():
            task.cancel()
        if running:
            await asyncio.gather(*running.values(), return_exceptions=True)
        running.clear()
        for node in self.store.list_nodes(workflow.workflow_id):
            if node.is_terminal:
                continue
            if node.status == NodeStatus.RUNNING:
                transition_node(node, status, error=reason)
            elif node.status == NodeStatus.WAITING_INPUT:
                transition_node(node, NodeStatus.CANCELLED, error=reason)
            else:  # PENDING / BLOCKED
                target = (
                    NodeStatus.CANCELLED if status == NodeStatus.CANCELLED else NodeStatus.FAILED
                )
                transition_node(node, target, error=reason)
            self.store.save_node(node)
        self._emit("scheduler_aborted", {
            "workflow_id": workflow.workflow_id, "reason": reason,
        })
