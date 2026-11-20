#!/usr/bin/env python3
"""Mock-mode regression probe for the approval-replay path (runner fix).

Submits the primes workflow, auto-approves every gate, then asserts:
  1. workflow COMPLETED
  2. >=2 approvals, all consumed exactly once
  3. real sandbox execution (PRIME_COUNT=25 in tool_results)
  4. batch_replay events present (resume replayed the approved call verbatim)
Exit 0 only if all hold. Requires services from scripts/phase3_start.sh.
"""
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
COORD = "http://127.0.0.1:8000"
MEM_DB = ROOT / "data" / "agent_memory.sqlite"
ORCH_DB = ROOT / "data" / "orchestration.sqlite"
GOAL = ("Write a Python program to calculate the prime numbers from 1 to 100, "
        "actually execute it, and verify the results.")


async def main() -> int:
    async with httpx.AsyncClient(timeout=60) as c:
        wf = (await c.post(f"{COORD}/workflows", json={"goal": GOAL})).json()["workflow_id"]
        print("wf:", wf, flush=True)
        deadline = time.monotonic() + 900
        decided = 0
        snap = None
        while time.monotonic() < deadline:
            s = (await c.get(f"{COORD}/workflows/{wf}")).json()
            st = s["workflow"]["status"]
            if st in ("completed", "failed", "cancelled"):
                snap = s
                break
            pend = (await c.get(f"{COORD}/approvals",
                                params={"workflow_id": wf, "status": "pending"})).json()
            for a in pend:
                await c.post(f"{COORD}/approvals/{a['approval_id']}/decide",
                             json={"decision": "approve", "decided_by": "replay_probe"})
                decided += 1
                print(f"  approved {a['approval_id']} ({a['tool_id']})", flush=True)
            await asyncio.sleep(2)
    if snap is None:
        print("PROBE: FAIL (timeout)")
        return 1
    st = snap["workflow"]["status"]
    blob = json.dumps(snap, ensure_ascii=False)

    def q(db, sql, args=()):
        conn = sqlite3.connect(str(db), timeout=10)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    apr = q(ORCH_DB, "SELECT status, consumed_at FROM approvals WHERE workflow_id=?", (wf,))
    consumed = [a for a in apr if a[1]]
    execs = q(MEM_DB, "SELECT tool_id FROM tool_results WHERE task_id LIKE ? "
                      "AND body LIKE '%PRIME_COUNT=25%'", (wf + ":%",))
    replays = q(MEM_DB, "SELECT COUNT(*) FROM task_events WHERE task_id LIKE ? "
                        "AND event_type='batch_replay'", (wf + ":%",))[0][0]
    ok = (st == "completed"
          and len(apr) >= 2 and all(a[0] == "approved" for a in apr) and len(consumed) >= 2
          and any(t[0] == "sandbox:run_python" for t in execs)
          and replays >= 1
          and "PRIME_COUNT=25" in blob and "PRIME_SUM=1060" in blob)
    print(f"status={st} approvals={len(apr)} consumed={len(consumed)} decided={decided}")
    print(f"prime_exec_rows={len(execs)} batch_replay_events={replays}")
    print("PROBE:", "PASS" if ok else "FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
