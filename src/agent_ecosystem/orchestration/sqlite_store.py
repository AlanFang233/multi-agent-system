"""SQLite-backed WorkflowStore — synchronous (same pattern as memory/store.py).

Uses WAL + busy_timeout + RLock for multi-process safety, just like the existing
MemoryDB. All methods are synchronous, so the Phase 2 scheduler and service
need zero changes (they already call the store interface directly).

Tables created: workflows, workflow_nodes, node_events, workflow_events,
approvals, clarifications, tool_reconciliations, a2a_tasks (full DDL below).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import NodeEvent, NodeStatus, Workflow, WorkflowNode, WorkflowStatus, utc_now
from .store import WorkflowStore

_SCHEMA_VERSION = 1
_V1_DDL = r"""
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS workflows (
    workflow_id TEXT PRIMARY KEY, goal TEXT NOT NULL, status TEXT NOT NULL,
    final_result TEXT, error TEXT, created_at TEXT NOT NULL, finished_at TEXT,
    metrics_json TEXT NOT NULL DEFAULT '{}', metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_workflows_created ON workflows(created_at);

CREATE TABLE IF NOT EXISTS workflow_nodes (
    node_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, plan_key TEXT NOT NULL,
    description TEXT NOT NULL, required_capabilities TEXT NOT NULL DEFAULT '[]',
    assigned_agent TEXT, dependencies TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL, status_version INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'generic', result TEXT, error TEXT,
    evidence_refs TEXT NOT NULL DEFAULT '[]', depth INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    lease_agent TEXT, lease_started_at TEXT, lease_timeout_seconds REAL
);
CREATE INDEX IF NOT EXISTS idx_nodes_workflow ON workflow_nodes(workflow_id, created_at);

CREATE TABLE IF NOT EXISTS node_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL, node_id TEXT,
    event_type TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_node_events ON node_events(workflow_id, id);

CREATE TABLE IF NOT EXISTS workflow_events (
    event_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, event_type TEXT NOT NULL,
    created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY, workflow_id TEXT, node_id TEXT, agent_id TEXT NOT NULL,
    tool_id TEXT NOT NULL, action TEXT NOT NULL, summary TEXT NOT NULL,
    arguments_json TEXT NOT NULL, argument_digest TEXT NOT NULL,
    risk_level TEXT NOT NULL, reason TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL, decision TEXT, created_at TEXT NOT NULL, expires_at TEXT,
    decided_at TEXT, decided_by TEXT, comment TEXT NOT NULL DEFAULT '',
    consumed_at TEXT, metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS clarifications (
    clarification_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, node_id TEXT NOT NULL,
    agent_id TEXT NOT NULL, question TEXT NOT NULL, context TEXT NOT NULL DEFAULT '',
    possible_interpretations_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL,
    answer TEXT, cancel_reason TEXT, created_at TEXT NOT NULL, expires_at TEXT,
    answered_at TEXT, answered_by TEXT, metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS tool_reconciliations (
    reconciliation_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, node_id TEXT NOT NULL,
    agent_id TEXT NOT NULL, tool_id TEXT NOT NULL, argument_digest TEXT NOT NULL,
    tool_call_id TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(workflow_id, node_id, tool_id, argument_digest)
);

CREATE TABLE IF NOT EXISTS a2a_tasks (
    task_id TEXT PRIMARY KEY, context_id TEXT, agent TEXT NOT NULL, state TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}', workflow_id TEXT, node_id TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _json_loads(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
    return raw


class SQLiteWorkflowStore(WorkflowStore):
    def __init__(self, path: str | Path = "data/orchestration.sqlite") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(fd)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._lock = threading.RLock()
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.executescript(_V1_DDL)
            ver = conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
            if ver is None:
                conn.execute("INSERT INTO schema_meta(key,value) VALUES('version','1')")
            conn.commit()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # -- WorkflowStore interface (all synchronous) -----------------------------

    def save_workflow(self, workflow: Workflow) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO workflows(workflow_id,goal,status,final_result,error,"
                "created_at,finished_at,metrics_json,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (workflow.workflow_id, workflow.goal, workflow.status.value,
                 workflow.final_result, workflow.error,
                 workflow.created_at.isoformat(),
                 workflow.finished_at.isoformat() if workflow.finished_at else None,
                 _json_dumps(workflow.metrics.model_dump()), _json_dumps(workflow.metadata)))
            conn.commit()

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
        return _row_to_workflow(row) if row else None

    def list_workflows(self, limit: int = 50) -> list[Workflow]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM workflows ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [_row_to_workflow(r) for r in rows]

    def save_node(self, node: WorkflowNode) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO workflow_nodes(node_id,workflow_id,plan_key,description,"
                "required_capabilities,assigned_agent,dependencies,status,status_version,"
                "attempt,priority,kind,result,error,evidence_refs,depth,created_at,started_at,"
                "finished_at,metadata_json,lease_agent,lease_started_at,lease_timeout_seconds) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (node.node_id, node.workflow_id, node.plan_key, node.description,
                 _json_dumps(node.required_capabilities), node.assigned_agent,
                 _json_dumps(node.dependencies), node.status.value,
                 int(node.metadata.get("status_version", 0)),
                 node.attempt, node.priority, node.kind, node.result, node.error,
                 _json_dumps(node.evidence_refs), node.depth,
                 node.created_at.isoformat(),
                 node.started_at.isoformat() if node.started_at else None,
                 node.finished_at.isoformat() if node.finished_at else None,
                 _json_dumps(node.metadata),
                 node.metadata.get("lease_agent"), node.metadata.get("lease_started_at"),
                 node.metadata.get("lease_timeout_seconds")))
            conn.commit()

    def get_node(self, node_id: str) -> WorkflowNode | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM workflow_nodes WHERE node_id=?", (node_id,)).fetchone()
        return _row_to_node(row) if row else None

    def list_nodes(self, workflow_id: str) -> list[WorkflowNode]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_nodes WHERE workflow_id=? ORDER BY created_at, node_id",
                (workflow_id,)).fetchall()
        return [_row_to_node(r) for r in rows]

    def append_event(self, workflow_id: str, event_type: str, *,
                     node_id: str | None = None, payload: dict | None = None) -> NodeEvent:
        created = utc_now()
        p = payload or {}
        with self._lock, self._connection() as conn:
            conn.execute(
                "INSERT INTO node_events(workflow_id,node_id,event_type,payload_json,created_at) "
                "VALUES(?,?,?,?,?)",
                (workflow_id, node_id, event_type, _json_dumps(p), created.isoformat()))
            conn.commit()
        return NodeEvent(workflow_id=workflow_id, node_id=node_id, event_type=event_type,
                         payload=p, created_at=created)

    def list_events(self, workflow_id: str, *, event_type: str | None = None) -> list[NodeEvent]:
        sql = "SELECT * FROM node_events WHERE workflow_id=?"
        params: list = [workflow_id]
        if event_type is not None:
            sql += " AND event_type=?"
            params.append(event_type)
        sql += " ORDER BY id"
        with self._connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [NodeEvent(workflow_id=r["workflow_id"], node_id=r["node_id"],
                          event_type=r["event_type"], payload=_json_loads(r["payload_json"]),
                          created_at=r["created_at"]) for r in rows]

    def get_flag(self, workflow_id: str, flag: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM workflow_events WHERE workflow_id=? AND event_type=? "
                "AND json_extract(metadata_json,'$.value')=true LIMIT 1",
                (workflow_id, flag)).fetchone()
        return bool(row)

    def set_flag(self, workflow_id: str, flag: str, value: bool) -> None:
        if not value:
            return
        eid = f"{workflow_id}_{flag}"
        with self._lock, self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO workflow_events(event_id,workflow_id,event_type,"
                "created_at,metadata_json) VALUES(?,?,?,?,?)",
                (eid, workflow_id, flag, _now_iso(), _json_dumps({"value": True})))
            conn.commit()

    def recover_expired_leases(self) -> list[str]:
        """Move RUNNING nodes with expired leases back to PENDING with replay_mode."""
        now = _now_iso()
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                "SELECT node_id FROM workflow_nodes WHERE status='running' "
                "AND lease_started_at IS NOT NULL AND lease_timeout_seconds IS NOT NULL "
                "AND datetime(lease_started_at,'+'||CAST(lease_timeout_seconds AS TEXT)||' seconds')<?",
                (now,)).fetchall()
            recovered = []
            for r in rows:
                conn.execute(
                    "UPDATE workflow_nodes SET status='pending',status_version=status_version+1,"
                    "error='recovered from expired lease',"
                    "metadata_json=json_set(metadata_json,'$.replay_mode',json('true'),"
                    "'$.recovered_from_running',json('true')),"
                    "lease_agent=NULL,lease_started_at=NULL WHERE node_id=?",
                    (r["node_id"],))
                recovered.append(r["node_id"])
            conn.commit()
        return recovered


def _row_to_workflow(row: sqlite3.Row) -> Workflow:
    return Workflow(
        workflow_id=row["workflow_id"], goal=row["goal"],
        status=WorkflowStatus(row["status"]), final_result=row["final_result"],
        error=row["error"], created_at=row["created_at"],
        finished_at=row["finished_at"], metadata=_json_loads(row["metadata_json"]))


def _row_to_node(row: sqlite3.Row) -> WorkflowNode:
    meta = _json_loads(row["metadata_json"]) if isinstance(row.get("metadata_json"), str) else {}
    return WorkflowNode(
        node_id=row["node_id"], workflow_id=row["workflow_id"], plan_key=row["plan_key"],
        description=row["description"],
        required_capabilities=_json_loads(row["required_capabilities"]),
        assigned_agent=row["assigned_agent"],
        dependencies=_json_loads(row["dependencies"]),
        status=NodeStatus(row["status"]), attempt=row["attempt"],
        priority=row["priority"], kind=row["kind"],
        result=row["result"], error=row["error"],
        evidence_refs=_json_loads(row["evidence_refs"]), depth=row["depth"],
        created_at=row["created_at"], started_at=row["started_at"],
        finished_at=row["finished_at"], metadata=meta)