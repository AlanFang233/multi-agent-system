"""Coordinator with Phase 3 HITL endpoints + workflow control.

Extends the Phase 2 REST API with approvals, clarifications, pause/resume/cancel,
and a minimal interactive web dashboard at ``/dashboard``.

Production wiring: SQLiteWorkflowStore + HITL services are created in
``build_default_service()`` and shared across the app.
"""
from __future__ import annotations

import argparse
import asyncio
import json
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
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentForge P3 Dashboard</title>
<style>
*{box-sizing:border-box}body{font-family:system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:12px;background:#f8f9fa}
.banner{background:#fff3cd;color:#856404;padding:8px 16px;border-radius:6px;text-align:center;font-weight:bold;margin-bottom:12px}
h1{font-size:1.3em;margin:0 0 8px 0}.card{background:#fff;border-radius:8px;padding:14px;margin:8px 0;box-shadow:0 1px 3px rgba(0,0,0,.1)}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.82em;font-weight:600}
.ok{background:#d4edda;color:#155724}.fail{background:#f8d7da;color:#721c24}
.warn{background:#fff3cd;color:#856404}.run{background:#cce5ff;color:#004085}.wait{background:#e2d5f1;color:#5a3d7a}
pre{background:#f1f3f5;padding:8px;border-radius:4px;overflow-x:auto;max-height:300px;font-size:.85em}
button,input,select{padding:6px 12px;margin:3px;border:1px solid #ccc;border-radius:4px;font-size:.9em;cursor:pointer}
button:hover{opacity:.9}button.danger{background:#dc3545;color:#fff;border-color:#dc3545}
button.ok{background:#28a745;color:#fff;border-color:#28a745}
button.warn{background:#ffc107;border-color:#ffc107}
details{margin:6px 0}summary{cursor:pointer;font-weight:600;margin-bottom:4px}
.row{display:flex;gap:12px;flex-wrap:wrap}.col{flex:1;min-width:280px}
.dag{margin:6px 0 0 20px;border-left:2px solid #dee2e6;padding-left:12px}
.dag-node{padding:4px 8px;margin:2px 0;border-radius:4px;font-size:.85em;display:flex;align-items:center;gap:8px}
.dag-node .key{font-weight:600;min-width:100px}
h2{font-size:1.05em;margin:16px 0 6px 0}hr{border:0;border-top:1px solid #dee2e6;margin:12px 0}
</style></head><body>
<div class="banner" id="modeBanner">🟡 MOCK MODE — All LLM decisions are mock-scripted. Tool execution is real.</div>
<h1>🔄 AgentForge Phase 3 Dashboard</h1>
<div class="row">
<div class="col"><div class="card">
<h2>📝 Submit Task</h2>
<input id="goal" size="50" placeholder="Describe your task...">
<button onclick="submit()">Submit</button><div id="submitStatus" style="margin-top:4px;font-size:.85em"></div>
</div><div class="card">
<h2>📋 Workflows</h2><div id="workflows">Loading...</div>
</div></div>
<div class="col"><div class="card">
<h2>✅ Approvals</h2><div id="approvals">Loading...</div>
</div><div class="card">
<h2>❓ Clarifications</h2><div id="clarifications">Loading...</div>
</div></div>
</div>
<div id="detailPanel"></div>
<script>
const B="";let S="";
async function api(p,m,b){let o={};if(b){o.method=m;o.headers={"Content-Type":"application/json"};o.body=JSON.stringify(b)}
let r=await fetch(B+p,o);return r.headers.get("content-type")||"".includes("html")?r.text():r.json()}
async function submit(){let g=document.getElementById("goal").value;
let r=await api("/workflows","POST",{goal:g});
document.getElementById("submitStatus").innerText="Submitted: "+r.workflow_id+" ("+r.status+")";refresh()}
async function act(id,action){await api("/workflows/"+id+(action==='result'?'/result':`/${action}`),action==='result'?'GET':'POST');refresh()}
async function decide(id,decision){await api("/approvals/"+id+"/decide","POST",{decision:decision});refresh()}
async function answerClarify(id){let v=document.getElementById("ans_"+id).value;await api("/clarifications/"+id+"/answer","POST",{answer:v});refresh()}
async function showDetail(id){S=id;refresh()}
function dagTree(nodes,pid){
  let ch=nodes.filter(n=>n.dependencies&&n.dependencies.includes(pid));
  if(!ch.length)return"";return ch.map(n=>{
    let b=statusBadge(n.status);
    return'<div class="dag"><div class="dag-node"><span class="key">🔹 '+(n.plan_key||n.node_id.slice(0,10))+'</span>'+
      '<span>'+b+'</span><span>🤖 '+(n.assigned_agent||"—")+'</span>'+(n.result?('<span style="font-size:.75em;color:#666">'+n.result.slice(0,60)+'</span>'):'')+
      '</div>'+dagTree(nodes,n.node_id)+'</div>';
  }).join("");}
function statusBadge(s){let c=s==="completed"?"ok":s==="failed"?"fail":s==="running"?"run":s==="waiting_input"?"wait":"warn";return'<span class="badge '+c+'">'+s+'</span>';}
async function refresh(){
  document.getElementById("modeBanner").innerText="🟡 MOCK MODE — All LLM decisions are mock-scripted. Tool execution is real.";
  let w=await api("/workflows?limit=20");let h="";(w||[]).forEach(wf=>{h+=`<div class="card"><b>${wf.workflow_id.slice(0,18)}…</b> ${statusBadge(wf.status)}<div style="font-size:.82em;color:#555;margin:2px 0">${wf.goal.slice(0,100)}</div><button onclick="showDetail('${wf.workflow_id}')">🔍 Detail</button> <button onclick="act('${wf.workflow_id}','pause')">⏸</button> <button onclick="act('${wf.workflow_id}','resume')">▶</button> <button class="danger" onclick="act('${wf.workflow_id}','cancel')">✕</button>${wf.status==='completed'?' <button onclick="act(\''+wf.workflow_id+'\',\'result\')">📄</button>':''}${wf.error?`<br><small style="color:red">${wf.error.slice(0,150)}</small>`:''}</div>`;if(wf.workflow_id===S){(async()=>{let d=await api("/workflows/"+wf.workflow_id);let nodes=d.nodes||[],roots=nodes.filter(n=>!(n.dependencies||[]).length);let tree=roots.map(r=>'<div class="dag"><div class="dag-node"><span class="key">📌 '+(r.plan_key||r.node_id.slice(0,10))+'</span>'+statusBadge(r.status)+'<span>🤖 '+(r.assigned_agent||"—")+'</span>'+(r.kind==="synthesis"?'<span>🔗SYNTHESIS</span>':'')+'</div>'+dagTree(nodes,r.node_id)+'</div>').join("");let ev=(d.events||[]).slice(-8).map(e=>'<div style="font-size:.75em;color:#888">'+e.event_type+'</div>').join("");let res=wf.status==="completed"&&d.workflow.final_result?'<pre>'+d.workflow.final_result.slice(0,1500)+'</pre>':'';document.getElementById("detailPanel").innerHTML='<div class="card"><b>DAG</b><div style="max-height:350px;overflow-y:auto">'+(tree||"(no nodes)")+'</div>'+res+'<details><summary>Events</summary>'+ev+'</details></div>';})();}});document.getElementById("workflows").innerHTML=h||"(none)";
  let a=await api("/approvals?status=pending");let ah="";(a||[]).forEach(r=>{ah+=`<div class="card"><b>${r.tool_id}</b> <span class="badge warn">${r.risk_level}</span><pre>${r.summary.slice(0,250)}</pre><button class="ok" onclick="decide('${r.approval_id}','approve')">✓ Approve</button> <button class="danger" onclick="decide('${r.approval_id}','reject')">✕ Reject</button></div>`;});document.getElementById("approvals").innerHTML=ah||"(none)";
  let c=await api("/clarifications?status=open");let ch="";(c||[]).forEach(r=>{ch+=`<div class="card"><b>Q:</b> ${r.question}<br><input id="ans_${r.clarification_id}" size="35"><button onclick="answerClarify('${r.clarification_id}')">Answer</button></div>`;});document.getElementById("clarifications").innerHTML=ch||"(none)";}
refresh();setInterval(refresh,6000);
</script></body></html>"""


def _group_service(app: FastAPI, base_svc: Any, group_id: str) -> Any:
    """Build (and cache) a WorkflowService scoped to one group's dynamic agents.

    Shares the base service's store/approvals/clarifications/control/planner so
    all workflows remain visible in one place; only the agent catalog (and thus
    capability assignment + dispatch URLs) is group-specific."""
    cache = app.state.group_services
    if group_id in cache:
        return cache[group_id]
    from agent_ecosystem.orchestration.assignment import AgentCapabilityCard
    from agent_ecosystem.orchestration.group_service import GroupService
    from agent_ecosystem.orchestration.service import WorkflowService

    gs = GroupService()
    group = gs.get_group(group_id)
    if group is None or not group.agents:
        return None
    urls = gs.get_agent_urls(group_id)
    cards = [
        AgentCapabilityCard(
            agent_id=a.agent_id, description=a.role_name,
            capabilities=tuple(a.capabilities), url=urls.get(a.agent_id, ""),
        )
        for a in group.agents if a.agent_id in urls
    ]
    if not cards:
        return None
    config = dict(base_svc.config)
    config["synthesis_capability"] = "evidence_synthesis"
    svc = WorkflowService(
        planner=base_svc.planner, config=config, store=base_svc.store,
        agent_cards=cards, events=base_svc.events,
        provider_kind=base_svc.provider_kind,
        approvals=base_svc.approvals, clarifications=base_svc.clarifications,
        control=base_svc.control,
    )
    cache[group_id] = svc
    return svc


def create_app(service: Any = None) -> FastAPI:
    app = FastAPI(title="Agent Coordinator — Phase 3")
    app.state.service = service
    app.state.background_tasks = set()
    app.state.active_tasks = {}
    app.state.group_services = {}

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
        app.state.active_tasks[workflow.workflow_id] = task
        task.add_done_callback(
            lambda t, wid=workflow.workflow_id: app.state.active_tasks.pop(wid, None)
        )
        return {"workflow_id": workflow.workflow_id, "status": workflow.status.value}

    @app.get("/workflows")
    async def list_workflows(limit: int = 50, status: str | None = None):
        svc = get_service()
        workflows = svc.store.list_workflows(limit=limit)
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
        workflow = svc.store.get_workflow(workflow_id)
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
        if svc.store.get_workflow(workflow_id) is None:
            return JSONResponse({"error": "unknown workflow"}, status_code=404)
        svc.control.pause(workflow_id)
        return {"status": "paused", "workflow_id": workflow_id}

    @app.post("/workflows/{workflow_id}/resume")
    async def resume_workflow(workflow_id: str):
        svc = get_service()
        if svc.store.get_workflow(workflow_id) is None:
            return JSONResponse({"error": "unknown workflow"}, status_code=404)
        svc.control.resume(workflow_id)
        # Clearing the flag alone is not enough after a coordinator restart:
        # re-spawn the scheduler loop if no live task drives this workflow.
        wsvc = _service_for_workflow(svc, workflow_id)
        _ensure_workflow_running(wsvc, workflow_id)
        return {"status": "resumed", "workflow_id": workflow_id}

    # ── Phase 3 HITL ────────────────────────────────────────────────────

    def _ensure_workflow_running(svc: Any, workflow_id: str) -> None:
        """(Re)start the scheduler loop for a non-terminal workflow if no live
        background task exists (covers coordinator restarts and HITL resumes)."""
        wf = svc.store.get_workflow(workflow_id)
        if wf is None or wf.status.value in ("completed", "failed", "cancelled"):
            return
        existing = app.state.active_tasks.get(workflow_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(svc.resume(workflow_id), name=f"resume-{workflow_id}")
        app.state.active_tasks[workflow_id] = task
        task.add_done_callback(
            lambda t, wid=workflow_id: app.state.active_tasks.pop(wid, None)
        )

    def _service_for_workflow(svc: Any, workflow_id: str) -> Any:
        """Return the service that can dispatch this workflow's nodes: the
        group-scoped service for group workflows, otherwise the default one."""
        wf = svc.store.get_workflow(workflow_id)
        group_id = (wf.metadata or {}).get("group_id") if wf else None
        if not group_id:
            return svc
        return _group_service(app, svc, group_id) or svc

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
        from agent_ecosystem.orchestration.models import NodeStatus
        from agent_ecosystem.orchestration.state_machine import transition_node

        svc = get_service()
        req = svc.approvals.decide(
            approval_id, "approved" if body.decision == "approve" else "rejected",
            decided_by=body.decided_by, comment=body.comment,
        )
        if req is None:
            return JSONResponse({"error": "unknown approval"}, status_code=404)
        if req.workflow_id and req.node_id:
            wsvc = _service_for_workflow(svc, req.workflow_id)
            node = wsvc.store.get_node(req.node_id)
            if node is not None and node.status == NodeStatus.WAITING_INPUT:
                if body.decision == "approve":
                    # Re-dispatch with the bound token; the agent-side gate
                    # validates digest/scope and consumes it exactly once.
                    transition_node(node, NodeStatus.PENDING)
                    node.metadata["approval_token"] = req.approval_id
                    node.metadata["replay_mode"] = True
                    node.metadata.pop("suspension", None)
                    wsvc.store.save_node(node)
                else:
                    # Reject: the tool must NEVER execute; fail the node and let
                    # the scheduler cascade to dependents.
                    transition_node(node, NodeStatus.FAILED,
                                    error=f"approval rejected by {body.decided_by or 'human'}")
                    wsvc.store.save_node(node)
                _ensure_workflow_running(wsvc, req.workflow_id)
        return {"approval_id": req.approval_id, "status": req.status, "decision": req.decision}

    @app.get("/clarifications")
    async def list_clarifications(workflow_id: str | None = None, status: str | None = "open",
                                  limit: int = 50):
        svc = get_service()
        reqs = svc.clarifications.list_open(workflow_id)
        return [{
            "clarification_id": r.clarification_id, "workflow_id": r.workflow_id,
            "node_id": r.node_id, "agent_id": r.agent_id,
            "question": r.question, "status": r.status,
            "answer": r.answer, "created_at": r.created_at,
        } for r in reqs]

    @app.post("/clarifications/{clarification_id}/answer")
    async def answer_clarification(clarification_id: str, body: AnswerRequest):
        from agent_ecosystem.orchestration.models import NodeStatus
        from agent_ecosystem.orchestration.state_machine import transition_node

        svc = get_service()
        req = svc.clarifications.answer(
            clarification_id, body.answer, answered_by=body.answered_by,
        )
        if req is None:
            return JSONResponse(
                {"error": "unknown or already answered clarification"}, status_code=404)
        if req.workflow_id and req.node_id:
            wsvc = _service_for_workflow(svc, req.workflow_id)
            node = wsvc.store.get_node(req.node_id)
            if node is not None and node.status == NodeStatus.WAITING_INPUT:
                transition_node(node, NodeStatus.PENDING)
                node.metadata["clarification_answer"] = req.answer
                node.metadata["replay_mode"] = True
                node.metadata.pop("suspension", None)
                wsvc.store.save_node(node)
                _ensure_workflow_running(wsvc, req.workflow_id)
        return {"clarification_id": req.clarification_id, "status": req.status}

    # ── Phase 5: Dynamic Groups ────────────────────────────────────────

    @app.post("/groups")
    async def create_group(request: Request):
        body = await request.json()
        group_id = body.get("group_id", "")
        group_name = body.get("group_name", "")
        agents_data = body.get("agents", [])
        if not group_id or not group_name:
            return JSONResponse({"error": "group_id and group_name required"}, status_code=422)
        from agent_ecosystem.orchestration.group_service import GroupService
        gs = GroupService()
        gs.create_group(group_id, group_name, agents_data)
        return {"group_id": group_id, "status": "created", "agents": len(agents_data)}

    @app.post("/groups/{group_id}/activate")
    async def activate_group(group_id: str):
        svc = get_service()
        from agent_ecosystem.orchestration.group_service import GroupService, GroupAgent
        gs = GroupService()
        group = gs.get_group(group_id)
        if group is None:
            return JSONResponse({"error": "unknown group"}, status_code=404)
        # Build agent objects from persisted config
        with gs._connect() as conn:
            rows = conn.execute(
                "SELECT agent_id, role_name, role_prompt, capabilities_json FROM group_agents "
                "WHERE group_id=?", (group_id,)
            ).fetchall()
        if rows:
            # Already activated — just return URLs
            urls = gs.get_agent_urls(group_id)
            if urls:
                return {"group_id": group_id, "status": "active", "agents": urls}
        # First activation: start agent processes
        agents_data = json.loads(
            gs._connect().execute("SELECT agents_json FROM groups WHERE group_id=?", (group_id,)
            ).fetchone()["agents_json"])
        agents = [
            GroupAgent(agent_id=a["agent_id"], role_name=a["role_name"],
                       role_prompt=a["role_prompt"], capabilities=a.get("capabilities", []),
                       tool_profile=a.get("tool_profile", "research"))
            for a in agents_data
        ]
        # Allocate a distinct port block per concurrently-active group so two
        # groups can run side by side (8100-8109, 8110-8119, ...).
        with gs._connect() as conn:
            n_active = conn.execute(
                "SELECT COUNT(DISTINCT group_id) AS n FROM group_agents").fetchone()["n"]
        started = gs.start_agents(group_id, agents, base_port=8100 + 10 * n_active)
        urls = {a.agent_id: f"http://127.0.0.1:{a.port}" for a in started}
        return {"group_id": group_id, "status": "active", "agents": urls}

    @app.get("/groups")
    async def list_groups():
        from agent_ecosystem.orchestration.group_service import GroupService
        gs = GroupService()
        groups = gs.list_groups()
        return [{"group_id": g.group_id, "group_name": g.group_name,
                 "agent_count": len(g.agents), "created_at": g.created_at} for g in groups]

    @app.get("/groups/{group_id}")
    async def get_group(group_id: str):
        from agent_ecosystem.orchestration.group_service import GroupService
        gs = GroupService()
        group = gs.get_group(group_id)
        if group is None:
            return JSONResponse({"error": "unknown group"}, status_code=404)
        urls = gs.get_agent_urls(group_id)
        return {
            "group_id": group.group_id, "group_name": group.group_name,
            "agents": [{"agent_id": a.agent_id, "role_name": a.role_name,
                        "capabilities": a.capabilities, "port": a.port}
                       for a in group.agents],
            "agent_urls": urls,
            "workflow_ids": group.workflow_ids,
            "created_at": group.created_at,
        }

    @app.post("/groups/{group_id}/messages")
    async def group_message(group_id: str, request: Request):
        body = await request.json()
        goal = body.get("goal", "")
        if not goal:
            return JSONResponse({"error": "goal required"}, status_code=422)
        svc = get_service()
        from agent_ecosystem.orchestration.group_service import GroupService
        gs = GroupService()
        group = gs.get_group(group_id)
        if group is None:
            return JSONResponse({"error": "unknown group"}, status_code=404)
        if not gs.get_agent_urls(group_id):
            return JSONResponse(
                {"error": f"group {group_id} has no active agents; activate it first"},
                status_code=409)
        wsvc = _group_service(app, svc, group_id)
        if wsvc is None:
            return JSONResponse(
                {"error": f"group {group_id} agents not activatable"}, status_code=409)
        workflow = wsvc.create_workflow(goal)
        workflow.metadata["group_id"] = group_id
        wsvc.store.save_workflow(workflow)
        task = asyncio.create_task(wsvc.run_workflow(workflow), name=workflow.workflow_id)
        app.state.background_tasks.add(task)
        task.add_done_callback(app.state.background_tasks.discard)
        app.state.active_tasks[workflow.workflow_id] = task
        task.add_done_callback(
            lambda t, wid=workflow.workflow_id: app.state.active_tasks.pop(wid, None)
        )
        gs.add_workflow(group_id, workflow.workflow_id)
        return {"group_id": group_id, "workflow_id": workflow.workflow_id,
                "status": "submitted"}

    @app.delete("/groups/{group_id}")
    async def deactivate_group(group_id: str):
        from agent_ecosystem.orchestration.group_service import GroupService
        gs = GroupService()
        gs.stop_agents(group_id)
        return {"group_id": group_id, "status": "deactivated"}

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