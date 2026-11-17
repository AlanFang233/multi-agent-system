"""Multi-agent system interactive CLI.

    python -m agent_ecosystem.cli run "任务描述" [--mock|--live] [--follow]
    python -m agent_ecosystem.cli status <workflow_id>
    python -m agent_ecosystem.cli approvals
    python -m agent_ecosystem.cli approve <approval_id>
    python -m agent_ecosystem.cli reject <approval_id>
    python -m agent_ecosystem.cli clarify <clarification_id> "answer"
    python -m agent_ecosystem.cli dashboard
"""
from __future__ import annotations

import argparse, asyncio, json, os, sys, time
from datetime import datetime, timezone
from typing import Any

import httpx

COORD = os.environ.get("AGENTFORGE_COORDINATOR", "http://127.0.0.1:8000")

# ── formatting ───────────────────────────────────────────────────────────────

C = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m",
    "white": "\033[37m",
}
USE_COLOR = sys.stdout.isatty()

def c(tag: str, text: str) -> str:
    return f"{C.get(tag,'')}{text}{C['reset']}" if USE_COLOR else text

def icon_status(s: str) -> str:
    return {"completed": c("green","✓"), "failed": c("red","✗"),
            "running": c("blue","●"), "waiting_input": c("yellow","⏳"),
            "pending": c("dim","○"), "cancelled": c("red","⊗"),
            }.get(s, s)

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


# ── API helpers ──────────────────────────────────────────────────────────────

async def _api(method: str, path: str, body: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        if method == "GET":
            r = await client.get(f"{COORD}{path}")
        elif method == "POST":
            r = await client.post(f"{COORD}{path}", json=body or {})
        else:
            raise ValueError(method)
        if r.status_code >= 400 and r.status_code != 409:
            print(f"API {method} {path}: {r.status_code}", file=sys.stderr)
        return r.json() if r.headers.get("content-type","").startswith("application/json") else {}


# ── commands ─────────────────────────────────────────────────────────────────

async def cmd_run(goal: str, *, mock: bool = True, follow: bool = True):
    mode = "MOCK" if mock else "LIVE"
    print(c("bold", f"\n{'='*60}"))
    print(c("bold", f"  Task: {goal[:100]}"))
    print(c("dim", f"  Mode: {mode}  |  {_now()}"))
    print(c("bold", f"{'='*60}\n"))

    result = await _api("POST", "/workflows", {"goal": goal})
    wf_id = result.get("workflow_id", "")
    print(f"  Workflow ID: {c('cyan', wf_id)}")
    print(f"  Status:      {c('blue', 'submitted')}")

    if not follow:
        print(f"\n  Use: {c('cyan', f'python -m agent_ecosystem.cli status {wf_id}')}")
        return

    seen_nodes = set()
    seen_events = 0
    last_status = ""
    hitl_prompted = set()

    while True:
        await asyncio.sleep(1.5)
        try:
            snap = await _api("GET", f"/workflows/{wf_id}")
        except Exception:
            continue
        wf = snap.get("workflow", {})
        status = wf.get("status", "unknown")
        nodes = snap.get("nodes", [])
        events = snap.get("events", [])
        error = wf.get("error", "")

        # ── status change header ─────────────────────────────
        if status != last_status:
            last_status = status
            icon = icon_status(status)
            print(f"\n  {c('bold', '═══')} Workflow {icon} {c('bold',status.upper())} {c('dim',_now())} {c('bold','═══')}")

        # ── new nodes ─────────────────────────────────────────────
        for node in nodes:
            nid = node.get("node_id", "")
            if nid in seen_nodes:
                continue
            seen_nodes.add(nid)
            plan = node.get("plan_key", nid[:12])
            agent = node.get("assigned_agent", "?")
            nstatus = node.get("status", "?")
            kind = node.get("kind", "")
            synthesis = " 🔗SYNTHESIS" if kind == "synthesis" else ""
            deps = node.get("dependencies", [])
            dep_txt = f" ← {len(deps)} deps" if deps else ""
            print(f"  {icon_status(nstatus)} [{c('cyan',agent)}] {plan}{synthesis}{dep_txt}")

        # ── tool evidence ─────────────────────────────────────────────
        for node in nodes:
            ev = (node.get("metadata") or {}).get("evidence") or {}
            tools = ev.get("executed_tools") or []
            tool_events = ev.get("tool_events") or []
            for te in tool_events:
                tid = te.get("tool_id", "?")
                is_err = te.get("is_error", False)
                mark = c("green","OK") if not is_err else c("red","ERR")
                print(f"    {c('dim','▸')} MCP: {tid} [{mark}]")

        # ── HITL detection ─────────────────────────────────────────────
        for node in nodes:
            if node.get("status") != "waiting_input":
                continue
            nid = node.get("node_id", "")[:12]
            # Check for pending approvals
            approvals = await _api("GET", "/approvals", None)
            for a in (approvals if isinstance(approvals, list) else []):
                aid = a.get("approval_id","")
                if aid in hitl_prompted:
                    continue
                hitl_prompted.add(aid)
                print(c("yellow", f"\n  ⏳ HITL APPROVAL REQUIRED"))
                print(f"    Tool:    {c('cyan', a.get('tool_id','?'))}")
                print(f"    Risk:    {c('red', a.get('risk_level','?'))}")
                print(f"    Summary: {a.get('summary','')[:200]}")
                print(f"    ID:      {aid}")
                print(f"\n  {c('green','[A]pprove')} / {c('red','[R]eject')} / [S]kip: ", end="", flush=True)
                # Non-blocking check via polling — user types in separate terminal
                print(c("dim", f"\n  → Use: approve {aid}  OR  reject {aid}"))

            # Check for clarifications
            clarifs = await _api("GET", "/clarifications", None)
            for cl in (clarifs if isinstance(clarifs, list) else []):
                cid = cl.get("clarification_id","")
                if cid in hitl_prompted:
                    continue
                hitl_prompted.add(cid)
                print(c("yellow", f"\n  ❓ CLARIFICATION NEEDED"))
                print(f"    Q: {cl.get('question','?')}")
                print(f"    ID: {cid}")
                print(c("dim", f"    → Use: clarify {cid} \"your answer\""))

        # ── terminal state ─────────────────────────────────────────────
        if status in ("completed", "failed", "cancelled"):
            print()
            if status == "completed":
                try:
                    r = await _api("GET", f"/workflows/{wf_id}/result")
                    final = r.get("final_result", "")
                    if final:
                        print(c("bold", "  ── Final Result ──"))
                        for line in final.split("\n")[:30]:
                            print(f"  {c('dim',line)}")
                        print(c("bold", "  ── End ──"))
                except Exception:
                    pass
                print(c("green", f"\n  ✓ Workflow COMPLETED ({len(nodes)} nodes)"))
            elif status == "failed":
                print(c("red", f"\n  ✗ Workflow FAILED"))
                if error:
                    print(c("red", f"    {error[:300]}"))
            else:
                print(f"\n  Workflow {status.upper()}")
            return


async def cmd_status(wf_id: str):
    snap = await _api("GET", f"/workflows/{wf_id}")
    wf = snap.get("workflow", {})
    print(c("bold", f"Workflow {wf_id}"))
    print(f"  Status: {icon_status(wf.get('status','?'))} {wf.get('status','?')}")
    print(f"  Goal:   {wf.get('goal','')[:120]}")
    if wf.get("final_result"):
        print(f"  Result: {wf['final_result'][:500]}")
    if wf.get("error"):
        print(c("red", f"  Error:  {wf['error'][:300]}"))
    nodes = snap.get("nodes", [])
    print(c("bold", f"\n  Nodes ({len(nodes)}):"))
    for n in nodes:
        print(f"    {icon_status(n.get('status','?'))} [{n.get('assigned_agent','?')}] "
              f"{n.get('plan_key', n.get('node_id','')[:12])}  "
              f"{c('dim', n.get('status','?'))}  "
              f"depth={n.get('depth',1)}  deps={len(n.get('dependencies',[]))}")


async def cmd_approvals():
    apps = await _api("GET", "/approvals?status=pending")
    apps = apps if isinstance(apps, list) else []
    if not apps:
        print(c("dim", "No pending approvals."))
        return
    for a in apps:
        print(f"  {c('yellow','⏳')} {a['approval_id'][:16]}  {a['tool_id']}  "
              f"risk={a.get('risk_level','?')}  {a.get('summary','')[:100]}")

async def cmd_approve(aid: str):
    await _api("POST", f"/approvals/{aid}/decide", {"decision": "approve"})
    print(c("green", f"  ✓ Approved {aid}"))

async def cmd_reject(aid: str):
    await _api("POST", f"/approvals/{aid}/decide", {"decision": "reject"})
    print(c("red", f"  ✗ Rejected {aid}"))

async def cmd_group_create(group_id: str, group_name: str, scenario: str = "dev-team"):
    roles = []
    if scenario == "dev-team":
        from agent_ecosystem.orchestration.group_service import SCENARIO_1_ROLES
        roles = list(SCENARIO_1_ROLES)
    body = {"group_id": group_id, "group_name": group_name, "agents": roles}
    result = await _api("POST", "/groups", body)
    print(c("green", f"Group created: {group_id} ({len(roles)} roles)"))
    for r in roles:
        print(f"  [{c('cyan', r['agent_id'])}] {r['role_name']}")

async def cmd_group_activate(group_id: str):
    result = await _api("POST", f"/groups/{group_id}/activate")
    agents = result.get("agents", {})
    print(c("green", f"Group {group_id} activated"))
    for aid, url in sorted(agents.items()):
        print(f"  [{c('cyan', aid)}] {url}")

async def cmd_group_message(group_id: str, goal: str):
    result = await _api("POST", f"/groups/{group_id}/messages", {"goal": goal})
    wf_id = result.get("workflow_id", "")
    print(f"Message sent to group {group_id}")
    print(f"Workflow: {c('cyan', wf_id)}")
    print(f"Monitor: {c('dim', f'python -m agent_ecosystem.cli status {wf_id}')}")

async def cmd_group_show(group_id: str):
    result = await _api("GET", f"/groups/{group_id}")
    print(c("bold", f"Group: {result.get('group_name', '?')} ({group_id})"))
    for a in result.get("agents", []):
        print(f"  [{c('cyan', a['agent_id'])}] {a['role_name']} "
              f"caps={a.get('capabilities',[])} port={a.get('port')}")
    print(f"  Workflows: {result.get('workflow_ids', [])}")
    await _api("POST", f"/clarifications/{cid}/answer", {"answer": answer})
    print(c("green", f"  ✓ Answered {cid}"))


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Multi-Agent System CLI")
    sub = p.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="Submit and follow a workflow")
    run_p.add_argument("goal", help="Natural language task")
    run_p.add_argument("--mock", action="store_true", default=True)
    run_p.add_argument("--live", action="store_true")
    run_p.add_argument("--follow", action="store_true", default=True)
    run_p.add_argument("--no-follow", action="store_false", dest="follow")

    status_p = sub.add_parser("status", help="Show workflow status")
    status_p.add_argument("workflow_id")

    sub.add_parser("approvals", help="List pending approvals")
    approve_p = sub.add_parser("approve", help="Approve a pending approval")
    approve_p.add_argument("approval_id")
    reject_p = sub.add_parser("reject", help="Reject a pending approval")
    reject_p.add_argument("approval_id")
    clarify_p = sub.add_parser("clarify", help="Answer a clarification")
    clarify_p.add_argument("clarification_id")
    clarify_p.add_argument("answer")
    sub.add_parser("dashboard", help="Open dashboard URL")

    # Group commands
    group_p = sub.add_parser("group-create", help="Create a new agent group")
    group_p.add_argument("group_id")
    group_p.add_argument("group_name")
    group_p.add_argument("--scenario", choices=["dev-team"], default="dev-team")
    grp_msg = sub.add_parser("group-message", help="Send a message to a group")
    grp_msg.add_argument("group_id")
    grp_msg.add_argument("goal")
    sub.add_parser("groups", help="List all groups")
    group_show = sub.add_parser("group-show", help="Show group details")
    group_show.add_argument("group_id")
    group_activate = sub.add_parser("group-activate", help="Activate a group (start agents)")
    group_activate.add_argument("group_id")
    group_stop = sub.add_parser("group-stop", help="Stop a group's agents")
    group_stop.add_argument("group_id")

    args = p.parse_args()
    loop = asyncio.get_event_loop()

    if args.cmd == "run":
        loop.run_until_complete(cmd_run(args.goal, mock=not args.live, follow=args.follow))
    elif args.cmd == "status":
        loop.run_until_complete(cmd_status(args.workflow_id))
    elif args.cmd == "approvals":
        loop.run_until_complete(cmd_approvals())
    elif args.cmd == "approve":
        loop.run_until_complete(cmd_approve(args.approval_id))
    elif args.cmd == "reject":
        loop.run_until_complete(cmd_reject(args.approval_id))
    elif args.cmd == "clarify":
        loop.run_until_complete(cmd_clarify(args.clarification_id, args.answer))
    elif args.cmd == "dashboard":
        print(f"Dashboard: {COORD}/dashboard")
    elif args.cmd == "group-create":
        loop.run_until_complete(cmd_group_create(args.group_id, args.group_name, args.scenario))
    elif args.cmd == "group-activate":
        loop.run_until_complete(cmd_group_activate(args.group_id))
    elif args.cmd == "group-message":
        loop.run_until_complete(cmd_group_message(args.group_id, args.goal))
    elif args.cmd == "groups":
        loop.run_until_complete(_cmd_groups())
    elif args.cmd == "group-show":
        loop.run_until_complete(cmd_group_show(args.group_id))
    elif args.cmd == "group-stop":
        from agent_ecosystem.orchestration.group_service import GroupService
        GroupService().stop_agents(args.group_id)
        print(f"Group {args.group_id} deactivated")
    else:
        p.print_help()


if __name__ == "__main__":
    main()