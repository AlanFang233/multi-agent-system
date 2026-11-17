"""Phase 2 audit: real cross-process concurrency probe."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from agent_ecosystem.orchestration.assignment import (
    AgentCapabilityCard,
    CapabilityAssigner,
    load_agent_cards,
)
from agent_ecosystem.orchestration.models import (
    ExecutionPlan,
    NodeStatus,
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
from agent_ecosystem.orchestration.service import (
    A2ANodeDispatcher,
    WorkflowService,
    load_orchestration_config,
)

PARALLEL_PLAN = [
    {"key": "research_bg", "description": "Search RAG background", "required_capabilities": ["web_search"]},
    {"key": "code_impl", "description": "Write and run a Python demo in Docker sandbox", "required_capabilities": ["sandbox_execution"]},
    {"key": "stats_analysis", "description": "Use loss-analysis skill for A=[2.5,2.0,1.6,1.2,0.9] B=[2.5,1.8,1.2,0.8,0.5]", "required_capabilities": ["statistics"]},
]


class StubPlanner:
    async def plan_goal(self, goal, catalog, assigner) -> PlanningResult:
        plan = ExecutionPlan.model_validate({"goal_summary": "probe", "subtasks": PARALLEL_PLAN})
        ordered = validate_plan(plan, max_subtasks=10, max_depth=6)
        assignments = check_capability_coverage(ordered, assigner)
        return PlanningResult(plan=plan, ordered_subtasks=ordered, assignments=assignments,
                              attempts=[{"raw_preview": "stub", "error": None}], output_mode="stub")


class ProbeDispatcher:
    def __init__(self, dispatcher=None):
        self.dispatcher = dispatcher
        self.timeline = {}
    async def __call__(self, node, workflow, agent_id, task_id):
        k = node.plan_key
        self.timeline[k] = {"enter": time.monotonic()}
        outcome = await self.dispatcher(node, workflow, agent_id, task_id)
        self.timeline[k]["exit"] = time.monotonic()
        return outcome


async def main() -> int:
    config = load_orchestration_config()
    cards = load_agent_cards()
    urls = {c.agent_id: c.url for c in cards}
    print("agent urls:", json.dumps(urls))

    def factory(wf):
        return A2ANodeDispatcher(urls, trace_id=wf.metadata.get("trace_id", wf.workflow_id),
                                 request_timeout=float(config.get("node_timeout_seconds", 300)) - 10,
                                 node_tool_budget=int(config.get("node_tool_budget", 12)),
                                 memory_database=config.get("memory_database", "data/agent_memory.sqlite"))

    service = WorkflowService(planner=StubPlanner(), config=config,
                              dispatcher_factory=factory, agent_cards=list(cards),
                              provider_kind="mock")
    # Create the workflow first so trace_id is generated properly
    wf = service.create_workflow("RAG verification and loss comparison (Stub Planner; real tool execution)")
    # Build probe wrapper pointing at the real A2A dispatcher using the workflow's trace_id
    real_dispatcher = A2ANodeDispatcher(
        urls,
        trace_id=wf.metadata.get("trace_id", wf.workflow_id),
        request_timeout=float(config.get("node_timeout_seconds", 300)) - 10,
        node_tool_budget=int(config.get("node_tool_budget", 12)),
        memory_database=config.get("memory_database", "data/agent_memory.sqlite"),
    )
    probe = ProbeDispatcher(real_dispatcher)
    service._dispatcher_factory = lambda wf: probe
    wf = await service.run_workflow(wf)

    report = {"workflow_id": wf.workflow_id, "status": wf.status.value,
              "final_result": (wf.final_result or "")[:500], "error": wf.error,
              "metrics": wf.metrics.model_dump(),
              "timeline": {k: {"enter": round(v["enter"], 3), "exit": round(v["exit"], 3),
                               "duration": round(v["exit"] - v["enter"], 3)}
                           for k, v in probe.timeline.items()},
              "nodes": []}
    nodes = service.store.list_nodes(wf.workflow_id)
    for node in nodes:
        ev = node.metadata.get("evidence") or {}
        report["nodes"].append({"plan_key": node.plan_key, "agent": node.assigned_agent,
                                "status": node.status.value, "kind": node.kind,
                                "tool_events": len(ev.get("tool_events", [])),
                                "executed_tools": ev.get("executed_tools"),
                                "result_ids": node.evidence_refs})

    out_dir = Path("data/phase2_audit")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "concurrency_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    t = probe.timeline
    plan_keys = {"research_bg", "code_impl", "stats_analysis"}
    enters = [t[k]["enter"] for k in plan_keys if k in t]
    exits = [t[k]["exit"] for k in plan_keys if k in t]
    overlap = max(enters) < min(exits) if enters else False
    print(f"OVERLAP ({'PASS' if overlap else 'FAIL'}): max_enter={max(enters):.2f} min_exit={min(exits):.2f}")
    dep_ok = "__synthesis__" in t and t["__synthesis__"]["enter"] > max(exits)
    print(f"DEPENDENCY ({'PASS' if dep_ok else 'FAIL'})")
    ev_ok = all((node.metadata.get("evidence") or {}).get("executed_tools") or node.evidence_refs
                for node in nodes if node.kind != "synthesis")
    print(f"EVIDENCE ({'PASS' if ev_ok else 'FAIL'})")
    syn = next((n for n in nodes if n.kind == "synthesis"), None)
    all_term = all(n.is_terminal for n in nodes) and syn and syn.status == NodeStatus.COMPLETED
    print(f"ALL_COMPLETE ({'PASS' if all_term else 'FAIL'})")
    passed = overlap and dep_ok and ev_ok and all_term
    print(f"PROBE_RESULT: {'PASS' if passed else 'FAIL'}")
    report["verdict"] = "PASS" if passed else "FAIL"
    (out_dir / "concurrency_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))