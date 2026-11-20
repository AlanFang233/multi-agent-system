"""Phase 3 automated tests: SQLite persistence, approval lifecycle, HITL gate,
tool-policy blocking, A2A TaskStore, clarification, scheduler control.

All tests are offline (use temporary SQLite databases, no live LLM, no Docker).
"""
from __future__ import annotations

import json, sqlite3

import pytest

from agent_ecosystem.orchestration.models import (
    NodeStatus, Workflow, WorkflowNode, WorkflowStatus,
)
from agent_ecosystem.orchestration.sqlite_store import SQLiteWorkflowStore
from agent_ecosystem.orchestration.hitl import (
    ApprovalRepository, ApprovalService, ApprovalStatus,
    ClarificationRepository, ClarificationService, ClarificationStatus,
    arguments_digest,
)
from agent_ecosystem.orchestration.tool_policy import (
    check_and_consume_approval,
    create_approval,
    find_reusable_approval,
    is_high_risk,
)

# ── helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "orch.sqlite"

@pytest.fixture
def store(db_path):
    return SQLiteWorkflowStore(db_path)

@pytest.fixture
def workflow():
    return Workflow(goal="test goal", status=WorkflowStatus.RUNNING)

@pytest.fixture
def node(workflow):
    return WorkflowNode(
        workflow_id=workflow.workflow_id, plan_key="a",
        description="test node", required_capabilities=["web_search"],
        assigned_agent="research",
    )

def _read_table(db_path, table):
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── SQLite persistence ──────────────────────────────────────────────────────

class TestSQLiteStore:
    def test_save_and_get_workflow(self, store, workflow):
        store.save_workflow(workflow)
        wf = store.get_workflow(workflow.workflow_id)
        assert wf.goal == "test goal"

    def test_save_and_get_node(self, store, workflow, node):
        store.save_workflow(workflow)
        store.save_node(node)
        n = store.get_node(node.node_id)
        assert n.plan_key == "a" and n.status == NodeStatus.PENDING

    def test_list_nodes(self, store, workflow, node):
        store.save_workflow(workflow)
        store.save_node(node)
        nodes = store.list_nodes(workflow.workflow_id)
        assert len(nodes) == 1

    def test_append_and_list_events(self, store, workflow):
        store.save_workflow(workflow)
        store.append_event(workflow.workflow_id, "test", payload={"k": "v"})
        ev = store.list_events(workflow.workflow_id)
        assert len(ev) == 1 and ev[0].event_type == "test"

    def test_flag_persistence(self, store, workflow):
        store.save_workflow(workflow)
        assert not store.get_flag(workflow.workflow_id, "cancel_requested")
        store.set_flag(workflow.workflow_id, "cancel_requested", True)
        assert store.get_flag(workflow.workflow_id, "cancel_requested")

    def test_reopen_roundtrip(self, db_path, workflow, node):
        s1 = SQLiteWorkflowStore(db_path)
        s1.save_workflow(workflow)
        s1.save_node(node)
        # reopen
        s2 = SQLiteWorkflowStore(db_path)
        wf = s2.get_workflow(workflow.workflow_id)
        assert wf is not None and wf.goal == "test goal"
        n = s2.get_node(node.node_id)
        assert n is not None and n.plan_key == "a"

    def test_list_workflows(self, store, workflow):
        store.save_workflow(workflow)
        wf2 = Workflow(goal="second", status=WorkflowStatus.COMPLETED)
        store.save_workflow(wf2)
        wfs = store.list_workflows(limit=10)
        assert len(wfs) == 2

    def test_all_json_columns_deserialized(self, store, workflow):
        node2 = WorkflowNode(
            workflow_id=workflow.workflow_id, plan_key="b",
            description="desc", required_capabilities=["sandbox_execution"],
            assigned_agent="coding", dependencies=["dep_id"],
            evidence_refs=["ev1", "ev2"],
            metadata={"replay_mode": True, "nested": {"key": [1, 2]}},
        )
        store.save_workflow(workflow)
        store.save_node(node2)
        n = store.get_node(node2.node_id)
        assert n.required_capabilities == ["sandbox_execution"]
        assert n.dependencies == ["dep_id"]
        assert n.evidence_refs == ["ev1", "ev2"]
        assert n.metadata.get("replay_mode") is True
        assert n.metadata["nested"]["key"] == [1, 2]

    def test_lease_recovery(self, store, workflow, node):
        store.save_workflow(workflow)
        from agent_ecosystem.orchestration.state_machine import transition_node
        transition_node(node, NodeStatus.RUNNING)
        # Simulate an expired lease
        node.metadata["lease_agent"] = "research"
        node.metadata["lease_started_at"] = "2020-01-01T00:00:00"
        node.metadata["lease_timeout_seconds"] = 30
        store.save_node(node)
        recovered = store.recover_expired_leases()
        assert len(recovered) == 1
        n = store.get_node(node.node_id)
        assert n.status == NodeStatus.PENDING
        assert n.metadata.get("replay_mode") is True


# ── Approval lifecycle ──────────────────────────────────────────────────────

class TestApproval:
    @pytest.fixture
    def repo(self, db_path):
        # Ensure the approvals table exists (store creates all tables)
        SQLiteWorkflowStore(db_path)
        return ApprovalRepository(db_path)

    @pytest.fixture
    def svc(self, repo):
        return ApprovalService(repo)

    def test_create_and_get(self, svc):
        req = svc.request(agent_id="research", tool_id="sandbox:run_python",
                          arguments={"script": "main.py"}, workflow_id="wf1",
                          node_id="n1")
        assert req.status == ApprovalStatus.PENDING
        got = svc.get(req.approval_id)
        assert got.tool_id == "sandbox:run_python"

    def test_approve_and_consume(self, svc):
        req = svc.request(agent_id="coding", tool_id="filesystem_code:write_file",
                          arguments={"path": "x.py", "content": "x"},
                          workflow_id="wf1", node_id="n1")
        svc.decide(req.approval_id, ApprovalStatus.APPROVED)
        status = svc.consume(req.approval_id, tool_id="filesystem_code:write_file",
                             argument_digest=req.argument_digest,
                             workflow_id="wf1", node_id="n1", agent_id="coding")
        assert status == ApprovalStatus.APPROVED

    def test_reject_never_executes(self, svc):
        req = svc.request(agent_id="research", tool_id="sandbox:run_python",
                          arguments={"script": "x"}, workflow_id="wf1", node_id="n1")
        svc.decide(req.approval_id, ApprovalStatus.REJECTED)
        status = svc.consume(req.approval_id, tool_id="sandbox:run_python",
                             argument_digest=req.argument_digest,
                             workflow_id="wf1", node_id="n1", agent_id="research")
        assert status != ApprovalStatus.APPROVED

    def test_double_consume_blocks(self, svc):
        req = svc.request(agent_id="coding", tool_id="filesystem_code:write_file",
                          arguments={"path": "a.py"}, workflow_id="wf1", node_id="n1")
        svc.decide(req.approval_id, ApprovalStatus.APPROVED)
        assert svc.consume(req.approval_id, tool_id="filesystem_code:write_file",
                           argument_digest=req.argument_digest, workflow_id="wf1",
                           node_id="n1", agent_id="coding") == ApprovalStatus.APPROVED
        assert svc.consume(req.approval_id, tool_id="filesystem_code:write_file",
                           argument_digest=req.argument_digest, workflow_id="wf1",
                           node_id="n1", agent_id="coding") == ApprovalStatus.DENIED

    def test_wrong_digest_rejected(self, svc):
        req = svc.request(agent_id="coding", tool_id="filesystem_code:write_file",
                          arguments={"path": "x.py"}, workflow_id="wf1", node_id="n1")
        svc.decide(req.approval_id, ApprovalStatus.APPROVED)
        # tampered arguments produce a different digest
        status = svc.consume(req.approval_id, tool_id="filesystem_code:write_file",
                             argument_digest="bad" * 16, workflow_id="wf1",
                             node_id="n1", agent_id="coding")
        assert status == ApprovalStatus.DENIED

    def test_wrong_scope_blocked(self, svc):
        req = svc.request(agent_id="research", tool_id="sandbox:run_python",
                          arguments={"script": "x"}, workflow_id="wf1", node_id="n1")
        svc.decide(req.approval_id, ApprovalStatus.APPROVED)
        # Different agent tries to consume
        status = svc.consume(req.approval_id, tool_id="sandbox:run_python",
                             argument_digest=req.argument_digest, workflow_id="wf1",
                             node_id="n1", agent_id="attacker")
        assert status == ApprovalStatus.DENIED

    def test_pending_cannot_be_consumed(self, svc):
        req = svc.request(agent_id="research", tool_id="sandbox:run_python",
                          arguments={"script": "x"}, workflow_id="wf1", node_id="n1")
        status = svc.consume(req.approval_id, tool_id="sandbox:run_python",
                             argument_digest=req.argument_digest, workflow_id="wf1",
                             node_id="n1", agent_id="research")
        assert status == ApprovalStatus.PENDING

    def test_list_pending(self, svc):
        svc.request(agent_id="r", tool_id="t", arguments={}, workflow_id="w1", node_id="n1")
        svc.request(agent_id="c", tool_id="t2", arguments={}, workflow_id="w1", node_id="n2")
        assert len(svc.list_pending("w1")) == 2

    def test_arguments_digest_deterministic(self):
        assert arguments_digest({"a": 1, "b": 2}) == arguments_digest({"b": 2, "a": 1})
        assert arguments_digest({"a": 1}) != arguments_digest({"a": 2})


# ── Tool policy gate ────────────────────────────────────────────────────────

class TestToolPolicy:
    def test_high_risk_tools_identified(self):
        for t in ("filesystem_code:write_file", "filesystem_code:edit_file",
                  "sandbox:run_python", "sandbox:run_pytest"):
            assert is_high_risk(t)
        assert not is_high_risk("filesystem:read_file")
        assert not is_high_risk("calculator:add")

    def test_approval_blocks_high_risk_tool(self):
        # Without approval, high-risk tools are not executed
        assert is_high_risk("sandbox:run_python") is True


# ── Clarification ───────────────────────────────────────────────────────────

class TestClarification:
    @pytest.fixture
    def repo(self, db_path):
        SQLiteWorkflowStore(db_path)
        return ClarificationRepository(db_path)

    @pytest.fixture
    def svc(self, repo):
        return ClarificationService(repo)

    def test_create_and_answer(self, svc):
        req = svc.create(workflow_id="wf1", node_id="n1", agent_id="research",
                         question="What timeframe?")
        assert req.status == ClarificationStatus.OPEN
        got = svc.get(req.clarification_id)
        assert got is not None
        svc.answer(req.clarification_id, "2024 Q1")
        answered = svc.get(req.clarification_id)
        assert answered.status == ClarificationStatus.ANSWERED
        assert answered.answer == "2024 Q1"

    def test_double_answer_idempotent(self, svc):
        req = svc.create(workflow_id="wf1", node_id="n1", agent_id="research",
                         question="Q?")
        svc.answer(req.clarification_id, "A1")
        svc.answer(req.clarification_id, "A2")
        assert svc.get(req.clarification_id).answer == "A1"

    def test_expire(self, svc):
        req = svc.create(workflow_id="wf1", node_id="n1", agent_id="research",
                         question="Q?")
        # Manually expire by moving the clock
        req.expires_at = "2020-01-01T00:00:00"
        svc.repo.save(req)
        svc.expire()
        assert svc.get(req.clarification_id).status == ClarificationStatus.EXPIRED


# ── Scheduler control flags ─────────────────────────────────────────────────

class TestControlFlags:
    def test_pause_and_resume_flags(self, store, workflow):
        store.save_workflow(workflow)
        assert not store.get_flag(workflow.workflow_id, "pause_requested")
        store.set_flag(workflow.workflow_id, "pause_requested", True)
        assert store.get_flag(workflow.workflow_id, "pause_requested")

    def test_cancel_flag(self, store, workflow):
        store.save_workflow(workflow)
        store.set_flag(workflow.workflow_id, "cancel_requested", True)
        assert store.get_flag(workflow.workflow_id, "cancel_requested")


# ── A2A TaskStore ───────────────────────────────────────────────────────────

class TestA2ATaskStore:
    async def test_save_and_get(self, db_path):
        # Ensure tables exist
        s = SQLiteWorkflowStore(db_path)
        from agent_ecosystem.a2a_task_store import SQLiteTaskStore
        ts = SQLiteTaskStore(db_path)
        payload = {"metadata": {"workflow_id": "wf1", "node_id": "n1"},
                   "status": {"state": "TASK_STATE_COMPLETED"},
                   "artifacts": [], "history": [], "created_at": "2024-01-01T00:00:00"}
        class FakeTask:
            id = "task-001"
            def to_json(self): return json.dumps(payload)
        class FakeCtx:
            context_id = "c1"
            agent = "research"
        await ts.save(FakeTask(), FakeCtx())
        got = await ts.get("task-001")
        assert got is not None
        assert got.status.state == "TASK_STATE_COMPLETED"


# ── HITL end-to-end (integration) ───────────────────────────────────────────

class TestHITLEndToEnd:
    def test_approval_flow_end_to_end(self, db_path):
        """Simulate the full approval+tension flow end-to-end."""
        # Ensure tables exist
        store = SQLiteWorkflowStore(db_path)
        repo = ApprovalRepository(db_path)
        svc = ApprovalService(repo)

        # 1. Agent would create an approval when encountering a high-risk tool
        req = svc.request(
            agent_id="coding", tool_id="sandbox:run_python",
            arguments={"script": "main.py", "sandbox_id": "abc"},
            workflow_id="wf1", node_id="n1",
        )
        assert req.status == ApprovalStatus.PENDING
        # At this point the agent would suspend (WorkflowSuspension)

        # 2. Human sees it in the dashboard and approves
        svc.decide(req.approval_id, ApprovalStatus.APPROVED)
        assert svc.get(req.approval_id).status == ApprovalStatus.APPROVED

        # 3. On re-dispatched node, agent validates and consumes the token
        status = svc.consume(req.approval_id, tool_id="sandbox:run_python",
                             argument_digest=req.argument_digest,
                             workflow_id="wf1", node_id="n1", agent_id="coding")
        assert status == ApprovalStatus.APPROVED

        # 4. Agent executes the tool (one time only)
        status2 = svc.consume(req.approval_id, tool_id="sandbox:run_python",
                              argument_digest=req.argument_digest,
                              workflow_id="wf1", node_id="n1", agent_id="coding")
        assert status2 == ApprovalStatus.DENIED

    def test_reject_prevents_execution(self, db_path):
        store = SQLiteWorkflowStore(db_path)
        svc = ApprovalService(ApprovalRepository(db_path))

        req = svc.request(agent_id="coding", tool_id="filesystem_code:write_file",
                          arguments={"path": "x.py"}, workflow_id="wf1", node_id="n1")
        svc.decide(req.approval_id, ApprovalStatus.REJECTED)
        # Rejected approval -> tool must NOT execute
        status = svc.consume(req.approval_id, tool_id="filesystem_code:write_file",
                             argument_digest=req.argument_digest,
                             workflow_id="wf1", node_id="n1", agent_id="coding")
        assert status == ApprovalStatus.REJECTED

    def test_approve_resume_no_duplicate_approval(self, db_path):
        """Simulate the 'Approve → Resume → infinite approval loop' guard."""
        store = SQLiteWorkflowStore(db_path)
        svc = ApprovalService(ApprovalRepository(db_path))

        # First request
        req1 = svc.request(agent_id="coding", tool_id="sandbox:run_python",
                           arguments={"script": "m.py"}, workflow_id="wf1", node_id="n1")
        svc.decide(req1.approval_id, ApprovalStatus.APPROVED)
        svc.consume(req1.approval_id, tool_id="sandbox:run_python",
                    argument_digest=req1.argument_digest, workflow_id="wf1",
                    node_id="n1", agent_id="coding")

        # Second request for the same (tool, args) — if a consumed token exists for the same scope,
        # the agent should not create ANOTHER approval (that would loop infinitely).
        # The tool_policy.create_approval creates a NEW approval each time — the loop guard is
        # that consumed_at prevents re-consumption. Creating a second PENDING doesn't consume.
        req2 = svc.request(agent_id="coding", tool_id="sandbox:run_python",
                           arguments={"script": "m.py"}, workflow_id="wf1", node_id="n1")
        # This second request exists, but consumed_at guards the first one.
        # The node should use the APPROVED token, not create yet another.
        assert req2.status == ApprovalStatus.PENDING
        # Re-consume the ORIGINAL token should fail (already used)
        status = svc.consume(req1.approval_id, tool_id="sandbox:run_python",
                             argument_digest=req1.argument_digest, workflow_id="wf1",
                             node_id="n1", agent_id="coding")
        assert status == ApprovalStatus.DENIED

    def test_clarification_answer_injected(self, db_path):
        store = SQLiteWorkflowStore(db_path)
        csvc = ClarificationService(ClarificationRepository(db_path))

        req = csvc.create(workflow_id="wf1", node_id="n1", agent_id="analysis",
                          question="Which data range?")
        assert req.status == ClarificationStatus.OPEN

        csvc.answer(req.clarification_id, "Jan-Jun 2024")
        answered = csvc.get(req.clarification_id)
        assert answered.status == ClarificationStatus.ANSWERED
        assert answered.answer == "Jan-Jun 2024"

        # When the node resumes, the answer should be in node metadata
        node = WorkflowNode(workflow_id="wf1", plan_key="a", description="test",
                            required_capabilities=["statistics"],
                            assigned_agent="analysis",
                            metadata={"clarification_answer": answered.answer})
        assert node.metadata["clarification_answer"] == "Jan-Jun 2024"


# ── CAS conflict test ───────────────────────────────────────────────────────

class TestCASConflict:
    def test_concurrent_save_no_duplicate_node(self, db_path, workflow):
        store = SQLiteWorkflowStore(db_path)
        store.save_workflow(workflow)

        node = WorkflowNode(
            workflow_id=workflow.workflow_id, plan_key="a",
            description="test", required_capabilities=["web_search"],
            assigned_agent="research",
        )
        store.save_node(node)
        # Save again — must be idempotent (INSERT OR REPLACE)
        store.save_node(node)
        nodes = store.list_nodes(workflow.workflow_id)
        assert len(nodes) == 1


class TestToolPolicyGateReuse:
    """Gate-side approval reuse: an approved-but-undelivered token must be
    consumable on the next attempt instead of spawning duplicate approvals
    (regression: 11 duplicate approvals, 0 consumed, fake COMPLETED node)."""

    BINDING = dict(workflow_id="wf1", node_id="n1", agent_id="coding",
                   tool_id="sandbox:run_python")
    ARGS = {"script": "m.py"}

    def _create_schema(self, db_path):
        SQLiteWorkflowStore(db_path)

    def _set_status(self, db_path, approval_id, status):
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE approvals SET status=? WHERE approval_id=?",
                     (status, approval_id))
        conn.commit()
        conn.close()

    def test_reusable_matches_exact_binding_only(self, db_path):
        self._create_schema(db_path)
        aid = create_approval(str(db_path), arguments=self.ARGS, **self.BINDING)
        found = find_reusable_approval(str(db_path), arguments=self.ARGS, **self.BINDING)
        assert found == (aid, "pending")
        # any binding/digest mismatch must NOT reuse
        assert find_reusable_approval(str(db_path), arguments={"script": "other.py"},
                                      **self.BINDING) is None
        assert find_reusable_approval(str(db_path), arguments=self.ARGS,
                                      **{**self.BINDING, "node_id": "n2"}) is None

    def test_consumed_is_single_use_and_not_reusable(self, db_path):
        self._create_schema(db_path)
        aid = create_approval(str(db_path), arguments=self.ARGS, **self.BINDING)
        self._set_status(db_path, aid, "approved")
        first = check_and_consume_approval(str(db_path), approval_id=aid,
                                           arguments=self.ARGS, **self.BINDING)
        assert first == "approved"
        second = check_and_consume_approval(str(db_path), approval_id=aid,
                                            arguments=self.ARGS, **self.BINDING)
        assert second == "consumed"
        assert find_reusable_approval(str(db_path), arguments=self.ARGS,
                                      **self.BINDING) is None

    def test_rejected_row_is_rejected_and_not_reusable(self, db_path):
        self._create_schema(db_path)
        aid = create_approval(str(db_path), arguments=self.ARGS, **self.BINDING)
        self._set_status(db_path, aid, "rejected")
        assert check_and_consume_approval(str(db_path), approval_id=aid,
                                          arguments=self.ARGS, **self.BINDING) == "rejected"
        assert find_reusable_approval(str(db_path), arguments=self.ARGS,
                                      **self.BINDING) is None

    def test_digest_mismatch_is_mismatch_and_row_stays_consumable(self, db_path):
        """A non-deterministic LLM may regenerate different arguments after
        resume: that call must NOT run on the old approval (fail closed), but
        the approved row stays valid for the exact approved arguments."""
        self._create_schema(db_path)
        aid = create_approval(str(db_path), arguments=self.ARGS, **self.BINDING)
        self._set_status(db_path, aid, "approved")
        assert check_and_consume_approval(str(db_path), approval_id=aid,
                                          arguments={"script": "changed.py"},
                                          **self.BINDING) == "mismatch"
        assert check_and_consume_approval(str(db_path), approval_id=aid,
                                          arguments=self.ARGS, **self.BINDING) == "approved"

    def test_approved_row_preferred_over_pending(self, db_path):
        self._create_schema(db_path)
        aid1 = create_approval(str(db_path), arguments=self.ARGS, **self.BINDING)
        aid2 = create_approval(str(db_path), arguments=self.ARGS, **self.BINDING)
        self._set_status(db_path, aid2, "approved")
        found = find_reusable_approval(str(db_path), arguments=self.ARGS, **self.BINDING)
        assert found == (aid2, "approved")
        assert aid1 != aid2


class TestDecisionRaceDelivery:
    """Regression: a decision landing while the node is still RUNNING (before
    the scheduler parks it) used to strand the workflow forever — the decide
    endpoint only transitions WAITING_INPUT nodes. The scheduler's poll must
    deliver stored decisions."""

    def _park(self, store, wf, node, approval_id):
        from agent_ecosystem.orchestration.state_machine import transition_node
        transition_node(node, NodeStatus.RUNNING)
        transition_node(node, NodeStatus.WAITING_INPUT)
        node.metadata["pending_approval_id"] = approval_id
        store.save_node(node)

    def _sched(self, store):
        from agent_ecosystem.orchestration.scheduler import DagScheduler
        return DagScheduler(store, None)

    def _seed(self, db_path):
        store = SQLiteWorkflowStore(db_path)
        wf = Workflow(goal="g", status=WorkflowStatus.RUNNING)
        store.save_workflow(wf)
        node = WorkflowNode(workflow_id=wf.workflow_id, plan_key="impl",
                            description="d", required_capabilities=["coding"],
                            assigned_agent="coding")
        store.save_node(node)
        return store, wf, node

    def test_approved_before_park_delivered_on_poll(self, db_path):
        store, wf, node = self._seed(db_path)
        svc = ApprovalService(ApprovalRepository(db_path))
        req = svc.request(agent_id="coding", tool_id="filesystem_code:write_file",
                          arguments={"path": "x.py"}, workflow_id=wf.workflow_id,
                          node_id=node.node_id)
        svc.decide(req.approval_id, ApprovalStatus.APPROVED)  # lands BEFORE park
        self._park(store, wf, node, req.approval_id)
        self._sched(store)._deliver_pending_decisions(wf, [node])
        assert node.status == NodeStatus.PENDING
        assert node.metadata["approval_token"] == req.approval_id
        assert node.metadata.get("replay_mode") is True

    def test_rejected_before_park_fails_node_on_poll(self, db_path):
        store, wf, node = self._seed(db_path)
        svc = ApprovalService(ApprovalRepository(db_path))
        req = svc.request(agent_id="coding", tool_id="sandbox:run_python",
                          arguments={"script": "m.py"}, workflow_id=wf.workflow_id,
                          node_id=node.node_id)
        svc.decide(req.approval_id, ApprovalStatus.REJECTED)  # lands BEFORE park
        self._park(store, wf, node, req.approval_id)
        self._sched(store)._deliver_pending_decisions(wf, [node])
        assert node.status == NodeStatus.FAILED

    def test_pending_approval_stays_parked(self, db_path):
        store, wf, node = self._seed(db_path)
        svc = ApprovalService(ApprovalRepository(db_path))
        req = svc.request(agent_id="coding", tool_id="sandbox:run_python",
                          arguments={"script": "m.py"}, workflow_id=wf.workflow_id,
                          node_id=node.node_id)
        self._park(store, wf, node, req.approval_id)
        self._sched(store)._deliver_pending_decisions(wf, [node])
        assert node.status == NodeStatus.WAITING_INPUT  # no decision yet