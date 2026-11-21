"""Coordinator REST API (Phase 2): submit/status/result/cancel over ASGI."""
import asyncio

import httpx

from agent_ecosystem.coordinator import create_app
from agent_ecosystem.orchestration.service import WorkflowService
from orchestration_support import TEST_CARDS, FakePlanner, ScriptedDispatcher

TWO_STEP = [
    {"key": "gather", "description": "搜索资料", "required_capabilities": ["web_search"]},
    {"key": "summarize", "description": "统计总结", "required_capabilities": ["statistics"],
     "depends_on": ["gather"]},
]


def build_client(tmp_path):
    service = WorkflowService(
        planner=FakePlanner(TWO_STEP),
        config={"memory_database": str(tmp_path / "m.sqlite")},
        dispatcher_factory=lambda wf: ScriptedDispatcher(delay=0.01),
        agent_cards=TEST_CARDS,
        provider_kind="mock",
        approvals=_StubApprovals(),
        clarifications=_StubClarifications(),
        control=_StubControl(),
    )
    app = create_app(service=service)
    return app, service


class _StubApprovals:
    def list_all(self, *a, **kw): return []
    def decide(self, *a, **kw): return None

class _StubClarifications:
    class repo:
        @staticmethod
        def list_open(*a, **kw): return []
    def answer(self, *a, **kw): return None

class _StubControl:
    def pause(self, *a): pass
    def resume(self, *a): pass
    def cancel(self, *a): pass


async def poll_until_terminal(client, wf_id, timeout=10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        r = await client.get(f"/workflows/{wf_id}")
        status = r.json()["workflow"]["status"]
        if status in ("completed", "failed", "cancelled"):
            return r.json()
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow {wf_id} did not finish in {timeout}s")


async def test_submit_poll_result_flow(tmp_path):
    app, service = build_client(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/workflows", json={"goal": "查资料并总结"})
        assert r.status_code == 202
        wf_id = r.json()["workflow_id"]
        assert r.json()["status"] == "pending"

        snap = await poll_until_terminal(client, wf_id)
        assert snap["workflow"]["status"] == "completed"
        assert len(snap["nodes"]) == 3
        assert any(e["event_type"] == "node_dispatched" for e in snap["events"])

        r = await client.get(f"/workflows/{wf_id}/result")
        assert r.status_code == 200
        assert r.json()["final_result"] == "done:__synthesis__"

        r = await client.get("/workflows")
        assert [w["workflow_id"] for w in r.json()] == [wf_id]


async def test_result_conflict_until_completed(tmp_path):
    app, service = build_client(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        wf = service.create_workflow("查资料并总结")  # created but not run
        r = await client.get(f"/workflows/{wf.workflow_id}/result")
        assert r.status_code == 409


async def test_unknown_workflow_404s(tmp_path):
    app, _ = build_client(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/workflows/wf_nope")).status_code == 404
        assert (await client.get("/workflows/wf_nope/result")).status_code == 404
        assert (await client.post("/workflows/wf_nope/cancel")).status_code == 404


async def test_invalid_goal_rejected_422(tmp_path):
    app, _ = build_client(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/workflows", json={"goal": ""})).status_code == 422
        assert (await client.post("/workflows", json={})).status_code == 422


async def test_legacy_endpoints_unchanged(tmp_path):
    app, _ = build_client(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
        assert r.json() == {"status": "ok", "service": "coordinator"}
        r = await client.get("/agents")
        assert set(r.json()) == {"research", "coding", "analysis"}


async def test_cancel_endpoint_sets_flag(tmp_path):
    app, service = build_client(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        wf = service.create_workflow("g")
        r = await client.post(f"/workflows/{wf.workflow_id}/cancel")
        assert r.status_code == 200 and r.json()["status"] == "cancel_requested"
        assert service.store.get_flag(wf.workflow_id, "cancel_requested") is True
