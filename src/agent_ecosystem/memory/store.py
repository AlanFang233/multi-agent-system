"""Single-file SQLite task ledger, checkpoints, tool result blobs and agent memories.

Requires only the Python standard library. SQLite WAL handles multiple local
processes with one writer at a time; the first release is for a trusted WSL host.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def redact(value: str) -> str:
    """Best-effort scrubbing, NOT a comprehensive DLP/security system."""
    value = re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(\b(?:openai_api_key|api_key|password|secret|access_token)\s*[:=]\s*[\"']?)[^\s\"',}]+", r"\1[REDACTED]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED_KEY]", value)
    return value


class MemoryDB:
    def __init__(self, path: str | Path = "data/agent_memory.sqlite") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(fd)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
              agent_id TEXT NOT NULL, task_id TEXT NOT NULL,
              parent_task_id TEXT, goal TEXT NOT NULL,
              status TEXT NOT NULL, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL, answer TEXT, executed_json TEXT,
              PRIMARY KEY (agent_id, task_id)
            );
            CREATE TABLE IF NOT EXISTS task_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              agent_id TEXT NOT NULL, task_id TEXT NOT NULL,
              event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(agent_id,task_id,id);
            CREATE TABLE IF NOT EXISTS checkpoints (
              agent_id TEXT NOT NULL, task_id TEXT NOT NULL,
              messages_json TEXT NOT NULL, summary TEXT NOT NULL,
              executed_json TEXT NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY (agent_id,task_id)
            );
            CREATE TABLE IF NOT EXISTS tool_results (
              result_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
              task_id TEXT NOT NULL, tool_id TEXT NOT NULL,
              body TEXT NOT NULL, is_truncated INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS long_term_memories (
              memory_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
              kind TEXT NOT NULL, content TEXT NOT NULL,
              source_task_id TEXT NOT NULL, status TEXT NOT NULL,
              evidence_count INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memories_agent ON long_term_memories(agent_id,status,updated_at);
            """)
            # Phase 4: evolve memory schema (idempotent, adds 12 columns + 3 tables)
            from agent_ecosystem.memory.schema_v4 import migrate
            migrate(conn)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=10000")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def begin(self, agent_id: str, task_id: str, goal: str, parent_task_id: str | None = None) -> dict:
        with self.connection() as conn:
            conn.execute("""
               INSERT OR IGNORE INTO tasks
               (agent_id,task_id,parent_task_id,goal,status,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?)
            """, (agent_id, task_id, parent_task_id, redact(goal), "running", now(), now()))
            row = conn.execute(
                "SELECT * FROM tasks WHERE agent_id=? AND task_id=?", (agent_id,task_id)
            ).fetchone()
            if row["goal"] != redact(goal):
                raise ValueError("task_id already exists with a different task goal")
            return dict(row)

    def task(self, agent_id: str, task_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE agent_id=? AND task_id=?",(agent_id,task_id)).fetchone()
            return dict(row) if row else None

    def finish(self, agent_id: str, task_id: str, answer: str, executed: list[str], status: str = "completed") -> None:
        with self.connection() as conn:
            conn.execute("""UPDATE tasks SET status=?,answer=?,executed_json=?,updated_at=?
                       WHERE agent_id=? AND task_id=?""",
                         (status,redact(answer),dumps(executed),now(),agent_id,task_id))

    def event(self, agent_id: str, task_id: str, event_type: str, payload: dict) -> int:
        with self.connection() as conn:
            cursor = conn.execute("""INSERT INTO task_events
              (agent_id,task_id,event_type,payload_json,created_at) VALUES (?,?,?,?,?)""",
              (agent_id,task_id,event_type,redact(dumps(payload)),now()))
            return int(cursor.lastrowid)

    def events(self, agent_id: str, task_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute("""SELECT id,event_type,payload_json,created_at FROM task_events
               WHERE agent_id=? AND task_id=? ORDER BY id""", (agent_id,task_id)).fetchall()
            return [dict(id=r["id"], event_type=r["event_type"], payload=json.loads(r["payload_json"]), created_at=r["created_at"]) for r in rows]

    def checkpoint(self, agent_id: str, task_id: str, messages: list[dict], summary: str, executed: list[str]) -> None:
        with self.connection() as conn:
            conn.execute("""INSERT INTO checkpoints(agent_id,task_id,messages_json,summary,executed_json,updated_at)
               VALUES (?,?,?,?,?,?) ON CONFLICT(agent_id,task_id) DO UPDATE SET
                 messages_json=excluded.messages_json, summary=excluded.summary,
                 executed_json=excluded.executed_json,updated_at=excluded.updated_at""",
               (agent_id, task_id, redact(dumps(messages)), redact(summary), dumps(executed), now()))

    def load_checkpoint(self, agent_id: str, task_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("""SELECT * FROM checkpoints
                WHERE agent_id=? AND task_id=?""", (agent_id,task_id)).fetchone()
            if not row: return None
            return {"messages":json.loads(row["messages_json"]),
                    "summary":row["summary"], "executed":json.loads(row["executed_json"])}

    def save_result(self, agent_id: str, task_id: str, tool_id: str, body: str, max_bytes: int = 1_000_000) -> tuple[str, bool]:
        result_id = uuid4().hex
        raw = body.encode("utf-8")
        truncated = len(raw) > max_bytes
        if truncated:
            body = raw[:max_bytes].decode("utf-8", errors="ignore")
        with self.connection() as conn:
            conn.execute("""INSERT INTO tool_results
              (result_id,agent_id,task_id,tool_id,body,is_truncated,created_at) VALUES (?,?,?,?,?,?,?)""",
              (result_id,agent_id,task_id,tool_id,redact(body),int(truncated),now()))
        return result_id, truncated

    def read_result(self, agent_id: str, task_id: str, result_id: str,
                    start: int = 0, length: int = 2000) -> dict:
        if start < 0 or not 1 <= length <= 4000:
            raise ValueError("invalid result slice")
        with self.connection() as conn:
            row = conn.execute("""SELECT body,tool_id,is_truncated FROM tool_results
              WHERE agent_id=? AND task_id=? AND result_id=?""",(agent_id,task_id,result_id)).fetchone()
        if row is None:
            raise PermissionError("result not found or belongs to another task/agent")
        body = row["body"]
        return {"result_id":result_id, "tool_id":row["tool_id"],
                "start":start,"end":min(len(body),start+length),"total_chars":len(body),
                "truncated_at_storage":bool(row["is_truncated"]),
                "text":body[start:start+length]}

    def put_memory(self, agent_id: str, kind: str, content: str, source_task_id: str,
                   status: str = "pending") -> str:
        if status not in {"pending","verified","rejected"}:
            raise ValueError("invalid memory status")
        if kind not in {"episodic","semantic","procedural","working"}:
            raise ValueError("invalid memory kind")
        text = redact(content.strip())[:2000]
        if not text:
            raise ValueError("empty memory")
        mid = hashlib.sha256(f"{agent_id}\0{kind}\0{text}".encode()).hexdigest()[:24]
        with self.connection() as conn:
            conn.execute("""INSERT INTO long_term_memories
              (memory_id,agent_id,kind,content,source_task_id,status,evidence_count,created_at,updated_at)
              VALUES (?,?,?,?,?,?,1,?,?) ON CONFLICT(memory_id) DO UPDATE SET
              evidence_count=evidence_count+1,updated_at=excluded.updated_at""",
              (mid,agent_id,kind,text,source_task_id,status,now(),now()))
        return mid

    def set_memory_status(self, agent_id: str, memory_id: str, status: str) -> bool:
        if status not in {"verified","rejected","pending"}:
            raise ValueError("invalid status")
        with self.connection() as conn:
            result=conn.execute("""UPDATE long_term_memories SET status=?,updated_at=?
              WHERE agent_id=? AND memory_id=?""", (status,now(),agent_id,memory_id))
            return bool(result.rowcount)

    def memories(self, agent_id: str, *, verified_only: bool = True, limit: int = 500) -> list[dict]:
        with self.connection() as conn:
            sql="SELECT * FROM long_term_memories WHERE agent_id=?"
            params: list[Any]=[agent_id]
            if verified_only:
                sql+=" AND status='verified'"
            sql+=" ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            return [dict(x) for x in conn.execute(sql,params).fetchall()]

    @staticmethod
    def _features(text: str) -> set[str]:
        lower=text.lower()
        words=set(re.findall(r"[a-z][a-z0-9:_./-]{1,}",lower))
        cjk="".join(re.findall(r"[\u3400-\u9fff]",lower))
        return words | {cjk[i:i+2] for i in range(len(cjk)-1)}

    def search(self, agent_id: str, query: str, limit: int = 4) -> list[dict]:
        """Lightweight lexical retrieval for small stores; no embeddings required."""
        q=self._features(query)
        if not q:
            return []
        scored=[]
        for m in self.memories(agent_id,verified_only=True,limit=500):
            p=self._features(m["content"])
            overlap=len(q & p)
            if overlap:
                scored.append((overlap / (len(q)**0.5 * max(len(p),1)**0.25),m["updated_at"],m))
        scored.sort(key=lambda x: (x[0],x[1]),reverse=True)
        return [m for _,__,m in scored[:max(0,limit)]]

    def stats(self) -> dict[str,Any]:
        with self.connection() as conn:
            return {
                "tasks":conn.execute("SELECT count(*) FROM tasks").fetchone()[0],
                "events":conn.execute("SELECT count(*) FROM task_events").fetchone()[0],
                "results":conn.execute("SELECT count(*) FROM tool_results").fetchone()[0],
                "memories": [dict(x) for x in conn.execute("""
                    SELECT agent_id,status,count(*) count FROM long_term_memories
                    GROUP BY agent_id,status ORDER BY agent_id,status""").fetchall()],
            }
