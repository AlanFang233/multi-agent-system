"""Phase 0: SQLite memory read/write, status transition, dedup, agent isolation."""
import sys

from agent_ecosystem.memory.store import MemoryDB


def main() -> int:
    db = MemoryDB("data/phase0_memory_check.sqlite")
    content = "Docker sandbox 离线执行 Python 成功，网络被禁用"
    mid = db.put_memory("coding", "procedural", content, "t0", status="pending")
    print("added memory_id:", mid)

    before = db.search("coding", "Docker sandbox 离线执行")
    print("search before verify (expect empty):", before)

    ok_review = db.set_memory_status("coding", mid, "verified")
    print("review->verified:", ok_review)

    hits = db.search("coding", "Docker sandbox 离线执行")
    print("search after verify:", [(h["memory_id"], h["status"], h["evidence_count"]) for h in hits])

    cross = db.search("research", "Docker sandbox 离线执行")
    print("cross-agent research isolation (expect empty):", cross)

    # Re-put identical content: same deterministic id, evidence_count bumps, no duplicate row.
    mid2 = db.put_memory("coding", "procedural", content, "t1", status="pending")
    same_id = mid == mid2
    after_dedup = db.search("coding", "Docker sandbox 离线执行")
    print("dedup same_id:", same_id, "evidence_count now:", after_dedup[0]["evidence_count"] if after_dedup else None)

    # Task ledger + events + checkpoint round-trip (used by the runner).
    db.begin("coding", "task-x", "demo goal")
    db.event("coding", "task-x", "tool_result", {"tool_id": "sandbox:run_python", "is_error": False})
    db.checkpoint("coding", "task-x", [{"role": "user", "content": "demo goal"}], "sum", ["sandbox:run_python"])
    db.finish("coding", "task-x", "done", ["sandbox:run_python"])
    cp = db.load_checkpoint("coding", "task-x")
    task = db.task("coding", "task-x")
    print("task status:", task["status"], "| checkpoint msgs:", len(cp["messages"]), "| events:", len(db.events("coding", "task-x")))

    print("stats:", db.stats())
    ok = ok_review and bool(hits) and not before and not cross and same_id and task["status"] == "completed" and cp is not None
    print("MEMORY_CHECK:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
