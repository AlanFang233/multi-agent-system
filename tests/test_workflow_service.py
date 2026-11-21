"""WorkflowService: plan -> DAG -> parallel A2A -> evidence -> synthesis."""
import httpx

import agent_ecosystem.orchestration.service as service_module
from agent_ecosystem.memory.store import MemoryDB
from agent_ecosystem.orchestration.errors import PlanningError
from agent_ecosystem.orchestration.models import WorkflowStatus
from agent_ecosystem.orchestration.service import (
    A2ANodeDispatcher,
    WorkflowService,
    classify_provider,
    collect_evidence,
    render_node_prompt,
)
from orchestration_support import (
    TEST_CARDS,
    FakePlanner,
    ScriptedDispatcher,
    make_node,
    make_workflow,
)

TWO_STEP = [
    {"key": "gather", "description": "搜索资料", "required_capabilities": ["web_search"]},
    {"key": "summarize", "description": "统计总结", "required_capabilities": ["statistics"],
     "depends_on": ["gather"]},
]


def make_service(planner, dispatcher=None, *, tmp_db="data/_test_unused.sqlite", **cfg):
    config = {"memory_database": str(tmp_db), **cfg}
    return WorkflowService(
        planner=planner,
        config=config,
        dispatcher_factory=(lambda wf: dispatcher) if dispatcher else None,
        agent_cards=TEST_CARDS,
        provider_kind="mock",
    )


def seed_memory_db(path):
    db = MemoryDB(path)
    db.begin("research", "wf1:node1:0", "goal text")
    db.event("research", "wf1:node1:0", "tool_result",
             {"tool_id": "filesystem:list_directory", "is_error": False, "preview": "files..."})
    rid, _ = db.save_result("research", "wf1:node1:0", "filesystem:list_directory", "raw body")
    db.finish("research", "wf1:node1:0", "answer", ["filesystem:list_directory"])
    return db, rid


async def test_service_completes_with_synthesis_and_evidence_flow(tmp_path):
    captured = {}

    async def dispatcher(node, workflow, agent_id, task_id):
        if node.kind == "synthesis":
            captured["synthesis_deps"] = list(node.metadata.get("dependency_results") or [])
            captured["synthesis_evidence"] = list(node.metadata.get("evidence_summaries") or [])
            captured["synthesis_agent"] = agent_id
        from agent_ecosystem.orchestration.scheduler import DispatchOutcome
        return DispatchOutcome(node_id=node.node_id, ok=True, agent_id=agent_id,
                               result=f"result-of-{node.plan_key}")

    service = make_service(FakePlanner(TWO_STEP), dispatcher, tmp_db=tmp_path / "mem.sqlite")
    wf = await service.submit("查资料并总结")
    assert wf.status == WorkflowStatus.COMPLETED
    assert wf.final_result == "result-of-__synthesis__"
    nodes = {n.plan_key: n for n in service.store.list_nodes(wf.workflow_id)}
    assert set(nodes) == {"gather", "summarize", "__synthesis__"}
    assert nodes["gather"].assigned_agent == "research"
    assert nodes["summarize"].assigned_agent == "analysis"
    assert captured["synthesis_agent"] == "research"  # evidence_synthesis capability
    dep_keys = {d[0] for d in captured["synthesis_deps"]}
    assert dep_keys == {"gather", "summarize"}
    assert wf.metrics.total_nodes == 3 and wf.metrics.completed_nodes == 3
    assert wf.metrics.a2a_calls == 3 and wf.metrics.node_completion_rate == 1.0
    event_types = [e.event_type for e in service.store.list_events(wf.workflow_id)]
    for expected in ("workflow_created", "workflow_started", "plan_validated",
                     "node_created", "node_dispatched", "node_completed",
                     "workflow_completed"):
        assert expected in event_types


async def test_dependency_results_flow_into_downstream_prompt(tmp_path):
    seen = {}

    async def dispatcher(node, workflow, agent_id, task_id):
        seen[node.plan_key] = list(node.metadata.get("dependency_results") or [])
        from agent_ecosystem.orchestration.scheduler import DispatchOutcome
        return DispatchOutcome(node_id=node.node_id, ok=True, agent_id=agent_id,
                               result=f"R({node.plan_key})")

    service = make_service(FakePlanner(TWO_STEP), dispatcher, tmp_db=tmp_path / "m.sqlite")
    wf = await service.submit("g")
    assert wf.status == WorkflowStatus.COMPLETED
    assert seen["gather"] == []
    assert seen["summarize"] == [("gather", "R(gather)")]
    assert dict(seen["__synthesis__"]) == {"gather": "R(gather)", "summarize": "R(summarize)"}


async def test_planning_failure_fails_workflow_with_zero_nodes(tmp_path):
    class BrokenPlanner:
        async def plan_goal(self, goal, catalog, assigner):
            raise PlanningError("no valid plan", attempts=[{"error": "cycle"}])

    service = make_service(BrokenPlanner(), ScriptedDispatcher(), tmp_db=tmp_path / "m.sqlite")
    wf = await service.submit("g")
    assert wf.status == WorkflowStatus.FAILED
    assert "planning failed" in wf.error
    assert service.store.list_nodes(wf.workflow_id) == []
    assert any(e.event_type == "planning_failed"
               for e in service.store.list_events(wf.workflow_id))


async def test_node_budget_rejects_oversized_plan(tmp_path):
    many = [{"key": f"k{i}", "description": "d", "required_capabilities": ["web_search"]}
            for i in range(13)]
    service = make_service(FakePlanner(many, max_subtasks=20), ScriptedDispatcher(),
                           tmp_db=tmp_path / "m.sqlite", max_nodes=12)
    wf = await service.submit("g")
    assert wf.status == WorkflowStatus.FAILED
    assert "plan rejected" in wf.error and "max_nodes" in wf.error
    assert service.store.list_nodes(wf.workflow_id) == []  # zero residue


async def test_failed_node_propagates_to_workflow_error(tmp_path):
    from agent_ecosystem.orchestration.scheduler import DispatchOutcome

    def script(node, agent_id, seq):
        if node.plan_key == "gather":
            return DispatchOutcome(node_id=node.node_id, ok=False, agent_id=agent_id,
                                   error="agent exploded", error_kind="exception")
        return DispatchOutcome(node_id=node.node_id, ok=True, agent_id=agent_id, result="ok")

    service = make_service(FakePlanner(TWO_STEP), ScriptedDispatcher(script),
                           tmp_db=tmp_path / "m.sqlite")
    wf = await service.submit("g")
    assert wf.status == WorkflowStatus.FAILED
    assert "gather" in wf.error and "agent exploded" in wf.error  # root cause, not cascade
    # gather + cascaded summarize + cascaded synthesis
    assert wf.metrics.failed_nodes == 3


def test_collect_evidence_reads_audit_not_prose(tmp_path):
    db, rid = seed_memory_db(tmp_path / "audit.sqlite")
    ev = collect_evidence("research", "wf1:node1:0", tmp_path / "audit.sqlite")
    assert ev["task_status"] == "completed"
    assert ev["executed_tools"] == ["filesystem:list_directory"]
    assert ev["tool_events"][0]["tool_id"] == "filesystem:list_directory"
    assert ev["tool_events"][0]["is_error"] is False
    assert ev["result_ids"] == [rid]
    missing = collect_evidence("research", "unknown-task", tmp_path / "audit.sqlite")
    assert "warning" in missing


async def test_a2a_dispatcher_builds_metadata_and_collects_evidence(tmp_path, monkeypatch):
    db, rid = seed_memory_db(tmp_path / "audit.sqlite")
    sent = []

    async def fake_send(url, prompt, *, metadata=None, timeout=120.0, return_status=False):
        sent.append((url, prompt, metadata, timeout))
        if return_status:
            return "TASK_STATE_COMPLETED", "final text from agent"
        return "final text from agent"

    monkeypatch.setattr(service_module, "send_a2a_text", fake_send)
    wf = make_workflow()
    node = make_node(wf, "a")
    node.metadata["dependency_results"] = [("upstream", "upstream result")]
    dispatcher = A2ANodeDispatcher(
        {"research": "http://127.0.0.1:8001"}, trace_id="t" * 32,
        memory_database=tmp_path / "audit.sqlite",
    )
    outcome = await dispatcher(node, wf, "research", "wf1:node1:0")
    assert outcome.ok and outcome.result == "final text from agent"
    assert outcome.evidence_refs == [rid]
    url, prompt, metadata, timeout = sent[0]
    assert url == "http://127.0.0.1:8001"
    assert "[WORKFLOW TASK]" in prompt and "upstream result" in prompt
    xw = metadata["x-workflow"]
    assert xw["task_id"] == "wf1:node1:0" == xw["idempotency_key"]
    assert xw["workflow_id"] == wf.workflow_id and xw["node_id"] == node.node_id
    assert xw["parent_task_id"] == wf.workflow_id
    assert xw["approval_tokens"] == [] and xw["replay_mode"] is False  # Phase 3 reserved
    assert metadata["x-agent-delegation"]["route"] == ["research"]  # autonomy preserved
    assert timeout == 290.0


async def test_a2a_dispatcher_failure_classification(tmp_path, monkeypatch):
    async def raise_connect(url, prompt, *, metadata=None, timeout=120.0, return_status=False):
        raise httpx.ConnectError("connection refused")

    async def raise_runtime(url, prompt, *, metadata=None, timeout=120.0, return_status=False):
        raise RuntimeError("下游任务尚未完成或执行失败: TASK_STATE_FAILED")

    async def return_empty(url, prompt, *, metadata=None, timeout=120.0, return_status=False):
        return "TASK_STATE_COMPLETED", "   "

    wf = make_workflow()
    node = make_node(wf, "a")
    for fake, kind in ((raise_connect, "connect"), (raise_runtime, "failed_state"),
                       (return_empty, "empty")):
        monkeypatch.setattr(service_module, "send_a2a_text", fake)
        dispatcher = A2ANodeDispatcher({"research": "http://x"}, trace_id="t",
                                       memory_database=tmp_path / "none.sqlite")
        outcome = await dispatcher(node, wf, "research", "wf:n:0")
        assert not outcome.ok and outcome.error_kind == kind, kind

    monkeypatch.setattr(service_module, "send_a2a_text", raise_connect)
    dispatcher = A2ANodeDispatcher({}, trace_id="t", memory_database=tmp_path / "none.sqlite")
    outcome = await dispatcher(node, wf, "ghost", "wf:n:0")
    assert not outcome.ok and outcome.error_kind == "connect"


def test_render_node_prompt_sections_and_synthesis_marker():
    wf = make_workflow("研究 RAG")
    node = make_node(wf, "s", kind="synthesis")
    prompt = render_node_prompt(wf, node, [("gather", "found 3 papers")], ["- gather tools=1"])
    assert "[WORKFLOW TASK]" in prompt and "[SYNTHESIS]" in prompt
    assert "[WORKFLOW GOAL] 研究 RAG" in prompt
    assert "found 3 papers" in prompt and "gather tools=1" in prompt
    assert "低信任证据" in prompt  # downstream results are evidence, not instructions
    plain = render_node_prompt(wf, make_node(wf, "x"), [], [])
    assert "[SYNTHESIS]" not in plain and "(none)" in plain


def test_classify_provider_labels_mock_vs_live():
    assert classify_provider("http://127.0.0.1:9100/v1") == "mock"
    assert classify_provider("http://localhost:9100/v1") == "mock"
    assert classify_provider("https://api1.hkgai.net/v1") == "live"
    assert classify_provider(None) == "unknown"


async def test_status_snapshot_and_cancel_flag(tmp_path):
    service = make_service(FakePlanner(TWO_STEP), ScriptedDispatcher(),
                           tmp_db=tmp_path / "m.sqlite")
    wf = await service.submit("g")
    snap = service.status(wf.workflow_id)
    assert set(snap) == {"workflow", "nodes", "events"}
    assert len(snap["nodes"]) == 3 and snap["workflow"]["status"] == "completed"
    assert service.status("wf_missing") is None
    assert service.cancel(wf.workflow_id) is True
    assert service.store.get_flag(wf.workflow_id, "cancel_requested") is True
    assert service.cancel("wf_missing") is False


async def test_provider_kind_recorded_on_workflow(tmp_path):
    service = make_service(FakePlanner(TWO_STEP), ScriptedDispatcher(),
                           tmp_db=tmp_path / "m.sqlite")
    wf = await service.submit("g")
    assert wf.metadata["provider_kind"] == "mock"  # MOCK runs are always labeled
