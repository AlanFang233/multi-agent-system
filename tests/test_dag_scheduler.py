"""DAG scheduler: TRUE parallelism (barrier-proven), dependency waiting,
cascade failure isolation, timeouts, bounded retry with stable idempotency key,
reassignment, budgets, deadline, cancel, deadlock guard, state consistency."""
import asyncio

import pytest

from agent_ecosystem.orchestration.assignment import AgentCapabilityCard, CapabilityAssigner
from agent_ecosystem.orchestration.errors import OrchestrationError
from agent_ecosystem.orchestration.models import NodeStatus
from agent_ecosystem.orchestration.scheduler import DagScheduler, DispatchOutcome
from orchestration_support import (
    ScriptedDispatcher,
    seed_store,
)


def make_scheduler(store, dispatcher, **kw):
    defaults = dict(max_concurrency=3, node_timeout=5.0, max_retries=0,
                    max_reassignments=0, max_a2a_calls=50, deadline_seconds=60.0)
    defaults.update(kw)
    return DagScheduler(store, dispatcher, **defaults)


async def test_two_independent_nodes_really_overlap_barrier():
    """Concurrency barrier: BOTH workers must be inside dispatch at the same
    time, otherwise the event wait times out and the test fails. Creating two
    asyncio tasks is NOT the evidence — actual time overlap is."""
    store, wf, nodes = seed_store([("a", []), ("b", [])])
    a_in, b_in = asyncio.Event(), asyncio.Event()
    timeline = {}

    async def dispatcher(node, workflow, agent_id, task_id):
        key = node.plan_key
        timeline[f"{key}_enter"] = asyncio.get_running_loop().time()
        if key == "a":
            a_in.set()
            await asyncio.wait_for(b_in.wait(), timeout=3.0)  # fails if serial
        else:
            b_in.set()
            await asyncio.wait_for(a_in.wait(), timeout=3.0)
        await asyncio.sleep(0.05)
        timeline[f"{key}_exit"] = asyncio.get_running_loop().time()
        return DispatchOutcome(node_id=node.node_id, ok=True, agent_id=agent_id, result="ok")

    await make_scheduler(store, dispatcher).run(wf)
    assert all(n.status == NodeStatus.COMPLETED for n in store.list_nodes(wf.workflow_id))
    # hard interval-overlap assertion on recorded timestamps
    assert timeline["a_enter"] < timeline["b_exit"]
    assert timeline["b_enter"] < timeline["a_exit"]


async def test_max_concurrency_one_serializes():
    store, wf, _ = seed_store([("a", []), ("b", []), ("c", [])])
    intervals = {}

    async def dispatcher(node, workflow, agent_id, task_id):
        start = asyncio.get_running_loop().time()
        await asyncio.sleep(0.1)
        intervals[node.plan_key] = (start, asyncio.get_running_loop().time())
        return DispatchOutcome(node_id=node.node_id, ok=True, agent_id=agent_id, result="ok")

    await make_scheduler(store, dispatcher, max_concurrency=1).run(wf)
    spans = sorted(intervals.values())
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 <= s2 + 1e-6, "serial execution violated"


async def test_dependent_node_waits_for_dependency():
    store, wf, nodes = seed_store([("a", []), ("b", ["a"])])
    order = []

    async def dispatcher(node, workflow, agent_id, task_id):
        order.append((node.plan_key, "enter"))
        await asyncio.sleep(0.05 if node.plan_key == "a" else 0)
        order.append((node.plan_key, "exit"))
        return DispatchOutcome(node_id=node.node_id, ok=True, agent_id=agent_id,
                               result=f"r-{node.plan_key}")

    await make_scheduler(store, dispatcher).run(wf)
    keys = [k for k, _ in order]
    assert keys.index("b") > keys.index("a")
    assert order[1] == ("a", "exit")
    assert nodes["b"].status == NodeStatus.COMPLETED and nodes["b"].result == "r-b"


async def test_failed_branch_cascades_but_sibling_continues():
    store, wf, nodes = seed_store([("bad", []), ("dep_of_bad", ["bad"]), ("good", [])])

    def script(node, agent_id, seq):
        if node.plan_key == "bad":
            return DispatchOutcome(node_id=node.node_id, ok=False, agent_id=agent_id,
                                   error="simulated crash", error_kind="exception")
        return DispatchOutcome(node_id=node.node_id, ok=True, agent_id=agent_id, result="fine")

    dispatcher = ScriptedDispatcher(script)
    await make_scheduler(store, dispatcher).run(wf)
    assert nodes["bad"].status == NodeStatus.FAILED
    assert nodes["dep_of_bad"].status == NodeStatus.FAILED
    assert "dependency failed" in nodes["dep_of_bad"].error
    assert nodes["good"].status == NodeStatus.COMPLETED
    dispatched = {k for k, _, _, _ in dispatcher.calls}
    assert "dep_of_bad" not in dispatched  # never ran
    assert "good" in dispatched


async def test_deep_cascade_marks_transitive_dependents():
    store, wf, nodes = seed_store([("a", []), ("b", ["a"]), ("c", ["b"]), ("d", ["c"])])

    def script(node, agent_id, seq):
        return DispatchOutcome(node_id=node.node_id, ok=node.plan_key != "a",
                               agent_id=agent_id, result="ok", error="boom",
                               error_kind="exception")

    await make_scheduler(store, ScriptedDispatcher(script)).run(wf)
    assert nodes["a"].status == NodeStatus.FAILED
    for key in ("b", "c", "d"):
        assert nodes[key].status == NodeStatus.FAILED
        assert "dependency failed" in nodes[key].error


async def test_node_timeout_marks_failure():
    store, wf, nodes = seed_store([("slow", [])])

    async def dispatcher(node, workflow, agent_id, task_id):
        await asyncio.sleep(5)
        return DispatchOutcome(node_id=node.node_id, ok=True, agent_id=agent_id, result="late")

    await make_scheduler(store, dispatcher, node_timeout=0.15).run(wf)
    assert nodes["slow"].status == NodeStatus.FAILED
    assert "timed out" in nodes["slow"].error


async def test_bounded_retry_same_agent_keeps_idempotency_key():
    store, wf, nodes = seed_store([("flaky", [])])

    def script(node, agent_id, seq):
        if seq == 1:
            return DispatchOutcome(node_id=node.node_id, ok=False, agent_id=agent_id,
                                   error="transient", error_kind="timeout")
        return DispatchOutcome(node_id=node.node_id, ok=True, agent_id=agent_id, result="ok")

    dispatcher = ScriptedDispatcher(script)
    scheduler = make_scheduler(store, dispatcher, max_retries=1)
    await scheduler.run(wf)
    assert nodes["flaky"].status == NodeStatus.COMPLETED
    assert scheduler.retries == 1
    ids = dispatcher.task_ids["flaky"]
    assert len(ids) == 2 and ids[0] == ids[1], "retry must reuse the task_id (idempotency key)"


async def test_retry_exhaustion_fails_node():
    store, wf, nodes = seed_store([("always_bad", [])])
    dispatcher = ScriptedDispatcher(
        lambda node, agent, seq: DispatchOutcome(
            node_id=node.node_id, ok=False, agent_id=agent, error="still bad",
            error_kind="timeout")
    )
    scheduler = make_scheduler(store, dispatcher, max_retries=2)
    await scheduler.run(wf)
    assert nodes["always_bad"].status == NodeStatus.FAILED
    assert len(dispatcher.calls) == 3  # initial + 2 retries, then stop
    assert scheduler.retries == 2


async def test_unreachable_agent_is_reassigned_to_capable_peer():
    store, wf, nodes = seed_store([("n", [])], agent="alpha", caps=["shared_cap"])
    cards = [
        AgentCapabilityCard("alpha", "down agent", ("shared_cap",), "http://x/alpha"),
        AgentCapabilityCard("beta", "healthy agent", ("shared_cap",), "http://x/beta"),
    ]

    def script(node, agent_id, seq):
        if agent_id == "alpha":
            return DispatchOutcome(node_id=node.node_id, ok=False, agent_id=agent_id,
                                   error="agent unreachable", error_kind="connect")
        return DispatchOutcome(node_id=node.node_id, ok=True, agent_id=agent_id, result="beta did it")

    dispatcher = ScriptedDispatcher(script)
    scheduler = make_scheduler(store, dispatcher, assigner=CapabilityAssigner(cards),
                               max_reassignments=1)
    await scheduler.run(wf)
    assert nodes["n"].status == NodeStatus.COMPLETED
    assert nodes["n"].assigned_agent == "beta"
    assert nodes["n"].attempt == 1  # reassignment rotates the idempotency key
    assert scheduler.reassignments == 1
    ids = dispatcher.task_ids["n"]
    assert ids[0] != ids[1]


async def test_no_alternate_agent_fails_after_retries():
    store, wf, nodes = seed_store([("lonely", [])])
    dispatcher = ScriptedDispatcher(
        lambda node, agent, seq: DispatchOutcome(
            node_id=node.node_id, ok=False, agent_id=agent, error="down",
            error_kind="connect")
    )
    await make_scheduler(store, dispatcher, max_retries=1).run(wf)
    assert nodes["lonely"].status == NodeStatus.FAILED


async def test_a2a_call_budget_stops_workflow():
    store, wf, nodes = seed_store([("a", []), ("b", []), ("c", []), ("d", [])])
    dispatcher = ScriptedDispatcher(delay=0.01)
    scheduler = make_scheduler(store, dispatcher, max_a2a_calls=2)
    await scheduler.run(wf)
    statuses = {k: n.status for k, n in nodes.items()}
    completed = [k for k, s in statuses.items() if s == NodeStatus.COMPLETED]
    failed = [k for k, s in statuses.items() if s == NodeStatus.FAILED]
    assert len(completed) == 2 and len(failed) == 2
    assert all("budget exceeded" in nodes[k].error for k in failed)


async def test_deadline_aborts_remaining_nodes():
    ticks = iter(range(10_000))
    store, wf, nodes = seed_store([("a", []), ("b", ["a"])])
    dispatcher = ScriptedDispatcher(delay=0.01)
    scheduler = make_scheduler(store, dispatcher, deadline_seconds=2,
                               clock=lambda: next(ticks) * 1.0)
    await scheduler.run(wf)
    assert nodes["a"].status == NodeStatus.COMPLETED or nodes["a"].status == NodeStatus.FAILED
    terminal = [n for n in store.list_nodes(wf.workflow_id) if n.is_terminal]
    assert len(terminal) == 2
    assert any("deadline exceeded" in (n.error or "") for n in terminal)


async def test_cancel_flag_stops_scheduling():
    store, wf, nodes = seed_store([("a", []), ("b", ["a"]), ("c", ["b"])])

    async def dispatcher(node, workflow, agent_id, task_id):
        store.set_flag(workflow.workflow_id, "cancel_requested", True)
        return DispatchOutcome(node_id=node.node_id, ok=True, agent_id=agent_id, result="ok")

    await make_scheduler(store, dispatcher).run(wf)
    assert nodes["a"].status == NodeStatus.COMPLETED
    assert nodes["b"].status == NodeStatus.CANCELLED
    assert nodes["c"].status == NodeStatus.CANCELLED


async def test_deadlock_guard_raises():
    store, wf, nodes = seed_store([("stuck", []), ("waiting", ["stuck"])])
    # Simulate a node stranded in a non-terminal, non-runnable state.
    from agent_ecosystem.orchestration.state_machine import transition_node
    transition_node(nodes["stuck"], NodeStatus.RUNNING)
    transition_node(nodes["stuck"], NodeStatus.WAITING_INPUT)
    store.save_node(nodes["stuck"])
    with pytest.raises(OrchestrationError, match="no progress"):
        await make_scheduler(store, ScriptedDispatcher()).run(wf)


async def test_unassigned_node_fails_without_dispatch():
    store, wf, nodes = seed_store([("orphan", [])], agent="research")
    nodes["orphan"].assigned_agent = None
    store.save_node(nodes["orphan"])
    dispatcher = ScriptedDispatcher()
    await make_scheduler(store, dispatcher).run(wf)
    assert nodes["orphan"].status == NodeStatus.FAILED
    assert "no assigned agent" in nodes["orphan"].error
    assert dispatcher.calls == []


async def test_priority_orders_ready_nodes():
    store, wf, nodes = seed_store([("low", []), ("high", []), ("mid", [])])
    nodes["low"].priority = 0
    nodes["mid"].priority = 5
    nodes["high"].priority = 9
    for n in nodes.values():
        store.save_node(n)
    order = []

    async def dispatcher(node, workflow, agent_id, task_id):
        order.append(node.plan_key)
        return DispatchOutcome(node_id=node.node_id, ok=True, agent_id=agent_id, result="ok")

    await make_scheduler(store, dispatcher, max_concurrency=1).run(wf)
    assert order == ["high", "mid", "low"]


async def test_diamond_state_consistency_under_concurrency():
    """Stress: 6 nodes, concurrency 3, one deterministic failure. Final state
    must be consistent: every COMPLETED node's deps are COMPLETED, all nodes
    terminal, completion order is topologically valid (single-writer proof)."""
    store, wf, nodes = seed_store([
        ("root", []), ("l1", ["root"]), ("bad", ["root"]), ("r1", ["root"]),
        ("l2", ["l1"]), ("join", ["l2", "r1"]),
    ])

    def script(node, agent_id, seq):
        if node.plan_key == "bad":
            return DispatchOutcome(node_id=node.node_id, ok=False, agent_id=agent_id,
                                   error="injected", error_kind="exception")
        return DispatchOutcome(node_id=node.node_id, ok=True, agent_id=agent_id,
                               result=f"ok-{node.plan_key}")

    events = []
    scheduler = make_scheduler(store, ScriptedDispatcher(script), max_retries=0,
                               event_sink=lambda name, payload: events.append((name, payload)))
    await scheduler.run(wf)
    by_key = nodes
    for key, node in by_key.items():
        assert node.is_terminal, f"{key} not terminal"
        if node.status == NodeStatus.COMPLETED:
            for dep_id in node.dependencies:
                dep = next(n for n in by_key.values() if n.node_id == dep_id)
                assert dep.status == NodeStatus.COMPLETED
    assert by_key["bad"].status == NodeStatus.FAILED
    # join depends on l2/r1 (not on bad): the unrelated branch must complete
    assert by_key["join"].status == NodeStatus.COMPLETED
    # real completion order (from the audit event stream) is topologically valid
    id_to_key = {n.node_id: k for k, n in by_key.items()}
    completed_order = [
        id_to_key[p["node_id"]] for name, p in events if name == "node_completed"
    ]
    assert completed_order.index("root") < completed_order.index("l1")
    assert completed_order.index("l1") < completed_order.index("l2")
    assert completed_order.index("l2") < completed_order.index("join")
    # dispatch events strictly precede completion events per node (single writer)
    per_node = {}
    for name, payload in events:
        per_node.setdefault(payload.get("node_id"), []).append(name)
    for nid, names in per_node.items():
        if "node_dispatched" in names and "node_completed" in names:
            assert names.index("node_dispatched") < names.index("node_completed")
