"""Minimal tool approval gate for Phase 3.

Injected into the agent's tool-call path when the agent is executing a
workflow node (x-workflow metadata present). High-risk tools (file overwrite,
edit, delete, git push, sandbox execution) are BLOCKED unless a valid,
consumed-once approval exists in the shared orchestration DB.

This gate is the ONLY path through which a DAG-node agent can invoke a tool —
the Coordinator cannot enforce tool-level approval because the actual MCP
call happens inside the agent's process. This module closes that gap.

For Phase 3 scope the risk list is hardcoded; Phase 5 replaces it with a
config-driven ToolGovernance overlay.
"""
from __future__ import annotations

import hashlib, json, sqlite3, uuid

HIGH_RISK_TOOLS = frozenset({
    "filesystem_code:write_file",
    "filesystem_code:edit_file",
    "filesystem_code:create_directory",
    "sandbox:run_python",
    "sandbox:run_pytest",
})

def _digest(args: dict) -> str:
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_high_risk(tool_id: str) -> bool:
    return tool_id in HIGH_RISK_TOOLS


def create_approval(
    db_path: str, *,
    workflow_id: str, node_id: str, agent_id: str,
    tool_id: str, arguments: dict,
) -> str:
    """Persist a PENDING approval and return its id. The agent MUST suspend
    the A2A task (INPUT_REQUIRED) after this call and NOT execute the tool."""
    approval_id = f"approval_{uuid.uuid4().hex}"
    digest = _digest(arguments)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute(
        """INSERT INTO approvals(approval_id, workflow_id, node_id, agent_id,
           tool_id, action, summary, arguments_json, argument_digest,
           risk_level, reason, status, created_at, expires_at, comment,
           details_json, metadata_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'','{}','{}')""",
        (approval_id, workflow_id, node_id, agent_id, tool_id, tool_id,
         json.dumps(arguments, ensure_ascii=False, default=str)[:2000],
         json.dumps(arguments, ensure_ascii=False, default=str),
         digest, "high", "high-risk tool requires human approval",
         "pending", _now_iso(),
         (__import__("datetime").datetime.now(__import__("datetime").timezone.utc) +
          __import__("datetime").timedelta(seconds=3600)).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return approval_id


def check_and_consume_approval(
    db_path: str, *,
    approval_id: str, tool_id: str, arguments: dict,
    workflow_id: str, node_id: str, agent_id: str,
) -> str:
    """Validate an approval and atomically consume it if valid.
    Returns: 'approved' | 'rejected' (human said no / invalid state)
           | 'mismatch' (binding or arguments differ from what was approved)
           | 'expired' | 'pending' | 'consumed' | 'not_found'"""
    digest = _digest(arguments)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
    ).fetchone()
    if row is None:
        conn.close(); return "not_found"

    status = row["status"]
    if status == "expired":
        conn.close(); return "expired"
    if status in ("rejected", "cancelled"):
        conn.close(); return "rejected"
    if status == "pending":
        conn.close(); return "pending"
    if status != "approved":
        conn.close(); return "rejected"

    # Validate bindings — a mismatch means this call is NOT what the human
    # approved (e.g. a non-deterministic LLM regenerated different arguments):
    # fail closed and let the caller request a fresh approval.
    if row["argument_digest"] and row["argument_digest"] != digest:
        conn.close(); return "mismatch"
    if row["tool_id"] and row["tool_id"] != tool_id:
        conn.close(); return "mismatch"
    if row["workflow_id"] and row["workflow_id"] != workflow_id:
        conn.close(); return "mismatch"
    if row["node_id"] and row["node_id"] != node_id:
        conn.close(); return "mismatch"
    if row["agent_id"] and row["agent_id"] != agent_id:
        conn.close(); return "mismatch"

    # Check consumed_at (single-use)
    if row["consumed_at"] is not None:
        conn.close(); return "consumed"

    # CAS consume
    cur = conn.execute(
        "UPDATE approvals SET consumed_at=? WHERE approval_id=? AND consumed_at IS NULL",
        (_now_iso(), approval_id),
    )
    conn.commit()
    conn.close()
    return "approved" if cur.rowcount == 1 else "consumed"


def find_reusable_approval(
    db_path: str, *,
    workflow_id: str, node_id: str, agent_id: str,
    tool_id: str, arguments: dict,
) -> tuple[str, str] | None:
    """Find an unconsumed approval whose 5-way binding matches this exact call.

    Returns ``(approval_id, status)`` or ``None``. Lets a re-blocked node reuse
    the approval a human already decided on instead of accumulating duplicates
    (approved rows are preferred over pending ones)."""
    digest = _digest(arguments)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """SELECT approval_id, status FROM approvals
           WHERE workflow_id=? AND node_id=? AND agent_id=? AND tool_id=?
             AND argument_digest=? AND consumed_at IS NULL
             AND status IN ('approved','pending')
           ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END,
                    created_at DESC LIMIT 1""",
        (workflow_id, node_id, agent_id, tool_id, digest),
    ).fetchone()
    conn.close()
    return (row["approval_id"], row["status"]) if row else None