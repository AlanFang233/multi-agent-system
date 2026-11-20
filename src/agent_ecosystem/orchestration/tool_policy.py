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
    Returns: 'approved', 'denied', 'expired', 'pending', 'not_found'"""
    digest = _digest(arguments)
    conn = sqlite3.connect(db_path, timeout=10)

    row = conn.execute(
        "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
    ).fetchone()
    if row is None:
        conn.close(); return "not_found"

    status = row["status"]
    if status == "expired":
        conn.close(); return "expired"
    if status == "rejected" or status == "cancelled":
        conn.close(); return "denied"
    if status == "pending":
        conn.close(); return "pending"
    if status != "approved":
        conn.close(); return "denied"

    # Validate bindings
    if row["argument_digest"] and row["argument_digest"] != digest:
        conn.close(); return "denied"
    if row["tool_id"] and row["tool_id"] != tool_id:
        conn.close(); return "denied"
    if row["workflow_id"] and row["workflow_id"] != workflow_id:
        conn.close(); return "denied"
    if row["node_id"] and row["node_id"] != node_id:
        conn.close(); return "denied"
    if row["agent_id"] and row["agent_id"] != agent_id:
        conn.close(); return "denied"

    # Check consumed_at (single-use)
    if row["consumed_at"] is not None:
        conn.close(); return "denied"

    # CAS consume
    cur = conn.execute(
        "UPDATE approvals SET consumed_at=? WHERE approval_id=? AND consumed_at IS NULL",
        (_now_iso(), approval_id),
    )
    conn.commit()
    conn.close()
    return "approved" if cur.rowcount == 1 else "denied"