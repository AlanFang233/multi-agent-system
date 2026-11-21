#!/usr/bin/env python3
"""Mid_Test task driver: submits a teacher task to a group, live-streams the
workflow (events, node transitions, tool approvals, clarifications), decides
HITL gates as the operator, and exports machine-readable evidence.

Usage:
  python scripts/midtest_task_driver.py --group midtest-software \
      --goal-file artifacts/mid_test_local/task1_goal.txt --tag task1 \
      --clarify-file artifacts/mid_test_local/operator_defaults_task1.txt \
      --timeout 5700

Nothing about the plan, the code or the answers is pre-seeded: the planner and
the four group agents decide everything at runtime; this driver only observes,
approves tool executions (logging each one) and answers clarifications with the
documented operator defaults.
"""
import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
COORD = "http://127.0.0.1:8000"
MEM_DB = ROOT / "data" / "agent_memory.sqlite"
ORCH_DB = ROOT / "data" / "orchestration.sqlite"
ART = ROOT / "artifacts" / "mid_test_local"
MAX_APPROVALS = 100  # runaway guard (cost control on the real model)


def ts() -> str:
    return time.strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


class Evidence:
    def __init__(self, tag: str):
        self.dir = ART
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_f = open(self.dir / f"{tag}_events.jsonl", "a", encoding="utf-8")
        self.approvals = []
        self.clarifications = []
        self.tag = tag

    def event(self, kind: str, payload: dict) -> None:
        rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, **payload}
        self.events_f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.events_f.flush()

    def close(self, wf_id: str, snap: dict | None) -> None:
        self.events_f.close()
        (self.dir / f"{self.tag}_approvals.json").write_text(
            json.dumps(self.approvals, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.dir / f"{self.tag}_clarifications.json").write_text(
            json.dumps(self.clarifications, ensure_ascii=False, indent=2),
            encoding="utf-8")
        if snap is not None:
            (self.dir / f"{self.tag}_workflow_final.json").write_text(
                json.dumps(snap, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8")
        self.export_audit(wf_id)

    def export_audit(self, wf_id: str) -> None:
        """Export agent-side audit (memory DB) + approval rows (orch DB)."""
        out = {"workflow_id": wf_id, "tasks": [], "tool_results": [], "approvals": []}
        conn = sqlite3.connect(str(MEM_DB), timeout=10)
        conn.row_factory = sqlite3.Row
        like = wf_id + ":%"
        for r in conn.execute("SELECT agent_id, task_id, status, goal, executed_json, "
                              "answer FROM tasks WHERE task_id LIKE ?", (like,)):
            d = dict(r)
            d["answer"] = (d.get("answer") or "")[:4000]
            out["tasks"].append(d)
        for r in conn.execute("SELECT task_id, event_type, payload_json, created_at "
                              "FROM task_events WHERE task_id LIKE ? ORDER BY id", (like,)):
            pass  # events can be huge; counted below instead
        counts = {}
        for r in conn.execute("SELECT task_id, event_type, COUNT(*) AS n FROM task_events "
                              "WHERE task_id LIKE ? GROUP BY task_id, event_type", (like,)):
            counts.setdefault(r["task_id"], {})[r["event_type"]] = r["n"]
        out["event_counts"] = counts
        llm_calls = sum(c.get("llm_tool_requests", 0) + c.get("task_completed", 0)
                        + c.get("tool_budget_exhausted", 0) for c in counts.values())
        out["llm_call_estimate"] = llm_calls
        for r in conn.execute("SELECT task_id, tool_id, is_truncated, created_at, "
                              "substr(body,1,600) AS body_head FROM tool_results "
                              "WHERE task_id LIKE ? ORDER BY created_at", (like,)):
            out["tool_results"].append(dict(r))
        conn.close()
        conn = sqlite3.connect(str(ORCH_DB), timeout=10)
        conn.row_factory = sqlite3.Row
        for r in conn.execute("SELECT approval_id, node_id, agent_id, tool_id, status, "
                              "decision, consumed_at, argument_digest, summary "
                              "FROM approvals WHERE workflow_id=?", (wf_id,)):
            out["approvals"].append(dict(r))
        conn.close()
        (self.dir / f"{self.tag}_audit.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        log(f"audit exported: llm_call_estimate={llm_calls} "
            f"tool_results={len(out['tool_results'])} approvals={len(out['approvals'])}")
        # copy produced software files out of the sandbox workspaces
        workspaces = set()
        for tr in out["tool_results"]:
            body = tr.get("body_head") or ""
            if tr["tool_id"] == "sandbox:create":
                try:
                    j = json.loads(body)
                    ws = None
                    txt = j.get("result")
                    if isinstance(txt, list) and txt:
                        inner = json.loads(txt[0].get("text", "{}"))
                        ws = inner.get("workspace")
                    if ws:
                        workspaces.add(ws)
                except Exception:
                    pass
        copied = []
        for ws in workspaces:
            p = Path(ws)
            if p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file() and f.suffix in (".py", ".json", ".md", ".html",
                                                    ".csv", ".txt", ".png", ".svg"):
                        dest = self.dir / f"{self.tag}_software" / p.name / f.relative_to(p)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, dest)
                        copied.append(str(dest.relative_to(self.dir)))
        if copied:
            log(f"software files copied to artifacts: {len(copied)}")
            (self.dir / f"{self.tag}_software_files.json").write_text(
                json.dumps({"workspaces": sorted(workspaces), "copied": copied},
                           ensure_ascii=False, indent=2), encoding="utf-8")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True)
    ap.add_argument("--goal-file", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--clarify-file", default=None)
    ap.add_argument("--timeout", type=float, default=5700.0)
    args = ap.parse_args()

    goal = Path(args.goal_file).read_text(encoding="utf-8").strip()
    clarify_answer = ""
    if args.clarify_file and Path(args.clarify_file).exists():
        clarify_answer = Path(args.clarify_file).read_text(encoding="utf-8").strip()

    ev = Evidence(args.tag)
    (ART / f"{args.tag}_goal.txt").write_text(goal, encoding="utf-8")
    log(f"goal ({len(goal)} chars) submitted to group {args.group}")
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{COORD}/groups/{args.group}/messages", json={"goal": goal})
        if r.status_code != 200:
            log(f"SUBMIT FAILED rc={r.status_code} {r.text[:300]}")
            return 1
        wf = r.json()["workflow_id"]
        log(f"workflow_id={wf}")
        ev.event("submitted", {"workflow_id": wf, "group": args.group, "goal": goal})

        seen_events = 0
        seen_states: dict[str, str] = {}
        decided = 0
        answered = 0
        snap = None
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            try:
                snap = (await c.get(f"{COORD}/workflows/{wf}")).json()
            except httpx.HTTPError as e:
                log(f"poll error (retrying): {e}")
                await asyncio.sleep(3)
                continue
            # stream NEW workflow events
            events = snap.get("events") or []
            while seen_events < len(events):
                e = events[seen_events]
                seen_events += 1
                et = e.get("event_type")
                p = e.get("payload") or {}
                brief = {k: (str(v)[:160]) for k, v in list(p.items())[:8]}
                log(f"EVENT {et} {json.dumps(brief, ensure_ascii=False)}")
                ev.event(et, {"payload": p})
            # node status transitions
            for n in snap.get("nodes") or []:
                old = seen_states.get(n["node_id"])
                if old != n["status"]:
                    seen_states[n["node_id"]] = n["status"]
                    log(f"NODE {n.get('plan_key') or n['node_id'][:12]} "
                        f"agent={n.get('assigned_agent')} {old or '-'} -> {n['status']}")
                    ev.event("node_status", {"node_id": n["node_id"],
                                             "plan_key": n.get("plan_key"),
                                             "agent": n.get("assigned_agent"),
                                             "from": old, "to": n["status"]})
            st = snap["workflow"]["status"]
            if st in ("completed", "failed", "cancelled"):
                break
            # HITL approvals — operator decides (approve), logging every gate
            pend = (await c.get(f"{COORD}/approvals",
                                params={"workflow_id": wf, "status": "pending"})).json()
            for a in pend:
                if decided >= MAX_APPROVALS:
                    log(f"RUNAWAY GUARD: {decided} approvals exceeded, stopping")
                    ev.event("runaway_guard", {"decided": decided})
                    snap = None
                    break
                decided += 1
                log(f"APPROVAL#{decided} tool={a['tool_id']} node={a['node_id'][:16]} "
                    f"agent={a['agent_id']} args={a['summary'][:180]!r}")
                rr = await c.post(f"{COORD}/approvals/{a['approval_id']}/decide",
                                  json={"decision": "approve",
                                        "decided_by": "operator(midtest-driver)",
                                        "comment": f"midtest {args.tag} approval #{decided}"})
                rec = {"approval_id": a["approval_id"], "tool_id": a["tool_id"],
                       "node_id": a["node_id"], "agent_id": a["agent_id"],
                       "summary": a["summary"][:500], "decide_rc": rr.status_code,
                       "decided_at": time.strftime("%H:%M:%S")}
                ev.approvals.append(rec)
                ev.event("approval_decided", rec)
            if snap is None:
                break
            # clarifications — answer with documented operator defaults
            cls = (await c.get(f"{COORD}/clarifications",
                               params={"workflow_id": wf})).json()
            for q in cls:
                answered += 1
                log(f"CLARIFICATION from {q.get('agent_id')}: {q.get('question')}")
                ans = clarify_answer or ("Proceed with reasonable defaults and document "
                                         "your assumptions in the deliverable.")
                rr = await c.post(f"{COORD}/clarifications/{q['clarification_id']}/answer",
                                  json={"answer": ans,
                                        "answered_by": "operator(midtest-driver)"})
                rec = {"clarification_id": q["clarification_id"],
                       "agent_id": q.get("agent_id"), "question": q.get("question"),
                       "answer": ans[:1000], "rc": rr.status_code}
                ev.clarifications.append(rec)
                ev.event("clarification_answered", rec)
            await asyncio.sleep(3)

    if snap is None:
        log("RESULT: FAIL (runaway guard)")
        ev.close(wf, None)
        return 2
    st = snap["workflow"]["status"]
    final = snap["workflow"].get("final_result") or ""
    err = snap["workflow"].get("error") or ""
    log("=" * 70)
    log(f"WORKFLOW {wf} -> {st}  (approvals={decided}, clarifications={answered})")
    if err:
        log(f"error: {err[:400]}")
    for n in snap.get("nodes") or []:
        log(f"  node {n.get('plan_key') or n['node_id'][:12]:<22} "
            f"agent={str(n.get('assigned_agent')):<24} status={n['status']}")
    log("-" * 70)
    log("FINAL RESULT (first 3000 chars):")
    print(final[:3000], flush=True)
    ev.event("final", {"status": st, "error": err, "final_result_chars": len(final),
                       "approvals_decided": decided, "clarifications_answered": answered})
    ev.close(wf, snap)
    log(f"evidence saved under {ART} (tag={args.tag})")
    return 0 if st == "completed" else 3


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
