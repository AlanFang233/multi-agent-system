#!/usr/bin/env python3
"""Integration acceptance run — real E2E tasks A/B/C + HITL + recovery + isolation.

Runs against live services started by scripts/phase3_start.sh --legacy (Mock LLM only,
no paid API). Every check prints a "[ACPT] PASS/FAIL" line; evidence JSON goes
to data/acceptance/. Exit code 0 only if ALL checks pass.
"""
import asyncio
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
COORD = "http://127.0.0.1:8000"
MEM_DB = ROOT / "data" / "agent_memory.sqlite"
ORCH_DB = ROOT / "data" / "orchestration.sqlite"
OUT = ROOT / "data" / "acceptance"
VENV_PY = str(ROOT / ".venv" / "bin" / "python")

GOAL_A = ("Research the key components of RAG, compare their roles, and produce "
          "a concise evidence-backed report.")
GOAL_B = ("Write a Python program to calculate the prime numbers from 1 to 100, "
          "actually execute it, and verify the results.")
GOAL_CL = "Research the key components of RAG and compare their roles. [FORCE_CLARIFY]"
GOAL_C = ("Build a simple Python calculator, test its addition and subtraction "
          "functions, and provide a short release summary.")
GOAL_R = ("Write a Python script that prints hello world, actually execute it in "
          "the sandbox, and verify the output.")

RESULTS: list[dict] = []
FAILED: list[str] = []
GLOBAL: dict = {}


def record(cid: str, name: str, ok: bool, evidence: str = "") -> None:
    print(f"[ACPT] {'PASS' if ok else 'FAIL'} {cid}: {name}"
          + (f" | {evidence}" if evidence else ""), flush=True)
    RESULTS.append({"id": cid, "name": name, "ok": bool(ok),
                    "evidence": str(evidence)[:800]})
    if not ok:
        FAILED.append(cid)


def save(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _query(db: Path, sql: str, args=()) -> list[dict]:
    conn = sqlite3.connect(str(db), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def mem_query(sql, args=()):
    return _query(MEM_DB, sql, args)


def orch_query(sql, args=()):
    return _query(ORCH_DB, sql, args)


async def submit(client, goal) -> str:
    r = await client.post(f"{COORD}/workflows", json={"goal": goal})
    r.raise_for_status()
    return r.json()["workflow_id"]


async def decide_pending(client, wf_id, decision) -> list:
    r = await client.get(f"{COORD}/approvals",
                         params={"workflow_id": wf_id, "status": "pending"})
    decided = []
    for a in r.json():
        await client.post(f"{COORD}/approvals/{a['approval_id']}/decide",
                          json={"decision": decision, "decided_by": "acceptance"})
        decided.append((a["approval_id"], a["tool_id"]))
        print(f"  [hitl] {decision} {a['approval_id']} tool={a['tool_id']} "
              f"node={a['node_id']} agent={a['agent_id']}", flush=True)
    return decided


async def poll(client, wf_id, timeout, auto=None):
    """Poll until terminal. auto in {approve, reject, None} decides pending approvals."""
    deadline = time.monotonic() + timeout
    n_decided = 0
    while time.monotonic() < deadline:
        try:
            r = await client.get(f"{COORD}/workflows/{wf_id}")
            snap = r.json()
        except httpx.HTTPError:
            await asyncio.sleep(2)
            continue
        st = snap["workflow"]["status"]
        if st in ("completed", "failed", "cancelled"):
            return snap, n_decided
        if auto:
            n_decided += len(await decide_pending(client, wf_id, auto))
        await asyncio.sleep(2.0)
    raise TimeoutError(f"workflow {wf_id} did not finish in {timeout}s")


async def wait_first_pending(client, wf_id, timeout=400):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = await client.get(f"{COORD}/approvals",
                             params={"workflow_id": wf_id, "status": "pending"})
        pend = r.json()
        if pend:
            return pend[0]
        await asyncio.sleep(2)
    return None


# ── Task A: research workflow ────────────────────────────────────────────────

async def task_a(client):
    print("\n===== TASK A: RAG research workflow =====", flush=True)
    wf = await submit(client, GOAL_A)
    t0 = time.monotonic()
    snap, _ = await poll(client, wf, 600)
    el = time.monotonic() - t0
    save("task_a.json", {"goal": GOAL_A, "workflow_id": wf,
                         "elapsed_s": round(el, 1), "snapshot": snap})
    st = snap["workflow"]["status"]
    final = snap["workflow"].get("final_result") or ""
    nodes = snap["nodes"]
    comp = [n["node_id"] for n in nodes if n["status"] == "completed"]
    record("A1", "Task A workflow COMPLETED", st == "completed",
           f"wf={wf} status={st} {el:.0f}s nodes={len(nodes)}")
    record("A2", "Task A produced a real report (final_result >100 chars)",
           len(final) > 100, f"{len(final)} chars")
    record("A3", "Task A all nodes completed", len(comp) == len(nodes) and nodes,
           f"{len(comp)}/{len(nodes)}")


# ── Task B: primes with real approval flow + Docker execution ───────────────

async def task_b(client):
    print("\n===== TASK B: primes 1-100 (approve flow, real execution) =====", flush=True)
    wf = await submit(client, GOAL_B)
    t0 = time.monotonic()
    snap, n_dec = await poll(client, wf, 1200, auto="approve")
    el = time.monotonic() - t0
    blob = json.dumps(snap, ensure_ascii=False)
    save("task_b.json", {"goal": GOAL_B, "workflow_id": wf, "approvals_decided": n_dec,
                         "elapsed_s": round(el, 1), "snapshot": snap})
    st = snap["workflow"]["status"]
    record("B1", "Task B COMPLETED through real approval flow (>=2 approvals)",
           st == "completed" and n_dec >= 2,
           f"wf={wf} status={st} approvals_decided={n_dec} {el:.0f}s")
    record("B2", "Task B result contains PRIME_COUNT=25 and PRIME_SUM=1060",
           "PRIME_COUNT=25" in blob and "PRIME_SUM=1060" in blob)
    rows = mem_query(
        "SELECT tool_id, agent_id, task_id FROM tool_results "
        "WHERE task_id LIKE ? AND body LIKE '%PRIME_COUNT=25%'", (wf + ":%",))
    record("B3", "Real sandbox execution in tool audit (sandbox:run_python stdout)",
           any(r["tool_id"] == "sandbox:run_python" for r in rows),
           f"{len(rows)} rows: {[(r['tool_id'], r['agent_id']) for r in rows]}")
    apr = orch_query(
        "SELECT approval_id, tool_id, status, consumed_at FROM approvals "
        "WHERE workflow_id=?", (wf,))
    consumed = [a for a in apr if a["consumed_at"]]
    record("B4", "All approvals approved and consumed exactly-once",
           len(apr) >= 2 and all(a["status"] == "approved" for a in apr)
           and len(consumed) >= 2,
           f"{len(apr)} approvals, {len(consumed)} consumed")


async def task_b_reject(client):
    print("\n===== TASK B-REJECT: rejected approval must never execute =====", flush=True)
    wf = await submit(client, GOAL_B)
    a = await wait_first_pending(client, wf)
    if a is None:
        record("R1", "Reject variant: approval gate triggered", False,
               "no pending approval appeared in 400s")
        return
    await client.post(f"{COORD}/approvals/{a['approval_id']}/decide",
                      json={"decision": "reject", "decided_by": "acceptance"})
    snap, _ = await poll(client, wf, 400)
    st = snap["workflow"]["status"]
    save("task_b_reject.json", {"goal": GOAL_B, "workflow_id": wf,
                                "rejected_approval": a, "snapshot": snap})
    record("R1", "Rejected approval -> workflow FAILED (never false-COMPLETED)",
           st == "failed", f"wf={wf} status={st} rejected tool={a['tool_id']}")
    failed_nodes = [n["node_id"] for n in snap["nodes"] if n["status"] == "failed"]
    record("R2", "Blocked node FAILED and cascade swept dependents",
           len(failed_nodes) >= 1, f"failed nodes: {failed_nodes}")
    dangerous = mem_query(
        "SELECT tool_id FROM tool_results WHERE task_id LIKE ? AND tool_id IN "
        "('filesystem_code:write_file','filesystem_code:edit_file',"
        "'sandbox:run_python','sandbox:run_pytest')", (wf + ":%",))
    record("R3", "ZERO high-risk tool executions after rejection",
           len(dangerous) == 0, f"tool_results rows: {dangerous}")
    apr = orch_query("SELECT status, consumed_at FROM approvals WHERE workflow_id=?",
                     (wf,))
    record("R4", "Approval row rejected and unconsumed",
           any(x["status"] == "rejected" and not x["consumed_at"] for x in apr),
           str(apr))


# ── Clarification E2E ────────────────────────────────────────────────────────

async def task_clarify(client):
    print("\n===== CLARIFICATION: suspend -> answer -> resume =====", flush=True)
    wf = await submit(client, GOAL_CL)
    # Answer every clarification the workflow raises, keyed off nodes that have
    # actually reached waiting_input (race-free: the node metadata carries the
    # pending_clarification_id only after the scheduler parked it).
    deadline = time.monotonic() + 600
    first = None
    first_q = None
    answered = 0
    snap = None
    while time.monotonic() < deadline:
        s = (await client.get(f"{COORD}/workflows/{wf}")).json()
        st = s["workflow"]["status"]
        if st in ("completed", "failed", "cancelled"):
            snap = s
            break
        for n in s["nodes"]:
            if n["status"] == "waiting_input":
                cid = (n.get("metadata") or {}).get("pending_clarification_id")
                if not cid:
                    continue
                if first is None:
                    first = cid
                r = await client.post(
                    f"{COORD}/clarifications/{cid}/answer",
                    json={"answer": "Focus on retrieval vs generation components; "
                                    "keep it concise.",
                          "answered_by": "acceptance"})
                if r.status_code == 200:
                    answered += 1
                    if first_q is None:
                        cl = (await client.get(f"{COORD}/clarifications",
                                               params={"workflow_id": wf})).json()
                        first_q = next((c.get("question") for c in cl
                                        if c["clarification_id"] == cid), None)
        await asyncio.sleep(2)
    record("CL1", "Clarification triggered (agent asked, workflow suspended)",
           first is not None, f"wf={wf} cid={first} q={str(first_q)[:100]}")
    if snap is None:
        record("CL2", "Answer resumed workflow to COMPLETED", False,
               f"wf={wf} did not finish in 600s (answered={answered})")
        return
    st = snap["workflow"]["status"]
    final = snap["workflow"].get("final_result") or ""
    save("clarify.json", {"goal": GOAL_CL, "workflow_id": wf, "first_cid": first,
                          "answered": answered, "snapshot": snap})
    record("CL2", "Answer resumed workflow to COMPLETED",
           st == "completed" and len(final) > 50 and answered >= 1,
           f"status={st} answered={answered} final={len(final)} chars")


# ── Task C: dynamic group, 4 roles, real A2A ────────────────────────────────

async def task_c(client):
    print("\n===== TASK C: dynamic dev-team group (4 roles) =====", flush=True)
    from agent_ecosystem.orchestration.group_service import SCENARIO_1_ROLES
    gid = "acpt-c"
    r = await client.post(f"{COORD}/groups",
                          json={"group_id": gid, "group_name": "Acceptance Dev Team",
                                "agents": list(SCENARIO_1_ROLES)})
    ok_create = r.status_code == 200
    r = await client.post(f"{COORD}/groups/{gid}/activate")
    urls = (r.json() or {}).get("agents") or {}
    healthy = 0
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        healthy = 0
        for u in urls.values():
            try:
                rr = await client.get(f"{u}/health", timeout=5)
                healthy += rr.status_code == 200
            except httpx.HTTPError:
                pass
        if urls and healthy == len(urls):
            break
        await asyncio.sleep(3)
    record("C1", "Group created, 4 namespaced agents spawned and healthy",
           ok_create and len(urls) == 4 and healthy == 4,
           f"urls={urls} healthy={healthy}/{len(urls)}")
    GLOBAL["c_urls"] = urls
    if healthy != 4:
        return
    r = await client.post(f"{COORD}/groups/{gid}/messages", json={"goal": GOAL_C})
    wf = r.json()["workflow_id"]
    t0 = time.monotonic()
    snap, n_dec = await poll(client, wf, 1500, auto="approve")
    el = time.monotonic() - t0
    st = snap["workflow"]["status"]
    agents_used = sorted({n.get("assigned_agent") or "" for n in snap["nodes"]})
    save("task_c.json", {"goal": GOAL_C, "group_id": gid, "workflow_id": wf,
                         "approvals_decided": n_dec, "elapsed_s": round(el, 1),
                         "snapshot": snap})
    record("C2", "Group workflow COMPLETED via real A2A (>=2 distinct roles)",
           st == "completed" and len(agents_used) >= 2,
           f"wf={wf} status={st} {el:.0f}s approvals={n_dec} agents={agents_used}")
    record("C3", "All nodes ran on namespaced group agents only",
           bool(agents_used) and all(a.startswith(gid + ":") for a in agents_used),
           str(agents_used))
    calc_rows = mem_query(
        "SELECT tool_id, agent_id FROM tool_results "
        "WHERE task_id LIKE ? AND body LIKE '%CALC_TESTS_PASSED%'", (wf + ":%",))
    record("C4", "Calculator really executed (CALC_TESTS_PASSED in tool audit)",
           len(calc_rows) >= 1, str(calc_rows[:4]))
    g = (await client.get(f"{COORD}/groups/{gid}")).json()
    record("C5", "Workflow registered under the group",
           wf in (g.get("workflow_ids") or []), str(g.get("workflow_ids")))
    GLOBAL["c_wf"] = wf


# ── Coordinator crash/restart recovery ───────────────────────────────────────

async def task_restart(client):
    print("\n===== RESTART: kill coordinator mid-approval, recover =====", flush=True)
    wf = await submit(client, GOAL_R)
    a = await wait_first_pending(client, wf, timeout=400)
    if a is None:
        record("RS1", "Workflow reached approval gate before kill", False,
               f"no pending approval for wf={wf}")
        return
    pre = (await client.get(f"{COORD}/workflows/{wf}")).json()
    pre_states = {n["node_id"]: n["status"] for n in pre["nodes"]}
    record("RS1", "Workflow reached approval gate before kill", True,
           f"wf={wf} node={a['node_id']} tool={a['tool_id']} states={pre_states}")
    subprocess.run(["pkill", "-f", "agent_ecosystem.coordinator"])
    await asyncio.sleep(2)
    dead = True
    try:
        httpx.get(f"{COORD}/health", timeout=3)
        dead = False
    except httpx.HTTPError:
        pass
    record("RS2", "Coordinator process killed", dead)
    logf = open(ROOT / "logs" / "coordinator.log", "ab")
    subprocess.Popen(
        [VENV_PY, "-m", "agent_ecosystem.coordinator", "--host", "0.0.0.0",
         "--port", "8000"],
        cwd=str(ROOT), stdout=logf, stderr=logf, start_new_session=True)
    up = False
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{COORD}/health", timeout=3).status_code == 200:
                up = True
                break
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1)
    record("RS3", "Coordinator restarted and healthy", up)
    if not up:
        return
    async with httpx.AsyncClient(timeout=60) as c2:
        r = await c2.get(f"{COORD}/workflows/{wf}")
        snap = r.json() if r.status_code == 200 else {}
        got_id = snap.get("workflow", {}).get("workflow_id")
        post_states = {n["node_id"]: n["status"] for n in snap.get("nodes", [])}
        record("RS4", "Workflow + node states persisted in SQLite across restart",
               r.status_code == 200 and got_id == wf,
               f"rc={r.status_code} states={post_states}")
        g = (await c2.get(f"{COORD}/groups/acpt-c")).json()
        gurls = g.get("agent_urls") or {}
        record("RS5", "Group registry survived coordinator restart",
               len(gurls) == 4, str(gurls))
        snap2, n_dec = await poll(c2, wf, 900, auto="approve")
        st = snap2["workflow"]["status"]
        final_states = {n["node_id"]: n["status"] for n in snap2["nodes"]}
        kept = all(final_states.get(nid) == "completed"
                   for nid, s in pre_states.items() if s == "completed")
        save("restart.json", {"workflow_id": wf, "pre_states": pre_states,
                              "post_restart_states": post_states,
                              "final_states": final_states, "snapshot": snap2})
        record("RS6", "Approval after restart resumed workflow to COMPLETED",
               st == "completed" and n_dec >= 1,
               f"status={st} approvals_decided={n_dec}")
        record("RS7", "Previously completed nodes not lost/re-executed", kept,
               f"pre={pre_states} final={final_states}")


# ── Cross-group isolation ────────────────────────────────────────────────────

async def task_isolation(client):
    print("\n===== ISOLATION: second concurrent group =====", flush=True)
    from agent_ecosystem.orchestration.group_service import SCENARIO_1_ROLES
    gid = "acpt-iso"
    r = await client.post(f"{COORD}/groups",
                          json={"group_id": gid, "group_name": "Isolation Squad",
                                "agents": list(SCENARIO_1_ROLES)})
    r.raise_for_status()
    r = await client.post(f"{COORD}/groups/{gid}/activate")
    urls = (r.json() or {}).get("agents") or {}
    iso_ports = sorted(int(u.rsplit(":", 1)[1]) for u in urls.values())
    c_ports = sorted(int(u.rsplit(":", 1)[1])
                     for u in (GLOBAL.get("c_urls") or {}).values())
    record("IS1", "Second group got distinct ports (no collision)",
           len(urls) == 4 and not (set(iso_ports) & set(c_ports)),
           f"{gid}={iso_ports} acpt-c={c_ports}")
    healthy = 0
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        healthy = 0
        for u in urls.values():
            try:
                rr = await client.get(f"{u}/health", timeout=5)
                healthy += rr.status_code == 200
            except httpx.HTTPError:
                pass
        if urls and healthy == len(urls):
            break
        await asyncio.sleep(3)
    record("IS2", "All 4 isolation-group agents healthy", healthy == 4,
           f"{healthy}/{len(urls)}")
    ids = sorted(urls.keys())
    record("IS3", "Agent ids namespaced per group",
           bool(ids) and all(i.startswith(gid + ":") for i in ids), str(ids))
    if healthy != 4:
        return
    r = await client.post(
        f"{COORD}/groups/{gid}/messages",
        json={"goal": "Research what unit testing is and summarize three best practices."})
    wf2 = r.json()["workflow_id"]
    # The mock planner still emits a test_verify (qa) node for a "testing"
    # goal, which trips the approval gate — auto-approve like the other tasks.
    snap, _ = await poll(client, wf2, 600, auto="approve")
    st = snap["workflow"]["status"]
    agents_used = sorted({n.get("assigned_agent") or "" for n in snap["nodes"]})
    save("isolation.json", {"group_id": gid, "workflow_id": wf2, "snapshot": snap})
    record("IS4", "Group-2 workflow ran solely on group-2 agents",
           st == "completed" and bool(agents_used)
           and all(a.startswith(gid + ":") for a in agents_used),
           f"status={st} agents={agents_used}")
    rows = mem_query("SELECT DISTINCT agent_id FROM tasks WHERE task_id LIKE ?",
                     (wf2 + ":%",))
    record("IS5", "Memory rows namespaced (no cross-group contamination)",
           bool(rows) and all(x["agent_id"].startswith(gid + ":") for x in rows),
           str([x["agent_id"] for x in rows]))
    c_wf = GLOBAL.get("c_wf")
    if c_wf:
        rows_c = mem_query("SELECT DISTINCT agent_id FROM tasks WHERE task_id LIKE ?",
                           (c_wf + ":%",))
        record("IS6", "Group-1 memory rows kept their own namespace",
               bool(rows_c) and all(x["agent_id"].startswith("acpt-c:")
                                    for x in rows_c),
               str([x["agent_id"] for x in rows_c]))
    GLOBAL["iso_urls"] = urls


async def cleanup(client):
    print("\n===== CLEANUP: stop groups, verify ports freed =====", flush=True)
    for gid in ("acpt-c", "acpt-iso"):
        try:
            await client.delete(f"{COORD}/groups/{gid}")
        except httpx.HTTPError as e:
            print(f"  cleanup {gid}: {e}", flush=True)
    await asyncio.sleep(3)
    ports = sorted({int(u.rsplit(":", 1)[1])
                    for u in list((GLOBAL.get("c_urls") or {}).values())
                    + list((GLOBAL.get("iso_urls") or {}).values())})
    freed = 0
    for port in ports:
        try:
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
        except httpx.HTTPError:
            freed += 1
    record("UP1", "Group agent processes stopped, ports freed",
           bool(ports) and freed == len(ports), f"{freed}/{len(ports)} of {ports}")


# ── Planning-crash zombie recovery ───────────────────────────────────────────

async def zombie_recovery(client):
    print("\n===== ZOMBIE: resume zero-node RUNNING workflow =====", flush=True)
    r = await client.get(f"{COORD}/workflows", params={"status": "running", "limit": 50})
    zombies = []
    for w in r.json():
        snap = (await client.get(f"{COORD}/workflows/{w['workflow_id']}")).json()
        if not snap.get("nodes"):
            zombies.append(w["workflow_id"])
    if not zombies:
        record("Z1", "Planning-crash zombie recovery", True,
               "no zero-node running workflows left to recover")
        return
    wid = zombies[0]
    r = await client.post(f"{COORD}/workflows/{wid}/resume")
    ok_call = r.status_code == 200
    try:
        snap, _ = await poll(client, wid, 900, auto="approve")
        st = snap["workflow"]["status"]
        record("Z1", "Planning-crash zombie re-planned via manual resume",
               ok_call and st in ("completed", "failed") and len(snap["nodes"]) > 0,
               f"wf={wid} status={st} nodes={len(snap['nodes'])}")
    except TimeoutError:
        record("Z1", "Planning-crash zombie re-planned via manual resume", False,
               f"wf={wid} still non-terminal 900s after resume")


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=60) as client:
        ok = False
        for _ in range(30):
            try:
                if (await client.get(f"{COORD}/health", timeout=5)).status_code == 200:
                    ok = True
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(2)
        if not ok:
            print("[ACPT] FATAL: coordinator not healthy", flush=True)
            sys.exit(2)
        # clean slate: stale group rows would shift port allocation
        try:
            for g in (await client.get(f"{COORD}/groups")).json():
                await client.delete(f"{COORD}/groups/{g['group_id']}")
        except (httpx.HTTPError, KeyError) as e:
            print(f"[ACPT] group pre-clean warning: {e}", flush=True)
        await task_a(client)
        await task_b(client)
        await task_b_reject(client)
        await task_clarify(client)
        await task_c(client)
        await task_restart(client)
        await zombie_recovery(client)
        await task_isolation(client)
        await cleanup(client)
    save("summary.json", {"results": RESULTS, "failed": FAILED,
                          "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    n_pass = len(RESULTS) - len(FAILED)
    print(f"\n[ACPT] ==== SUMMARY: {n_pass}/{len(RESULTS)} PASS ====", flush=True)
    for cid in FAILED:
        print(f"[ACPT] FAILED CHECK: {cid}", flush=True)
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    asyncio.run(main())
