"""Coordinator with Phase 3 HITL endpoints + workflow control.

Extends the Phase 2 REST API with approvals, clarifications, pause/resume/cancel,
and a minimal interactive web dashboard at ``/dashboard``.

Production wiring: SQLiteWorkflowStore + HITL services are created in
``build_default_service()`` and shared across the app.
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from agent_ecosystem.settings import get_settings


class GoalRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=8000)


class DecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    decided_by: str = "human"
    comment: str = ""


class AnswerRequest(BaseModel):
    answer: str = Field(min_length=1)
    answered_by: str = "human"


DASHBOARD_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>AgentForge P3 Dashboard</title>
<style>
body{font-family:system-ui,sans-serif;max-width:960px;margin:20px auto;padding:0 16px}
h1{font-size:1.4em}.card{background:#f5f5f5;border-radius:8px;padding:12px;margin:8px 0}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.85em}
.ok{background:#d4edda;color:#155724}.fail{background:#f8d7da;color:#721c24}
.warn{background:#fff3cd;color:#856404}.run{background:#cce5ff;color:#004085}
pre{background:#eee;padding:8px;border-radius:4px;overflow-x:auto;max-height:400px}
button,input{padding:6px 12px;margin:4px;border:1px solid #ccc;border-radius:4px}
h2{font-size:1.1em;margin-top:20px}
</style></head><body>
<h1>🔄 AgentForge Phase 3 Dashboard <span class="badge warn">MOCK LLM</span></h1>
<details><summary>⚙️ Submit new workflow</summary>
<div class="card">
<input id="goal" size="60" placeholder="Describe your task in natural language...">
<button onclick="submit()">Submit</button>
<p id="submitStatus"></p>
</div></details>
<details open><summary>📋 Workflows</summary>
<div id="workflows">Loading...</div></details>
<details open><summary>✅ Pending Approvals</summary>
<div id="approvals">Loading...</div></details>
<details open><summary>❓ Open Clarifications</summary>
<div id="clarifications">Loading...</div></details>
<script>
const BASE="";async function api(p,m,b){let o={};if(b){o.method=m;o.headers={"Content-Type":"application/json"};o.body=JSON.stringify(b)}
let r=await fetch(BASE+p,o);return r.json()}
async function submit(){let g=document.getElementById("goal").value;
let r=await api("/workflows","POST",{goal:g});
document.getElementById("submitStatus").innerText="Submitted: "+r.workflow_id+" ("+r.status+")";refresh()}
async function refresh(){
let w=await api("/workflows?limit=20");document.getElementById("workflows").innerHTML=
w.map(w=>`<div class="card"><b>${w.workflow_id.slice(0,16)}…</b>
<span class="badge ${w.status==='completed'?'ok':w.status==='failed'?'fail':'run'}">${w.status}</span>
${w.error?`<br><small>${w.error.slice(0,120)}</small>`:''}
<div style="margin-top:6px"><button onclick="detail('${w.workflow_id}')">Detail</button>
<button onclick="act('${w.workflow_id}','pause')">Pause</button>
<button onclick="act('${w.workflow_id}','resume')">Resume</button>
<button onclick="act('${w.workflow_id}','cancel')">Cancel</button>
<button onclick="act('${w.workflow_id}','result')">Result</button></div></div>`).join('');
let a=await api("/approvals?status=pending");document.getElementById("approvals").innerHTML=
a.map(a=>`<div class="card"><b>${a.tool_id}</b> <span class="badge warn">${a.risk_level}</span>
<pre>${a.summary.slice(0,300)}</pre>
<button onclick="decide('${a.approval_id}','approve')">Approve</button>
<button onclick="decide('${a.approval_id}','reject')">Reject</button></div>`).join('')||'(none)';
let c=await api("/clarifications?status=open");document.getElementById("clarifications").innerHTML=
c.map(c=>`<div class="card"><b>Q:</b> ${c.question}<br>
<input id="ans_${c.clarification_id}" size="40" placeholder="Your answer...">
<button onclick="answerClarify('${c.clarification_id}')">Answer</button></div>`).join('')||'(none)';}
async function detail(id){let d=await api("/workflows/"+id);alert(JSON.stringify(d,null,2).slice(0,2000))}
async function act(id,action){let r=await api("/workflows/"+id+(action==='result'?'/result':`/${action}`),action==='result'?'GET':'POST');alert(JSON.stringify(r,null,2).slice(0,2000));refresh()}
async function decide(id,decision){await api("/approvals/"+id+"/decide","POST",{decision:decision});refresh()}
async function answerClarify(id){let v=document.getElementById("ans_"+id).value;await api("/clarifications/"+id+"/answer","POST",{answer:v});refresh()}
refresh();setInterval(refresh,8000);
</script></body></html>"""


def create_app(service: Any = None) -> FastAPI:
    app = FastAPI(title="Agent Coordinator — Phase 3")
    app.state.service = service
    app.state.background_tasks = set()

    def get_service() -> Any:
        if app.state.service is None:
            from agent_ecosystem.orchestration.service import build_default_service
            app.state.service = build_default_service()
        return app.state.service

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "coordinator"}

    @app.get("/agents")
    async def agents():
        settings = get_settings()
        return {
            "research": settings.research_agent_url,
            "coding": settings.coding_agent_url,
            "analysis": settings.analysis_agent_url,
        }

    @app.get("/agents/discover")
    async def discover():
        settings = get_settings()
        urls = {
            "research": settings.research_agent_url,
            "coding": settings.coding_agent_url,
            "analysis": settings.analysis_agent_url,
        }
        result = {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            for name, base_url in urls.items():
                try:
                    response = await client.get(f"{base_url}/.well-known/agent-card.json")
                    response.raise_for_status()
                    result[name] = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    result[name] = {"error": str(exc)}
        return result

    # ── Workflow endpoints (Phase 2) ────────────────────────────────────

    @app.post("/workflows", status_code=202)
    async def submit_workflow(request: GoalRequest):
        svc = get_service()
        workflow = svc.create_workflow(request.goal)
        task = asyncio.create_task(svc.run_workflow(workflow), name=workflow.workflow_id)
        app.state.background_tasks.add(task)
        task.add_done_callback(app.state.background_tasks.discard)
        return {"workflow_id": workflow.workflow_id, "status": workflow.status.value}

    @app.get("/workflows")
    async def list_workflows(limit: int = 50, status: str | None = None):
        svc = get_service()
        workflows = await svc.store.list_workflows(limit=limit)
        if status:
            workflows = [w for w in workflows if w.status.value == status]
        return [
            {"workflow_id": w.workflow_id, "status": w.status.value,
             "goal": w.goal[:120], "created_at": w.created_at.isoformat(),
             "error": w.error}
            for w in workflows
        ]

    @app.get("/workflows/{workflow_id}")
    async def workflow_status(workflow_id: str):
        svc = get_service()
        snapshot = svc.status(workflow_id)
        if snapshot is None:
            return JSONResponse({"error": "unknown workflow"}, status_code=404)
        return snapshot

    @app.get("/workflows/{workflow_id}/result")
    async def workflow_result(workflow_id: str):
        svc = get_service()
        workflow = await svc.store.get_workflow(workflow_id)
        if workflow is None:
            return JSONResponse({"error": "unknown workflow"}, status_code=404)
        if workflow.status.value != "completed" or not workflow.final_result:
            return JSONResponse(
                {"status": workflow.status.value, "error": workflow.error}, status_code=409
            )
        return {"status": "completed", "final_result": workflow.final_result}

    @app.post("/workflows/{workflow_id}/cancel")
    async def cancel_workflow(workflow_id: str):
        svc = get_service()
        if not svc.cancel(workflow_id):
            return JSONResponse({"error": "unknown workflow"}, status_code=404)
        return {"status": "cancel_requested", "workflow_id": workflow_id}

    # ── Phase 3 control ─────────────────────────────────────────────────

    @app.post("/workflows/{workflow_id}/pause")
    async def pause_workflow(workflow_id: str):
        svc = get_service()
        if await svc.store.get_workflow(workflow_id) is None:
            return JSONResponse({"error": "unknown workflow"}, status_code=404)
        svc.control.pause(workflow_id)
        return {"status": "paused", "workflow_id": workflow_id}

    @app.post("/workflows/{workflow_id}/resume")
    async def resume_workflow(workflow_id: str):
        svc = get_service()
        if await svc.store.get_workflow(workflow_id) is None:
            return JSONResponse({"error": "unknown workflow"}, status_code=404)
        svc.control.resume(workflow_id)
        return {"status": "resumed", "workflow_id": workflow_id}

    # ── Phase 3 HITL ────────────────────────────────────────────────────

    @app.get("/approvals")
    async def list_approvals(workflow_id: str | None = None, status: str | None = "pending",
                             limit: int = 50):
        svc = get_service()
        reqs = svc.approvals.list_all(workflow_id=workflow_id, status=status, limit=limit)
        return [{
            "approval_id": r.approval_id, "workflow_id": r.workflow_id, "node_id": r.node_id,
            "agent_id": r.agent_id, "tool_id": r.tool_id, "summary": r.summary[:300],
            "risk_level": r.risk_level, "status": r.status, "decision": r.decision,
            "created_at": r.created_at, "expires_at": r.expires_at, "comment": r.comment,
        } for r in reqs]

    @app.post("/approvals/{approval_id}/decide")
    async def decide_approval(approval_id: str, body: DecisionRequest):
        svc = get_service()
        req = svc.approvals.decide(
            approval_id, body.decision, decided_by=body.decided_by, comment=body.comment,
        )
        if req is None:
            return JSONResponse({"error": "unknown approval"}, status_code=404)
        return {"approval_id": req.approval_id, "status": req.status, "decision": req.decision}

    @app.get("/clarifications")
    async def list_clarifications(workflow_id: str | None = None, status: str | None = "open",
                                  limit: int = 50):
        svc = get_service()
        reqs = svc.clarifications.repo.list_open(workflow_id)
        if status != "open":
            # re-use list_all semantics for non-open queries
            pass
        return [{
            "clarification_id": r.clarification_id, "workflow_id": r.workflow_id,
            "node_id": r.node_id, "agent_id": r.agent_id,
            "question": r.question, "status": r.status,
            "answer": r.answer, "created_at": r.created_at,
        } for r in reqs]

    @app.post("/clarifications/{clarification_id}/answer")
    async def answer_clarification(clarification_id: str, body: AnswerRequest):
        svc = get_service()
        req = svc.clarifications.answer(
            clarification_id, body.answer, answered_by=body.answered_by,
        )
        if req is None:
            return JSONResponse({"error": "unknown or already answered clarification"}, status_code=404)
        # Resume the waiting node
        node = await svc.store.get_node(req.node_id) if req.node_id else None
        if node and node.status.value == "waiting_input":
            node.metadata["clarification_answer"] = req.answer
            node.metadata["replay_mode"] = True
            await svc.store.save_node(node)
            # Transition back to PENDING so the scheduler picks it up
            # (the scheduler's main loop will re-launch it)
        return {"clarification_id": req.clarification_id, "status": req.status}

    # ── Dashboard ───────────────────────────────────────────────────────

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard():
        return DASHBOARD_HTML

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Coordinator with DAG + HITL (Phase 3)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()