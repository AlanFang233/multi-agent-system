"""SQLite-backed A2A TaskStore implementing a2a-sdk>=1.3 TaskStore protocol.

Replaces the ephemeral ``InMemoryTaskStore`` with a durable store that shares the
same ``data/orchestration.sqlite`` used by SQLiteWorkflowStore (the ``a2a_tasks``
table is already created in that store's DDL).

Every task is keyed by its A2A ``task.id``. When the Coordinator dispatches a
DAG node it embeds ``{workflow_id}:{node_id}:{attempt}`` as the A2A message's
``task_id`` so the agent-side Memory runner and this store agree on the
idempotency key without extra mapping tables.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class SQLiteTaskStore:
    """Minimal durable TaskStore conforming to a2a-sdk's TaskStore interface.

    The a2a-sdk 1.3.0 TaskStore requires ``save(task: Task, context)``,
    ``get(task_id: str) -> Task | None``, ``delete(task_id: str)`` and
    ``list(context) -> list[Task]``. Tasks are protobuf ``Task`` objects;
    we serialise them to JSON via ``Task.to_json()`` / ``Task.from_json()``.
    """

    def __init__(self, path: str | Path = "data/orchestration.sqlite") -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(
                f"orchestration database not found at {self.path}; "
                "start the coordinator once to create it"
            )
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # -- TaskStore protocol ---------------------------------------------------

    def save(self, task: Any, context: Any) -> None:
        """Persist an a2a Task proto."""
        from a2a.types import Task as A2ATask
        if isinstance(task, A2ATask):
            payload = A2ATask.to_json(task)
        else:
            payload = json.dumps(_safe_dict(task), ensure_ascii=False, default=str)
        task_id = str(getattr(task, "id", "") or "")
        cid = str(getattr(context, "context_id", "") or "")
        agent = str(getattr(context, "agent", "") or "")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO a2a_tasks(task_id, context_id, agent, "
                "state, payload_json, workflow_id, node_id, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
                (
                    task_id, cid or "", agent or "",
                    str(getattr(task, "status", {}).get("state", "") or ""),
                    payload,
                    getattr(task, "metadata", {}).get("workflow_id", ""),
                    getattr(task, "metadata", {}).get("node_id", ""),
                ),
            )
            conn.commit()

    def get(self, task_id: str) -> Any | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json, state FROM a2a_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        return _DictTask(task_id=task_id, state=row["state"],
                         payload_raw=row["payload_json"])

    def delete(self, task_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM a2a_tasks WHERE task_id=?", (task_id,))
            conn.commit()

    def list(self, context: Any) -> list[Any]:
        agent = str(getattr(context, "agent", "") or "")
        with self._connect() as conn:
            if agent:
                rows = conn.execute(
                    "SELECT task_id, payload_json, state FROM a2a_tasks WHERE agent=? ORDER BY created_at DESC",
                    (agent,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT task_id, payload_json, state FROM a2a_tasks ORDER BY created_at DESC"
                ).fetchall()
        return [_DictTask(task_id=r["task_id"], state=r["state"],
                          payload_raw=r["payload_json"]) for r in rows]


class _DictTask:
    """Lightweight Task-like object that satisfies what the a2a server expects."""
    def __init__(self, task_id: str, state: str, payload_raw: str) -> None:
        self.id = task_id
        self.context_id = ""
        payload = json.loads(payload_raw) if payload_raw else {}
        st = payload.get("status", {})
        self.status = _DictStatus(
            state=state or st.get("state", ""),
            message=st.get("message", {}),
            timestamp=payload.get("created_at", ""),
        )
        self.artifacts = payload.get("artifacts", [])
        self.metadata = payload.get("metadata", {})
        self.history = payload.get("history", [])


class _DictStatus:
    def __init__(self, state: str, message: dict, timestamp: str) -> None:
        self.state = state
        self.message = message
        self.timestamp = timestamp


def _safe_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_json"):
        raw = obj.to_json()
        if isinstance(raw, str):
            return json.loads(raw)
        return raw
    if hasattr(obj, "__dict__"):
        out = {}
        for k, v in obj.__dict__.items():
            if not k.startswith("_"):
                try:
                    out[k] = _safe_dict(v)
                except Exception:
                    out[k] = str(v)
        return out
    return {"value": str(obj)}