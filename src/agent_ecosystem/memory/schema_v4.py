"""Phase 4 memory schema migration: extends long_term_memories and adds
memory_candidates / retrieval_events / memory_feedback tables.

Run once per DB open — idempotent (IF NOT EXISTS / try-ALTER).
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 4

_MIGRATION_SQL = """
-- P4: extend long_term_memories (12 columns, all with safe defaults)
ALTER TABLE long_term_memories ADD COLUMN namespace TEXT NOT NULL DEFAULT 'experience';
ALTER TABLE long_term_memories ADD COLUMN verification_level TEXT NOT NULL DEFAULT 'UNVERIFIED';
ALTER TABLE long_term_memories ADD COLUMN importance REAL NOT NULL DEFAULT 0.5;
ALTER TABLE long_term_memories ADD COLUMN confidence REAL NOT NULL DEFAULT 0.15;
ALTER TABLE long_term_memories ADD COLUMN source_json TEXT;
ALTER TABLE long_term_memories ADD COLUMN metadata_json TEXT;
ALTER TABLE long_term_memories ADD COLUMN embedding_json TEXT;
ALTER TABLE long_term_memories ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE long_term_memories ADD COLUMN last_accessed_at TEXT;
ALTER TABLE long_term_memories ADD COLUMN expires_at TEXT;
ALTER TABLE long_term_memories ADD COLUMN promotion_state TEXT NOT NULL DEFAULT 'none';
ALTER TABLE long_term_memories ADD COLUMN dedupe_key TEXT;

CREATE TABLE IF NOT EXISTS memory_candidates (
    candidate_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
    namespace TEXT NOT NULL, signature TEXT NOT NULL,
    verification_level TEXT NOT NULL, content TEXT NOT NULL,
    procedure_json TEXT NOT NULL, support_count INTEGER NOT NULL DEFAULT 1,
    evidence_workflow_ids_json TEXT NOT NULL DEFAULT '[]',
    evidence_tool_call_ids_json TEXT NOT NULL DEFAULT '[]',
    evidence_task_ids_json TEXT NOT NULL DEFAULT '[]',
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    promoted_memory_id TEXT, status TEXT NOT NULL DEFAULT 'candidate'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_signature ON memory_candidates(agent_id, signature);

CREATE TABLE IF NOT EXISTS retrieval_events (
    retrieval_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
    namespace TEXT NOT NULL, query TEXT NOT NULL,
    memory_ids_json TEXT NOT NULL, workflow_id TEXT, task_id TEXT,
    execution_id TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_retrieval_scope ON retrieval_events(workflow_id, task_id, execution_id);

CREATE TABLE IF NOT EXISTS memory_feedback (
    feedback_id TEXT PRIMARY KEY, retrieval_id TEXT NOT NULL,
    memory_id TEXT NOT NULL, workflow_id TEXT, task_id TEXT,
    execution_id TEXT, agent_id TEXT NOT NULL,
    used INTEGER NOT NULL, success INTEGER NOT NULL,
    confidence_delta REAL NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(retrieval_id, memory_id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_workflow ON memory_feedback(workflow_id, created_at);

-- dedupe partial unique index (only where dedupe_key is set)
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedupe ON long_term_memories(agent_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL;

-- schema version tracking
INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', '""" + str(SCHEMA_VERSION) + """');
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    ver = conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
    if ver is not None and int(ver[0]) >= SCHEMA_VERSION:
        return
    for stmt in _MIGRATION_SQL.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            if "duplicate column" in str(exc).lower() or "already exists" in str(exc).lower():
                continue
            raise
    conn.commit()