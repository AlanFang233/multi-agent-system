"""HITL services: Approval + Clarification + WorkflowControl (Phase 3).

Adapted from AgentForge@7cee900 interaction/{approval_service,clarification_service,repository}.py
and orchestration/control.py.

All state lives in the same ``data/orchestration.sqlite`` used by SQLiteWorkflowStore
(tables ``approvals`` / ``clarifications`` / ``workflow_events`` created in its DDL).

Key adaptations for the distributed A2A architecture:
- Approval digest binding (tool_id + argument_digest) is identical to AgentForge's design;
  the single-use CAS and scope binding remain the same.
- Clarification answer is injected as an A2A metadata field on node re-dispatch (Phase 2
  x-workflow metadata already carries the ``approval_tokens`` and ``replay_mode`` fields).
- WorkflowControl gains **node-level** pause/resume/cancel (Phase 2 state machine already
  reserves WAITING_INPUT for this purpose; the scheduler's main loop treats WAITING_INPUT
  as a non-ready non-terminal node, so dependants naturally wait while siblings continue).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import NodeStatus, Workflow, WorkflowNode, utc_now


# ------------------------------------------------------------------ digest --


def arguments_digest(arguments: dict[str, Any]) -> str:
    """Canonical SHA-256 digest binding a approval to exact tool parameters."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------- Approval --


class ApprovalStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    DENIED = "denied"


class RiskLevel:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ApprovalRequest:
    approval_id: str
    workflow_id: str | None
    node_id: str | None
    agent_id: str
    tool_id: str
    action: str
    summary: str
    arguments: dict[str, Any] = field(default_factory=dict)
    argument_digest: str = ""
    risk_level: str = RiskLevel.HIGH
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    status: str = ApprovalStatus.PENDING
    decision: str | None = None
    created_at: str = ""
    expires_at: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None
    comment: str = ""
    consumed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None or not self.expires_at:
            return False
        try:
            return self.expires_at < utc_now().isoformat()
        except Exception:
            return False


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class ApprovalRepository:
    """SQLite access for approvals (orchestration.sqlite shared with SQLiteWorkflowStore)."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    def _connect(self):
        import sqlite3
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, req: ApprovalRequest) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO approvals(
                   approval_id, workflow_id, node_id, agent_id, tool_id, action,
                   summary, arguments_json, argument_digest, risk_level, reason,
                   details_json, status, decision, created_at, expires_at,
                   decided_at, decided_by, comment, consumed_at, metadata_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    req.approval_id, req.workflow_id, req.node_id, req.agent_id,
                    req.tool_id, req.action, req.summary,
                    json.dumps(req.arguments, ensure_ascii=False, default=str),
                    req.argument_digest or "", req.risk_level, req.reason,
                    json.dumps(req.details, ensure_ascii=False, default=str),
                    req.status, req.decision, req.created_at, req.expires_at,
                    req.decided_at, req.decided_by, req.comment, req.consumed_at,
                    json.dumps(req.metadata, ensure_ascii=False, default=str),
                ),
            )
            conn.commit()

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._row_to(self._fetchone(approval_id))

    def _fetchone(self, approval_id: str):
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()

    def list_pending(self, workflow_id: str | None = None) -> list[ApprovalRequest]:
        with self._connect() as conn:
            sql = "SELECT * FROM approvals WHERE status='pending'"
            params: list = []
            if workflow_id:
                sql += " AND workflow_id=?"
                params.append(workflow_id)
            sql += " ORDER BY created_at ASC"
            return [self._row_to(r) for r in conn.execute(sql, tuple(params)).fetchall()]

    def list_all(self, workflow_id: str | None = None, status: str | None = None,
                 limit: int = 50) -> list[ApprovalRequest]:
        with self._connect() as conn:
            sql = "SELECT * FROM approvals WHERE 1=1"
            params: list = []
            if workflow_id:
                sql += " AND workflow_id=?"
                params.append(workflow_id)
            if status:
                sql += " AND status=?"
                params.append(status)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            return [self._row_to(r) for r in conn.execute(sql, tuple(params)).fetchall()]

    def consume(self, approval_id: str) -> bool:
        """Atomically mark an APPROVED token as consumed (single-use CAS)."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE approvals SET consumed_at=? WHERE approval_id=? "
                "AND consumed_at IS NULL AND status='approved'",
                (_now_iso(), approval_id),
            )
            conn.commit()
            return cur.rowcount == 1

    def expire_overdue(self) -> list[str]:
        now = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE approvals SET status='expired', decision='expired' "
                "WHERE status='pending' AND expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            conn.commit()
            ids_row = conn.execute(
                "SELECT approval_id FROM approvals WHERE status='expired' AND decision='expired'"
            ).fetchall()
            return [r[0] for r in ids_row if cur.rowcount]

    def cancel_for_workflow(self, workflow_id: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "UPDATE approvals SET status='cancelled', decision='cancelled' "
                "WHERE workflow_id=? AND status='pending'",
                (workflow_id,),
            )
            conn.commit()
            ids_row = conn.execute(
                "SELECT approval_id FROM approvals WHERE workflow_id=? AND status='cancelled'",
                (workflow_id,),
            ).fetchall()
            return [r[0] for r in ids_row]

    @staticmethod
    def _row_to(row) -> ApprovalRequest | None:
        if row is None:
            return None
        return ApprovalRequest(
            approval_id=row["approval_id"], workflow_id=row["workflow_id"],
            node_id=row["node_id"], agent_id=row["agent_id"],
            tool_id=row["tool_id"], action=row["action"], summary=row["summary"],
            arguments=json.loads(row["arguments_json"]) if row["arguments_json"] else {},
            argument_digest=row["argument_digest"], risk_level=row["risk_level"],
            reason=row["reason"], details=json.loads(row["details_json"]) if row["details_json"] else {},
            status=row["status"], decision=row["decision"], created_at=row["created_at"],
            expires_at=row["expires_at"], decided_at=row["decided_at"],
            decided_by=row["decided_by"], comment=row["comment"],
            consumed_at=row["consumed_at"],
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        )


class ApprovalService:
    """Human-in-the-loop approval gate backed by the orchestration store."""

    def __init__(self, repo: ApprovalRepository, *, ttl_seconds: int = 3600) -> None:
        self.repo = repo
        self.ttl_seconds = ttl_seconds

    def request(self,
                *, workflow_id: str | None, node_id: str | None,
                agent_id: str, tool_id: str, arguments: dict[str, Any],
                risk_level: str = RiskLevel.HIGH, reason: str = "",
                action: str = "",
                ) -> ApprovalRequest:
        import uuid as _uuid
        digest = arguments_digest(arguments)
        now_str = _now_iso()
        expires = (datetime.datetime.now(datetime.timezone.utc) +
                   datetime.timedelta(seconds=self.ttl_seconds)).isoformat()
        req = ApprovalRequest(
            approval_id=f"approval_{_uuid.uuid4().hex}",
            workflow_id=workflow_id, node_id=node_id, agent_id=agent_id,
            tool_id=tool_id, action=action or tool_id,
            summary=json.dumps(arguments, ensure_ascii=False, default=str)[:2000],
            arguments=arguments, argument_digest=digest,
            risk_level=risk_level, reason=reason,
            status=ApprovalStatus.PENDING, created_at=now_str,
            expires_at=expires,
        )
        self.repo.save(req)
        return req

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self.repo.get(approval_id)

    def decide(self, approval_id: str, status: str, *,
               decided_by: str = "human", comment: str = "") -> ApprovalRequest | None:
        req = self.repo.get(approval_id)
        if req is None or req.status != ApprovalStatus.PENDING:
            return req
        req.status = status
        req.decision = status
        req.decided_at = _now_iso()
        req.decided_by = decided_by
        req.comment = comment
        self.repo.save(req)
        return req

    def consume(self, approval_id: str,
                *, tool_id: str, argument_digest: str,
                workflow_id: str | None, node_id: str | None, agent_id: str,
                ) -> str:
        """Validate and consume an approved token once. Returns status string."""
        req = self.repo.get(approval_id)
        if req is None:
            return ApprovalStatus.DENIED
        if req.consumed_at is not None:
            return ApprovalStatus.DENIED
        if req.argument_digest and req.argument_digest != argument_digest:
            return ApprovalStatus.DENIED
        if req.tool_id and req.tool_id != tool_id:
            return ApprovalStatus.DENIED
        if req.workflow_id and req.workflow_id != workflow_id:
            return ApprovalStatus.DENIED
        if req.node_id and req.node_id != node_id:
            return ApprovalStatus.DENIED
        if req.agent_id and req.agent_id != agent_id:
            return ApprovalStatus.DENIED
        if req.is_expired:
            self.repo.expire_overdue()
            return ApprovalStatus.EXPIRED
        if req.status != ApprovalStatus.APPROVED:
            return req.status
        if not self.repo.consume(approval_id):
            return ApprovalStatus.DENIED
        return ApprovalStatus.APPROVED

    def expire(self) -> list[str]:
        return self.repo.expire_overdue()

    def cancel_workflow(self, workflow_id: str) -> list[str]:
        return self.repo.cancel_for_workflow(workflow_id)

    def list_pending(self, workflow_id: str | None = None) -> list[ApprovalRequest]:
        return self.repo.list_pending(workflow_id)

    def list_all(self, workflow_id: str | None = None,
                 status: str | None = None, limit: int = 50) -> list[ApprovalRequest]:
        return self.repo.list_all(workflow_id, status=status, limit=limit)


# ------------------------------------------------------------ Clarification --


class ClarificationStatus:
    OPEN = "open"
    ANSWERED = "answered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class ClarificationRequest:
    clarification_id: str
    workflow_id: str
    node_id: str
    agent_id: str
    question: str
    context: str = ""
    possible_interpretations: list[str] = field(default_factory=list)
    status: str = ClarificationStatus.OPEN
    answer: str | None = None
    cancel_reason: str | None = None
    created_at: str = ""
    expires_at: str | None = None
    answered_at: str | None = None
    answered_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ClarificationRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    def _connect(self):
        import sqlite3
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, req: ClarificationRequest) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO clarifications(
                   clarification_id, workflow_id, node_id, agent_id, question,
                   context, possible_interpretations_json, status, answer,
                   cancel_reason, created_at, expires_at, answered_at, answered_by,
                   metadata_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    req.clarification_id, req.workflow_id, req.node_id, req.agent_id,
                    req.question, req.context,
                    json.dumps(req.possible_interpretations, ensure_ascii=False),
                    req.status, req.answer, req.cancel_reason,
                    req.created_at, req.expires_at, req.answered_at, req.answered_by,
                    json.dumps(req.metadata, ensure_ascii=False, default=str),
                ),
            )
            conn.commit()

    def get(self, clarification_id: str) -> ClarificationRequest | None:
        return self._row_to(self._fetchone(clarification_id))

    def _fetchone(self, cid: str):
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM clarifications WHERE clarification_id=?", (cid,)
            ).fetchone()

    def list_open(self, workflow_id: str | None = None) -> list[ClarificationRequest]:
        with self._connect() as conn:
            sql = "SELECT * FROM clarifications WHERE status='open'"
            params: list = []
            if workflow_id:
                sql += " AND workflow_id=?"
                params.append(workflow_id)
            sql += " ORDER BY created_at ASC"
            return [self._row_to(r) for r in conn.execute(sql, tuple(params)).fetchall()]

    def expire_overdue(self) -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE clarifications SET status='expired' WHERE status='open' "
                "AND expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            conn.commit()

    def cancel_for_workflow(self, workflow_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE clarifications SET status='cancelled' WHERE workflow_id=? AND status='open'",
                (workflow_id,),
            )
            conn.commit()

    @staticmethod
    def _row_to(row) -> ClarificationRequest | None:
        if row is None:
            return None
        return ClarificationRequest(
            clarification_id=row["clarification_id"], workflow_id=row["workflow_id"],
            node_id=row["node_id"], agent_id=row["agent_id"],
            question=row["question"], context=row["context"],
            possible_interpretations=json.loads(row["possible_interpretations_json"]) if row["possible_interpretations_json"] else [],
            status=row["status"], answer=row["answer"],
            cancel_reason=row["cancel_reason"],
            created_at=row["created_at"], expires_at=row["expires_at"],
            answered_at=row["answered_at"], answered_by=row["answered_by"],
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        )


class ClarificationService:
    def __init__(self, repo: ClarificationRepository, *, ttl_seconds: int = 86400) -> None:
        self.repo = repo
        self.ttl_seconds = ttl_seconds

    def create(self, *, workflow_id: str, node_id: str, agent_id: str,
               question: str, context: str = "",
               possible_interpretations: list[str] | None = None,
               ) -> ClarificationRequest:
        import uuid as _uuid
        now_str = _now_iso()
        expires = (datetime.datetime.now(datetime.timezone.utc) +
                   datetime.timedelta(seconds=self.ttl_seconds)).isoformat()
        req = ClarificationRequest(
            clarification_id=f"clarify_{_uuid.uuid4().hex}",
            workflow_id=workflow_id, node_id=node_id, agent_id=agent_id,
            question=question, context=context,
            possible_interpretations=possible_interpretations or [],
            status=ClarificationStatus.OPEN, created_at=now_str,
            expires_at=expires,
        )
        self.repo.save(req)
        return req

    def get(self, clarification_id: str) -> ClarificationRequest | None:
        return self.repo.get(clarification_id)

    def answer(self, clarification_id: str, answer: str,
               *, answered_by: str = "human") -> ClarificationRequest | None:
        req = self.repo.get(clarification_id)
        if req is None or req.status != ClarificationStatus.OPEN:
            return req
        if not answer or not answer.strip():
            return None
        req.status = ClarificationStatus.ANSWERED
        req.answer = answer.strip()
        req.answered_at = _now_iso()
        req.answered_by = answered_by
        self.repo.save(req)
        return req

    def expire(self) -> None:
        self.repo.expire_overdue()

    def cancel_workflow(self, workflow_id: str) -> None:
        self.repo.cancel_for_workflow(workflow_id)

    def list_open(self, workflow_id: str | None = None) -> list[ClarificationRequest]:
        return self.repo.list_open(workflow_id)


# -------------------------------------------------------------- control flags --


class WorkflowControlService:
    """Phase 3 enriched workflow/ node-level control flags backed by
    the durable ``workflow_events`` table (SQLiteWorkflowStore DDL)."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    def _connect(self):
        import sqlite3
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _is_set(self, workflow_id: str, flag: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM workflow_events WHERE workflow_id=? AND event_type=? "
                "AND json_extract(metadata_json, '$.value') = true LIMIT 1",
                (workflow_id, flag),
            ).fetchone()
            return bool(row)

    def is_paused(self, workflow_id: str) -> bool:
        return self._is_set(workflow_id, "pause_requested")

    def is_cancelled(self, workflow_id: str) -> bool:
        return self._is_set(workflow_id, "cancel_requested")

    def pause(self, workflow_id: str) -> None:
        self._upsert_flag(workflow_id, "pause_requested")

    def resume(self, workflow_id: str) -> None:
        self._remove_flag(workflow_id, "pause_requested")

    def cancel(self, workflow_id: str) -> None:
        self._upsert_flag(workflow_id, "cancel_requested")

    def _upsert_flag(self, workflow_id: str, flag: str) -> None:
        with self._connect() as conn:
            eid = f"{workflow_id}_{flag}"
            conn.execute(
                "INSERT OR REPLACE INTO workflow_events(event_id, workflow_id, event_type, created_at, metadata_json) VALUES(?,?,?,?,?)",
                (eid, workflow_id, flag, _now_iso(),
                 json.dumps({"value": True, "note": flag})),
            )
            conn.commit()

    def _remove_flag(self, workflow_id: str, flag: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM workflow_events WHERE workflow_id=? AND event_type=?",
                (workflow_id, flag),
            )
            conn.commit()