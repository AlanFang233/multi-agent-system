"""Phase 4 Memory Evolution Engine.

Evidence-first memory life cycle:
1. EPISODIC — per-task reflection (tool sequence, key results, evidence refs)
2. SEMANTIC  — single tool execution facts (stdout/content/path observations)
3. PROCEDURAL — verified tool call procedures (candidate→promoted via multi-workflow)
4. WORKING   — transient task/ workflow context (TTL, auto-clean)

Per Phase 1 design, two orthogonal axes distinguish evidence quality:
- LLM Decision Mode:  REAL_LLM | MOCK_LLM
- Tool Execution Mode: REAL_TOOL | MOCK_TOOL | NOT_EXECUTED

Current mock E2E = MOCK_LLM + REAL_TOOL — these experiences carry a
MOCK_SIMULATION label, content prefix and default retrieval exclude
until the user consciously opts in.

Retrieval: hybrid ranking (0.45·cosine + 0.25·Jaccard + 0.20·importance +
0.10·verification + type_boost), filtered by agent ownership, scope and
verification level. Retrieval events + feedback are persisted for auditing.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from .embeddings import HashingEmbeddingProvider, cosine_similarity, EmbeddingProvider
from .store import MemoryDB

# ── constants ───────────────────────────────────────────────────────────────

VERIFICATION_REAL = "REAL_EXECUTION"
VERIFICATION_MOCK = "MOCK_SIMULATION"
VERIFICATION_UNVERIFIED = "UNVERIFIED"

PROMOTION_NONE = "none"
PROMOTION_CANDIDATE = "candidate"
PROMOTION_PROVISIONAL = "provisional"
PROMOTION_PROMOTED = "promoted"

_VERIF_WEIGHT = {VERIFICATION_REAL: 1.0, VERIFICATION_MOCK: 0.45, VERIFICATION_UNVERIFIED: 0.15}
_PROMOTION_RANK = {PROMOTION_NONE: 0, PROMOTION_CANDIDATE: 1, PROMOTION_PROVISIONAL: 2, PROMOTION_PROMOTED: 3}

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")
_CHUNK_RE = re.compile(r"[\u4e00-\u9fff]")

# ── helpers ─────────────────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _features(text: str) -> set[str]:
    lower = text.lower()
    words = set(_TOKEN_RE.findall(lower))
    cjk = "".join(_CHUNK_RE.findall(lower))
    return words | {cjk[i:i + 2] for i in range(len(cjk) - 1)}


def _jaccard(q: set[str], m: set[str]) -> float:
    inter = q & m
    union = q | m
    return len(inter) / max(1, len(union))


def _procedure_signature(agent_id: str, tool_sequence: list[str], hints: list[str]) -> str:
    raw = f"{agent_id}|{','.join(tool_sequence)}|{','.join(hints)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _argument_hints(arguments: dict[str, Any]) -> str:
    keep = {"path", "filename", "url", "query", "remote", "branch"}
    return json.dumps({k: str(arguments[k])[:80] for k in keep if k in arguments},
                      ensure_ascii=False, default=str)


# ── Memory Evolution Engine ─────────────────────────────────────────────────

class MemoryEvolutionEngine:
    """Creates, retrieves, promotes and prunes agent memories.

    Decoupled from the LLM — all decisions are deterministic, based on
    tool audit rows stored in the shared memory SQLite database.
    """

    def __init__(
        self,
        db: MemoryDB,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        promotion_threshold: int = 2,
        promotion_confidence: float = 0.8,
        decay_half_life_days: float = 30.0,
        forget_threshold: float = 0.05,
        working_ttl_seconds: int = 3600,
        allow_mock_promotion: bool = False,
        allow_unverified_promotion: bool = False,
    ) -> None:
        self.db = db
        self.embeddings = embedding_provider or HashingEmbeddingProvider(256)
        self.promotion_threshold = promotion_threshold
        self.promotion_confidence = promotion_confidence
        self.decay_half_life_days = decay_half_life_days
        self.forget_threshold = forget_threshold
        self.working_ttl_seconds = working_ttl_seconds
        self.allow_mock_promotion = allow_mock_promotion
        self.allow_unverified_promotion = allow_unverified_promotion

    # -- episodic / semantic / working creation per task ---------------------

    def reflect_task(
        self,
        agent_id: str,
        task_id: str,
        workflow_id: str | None = None,
        *,
        llm_kind: str = "MOCK_LLM",
    ) -> dict[str, Any]:
        """Create EPISODIC + SEMANTIC + WORKING memories from one task's
        tool audit. Returns dict of created memory_ids."""
        created: dict[str, list[str]] = {}
        task = self.db.task(agent_id, task_id)
        if task is None:
            return created
        events = self.db.events(agent_id, task_id)
        tool_events = [e for e in events if e["event_type"] == "tool_result"]
        if not tool_events:
            return created

        tool_ids = []
        has_error = False
        tool_outputs = ""
        for e in tool_events:
            p = e["payload"]
            tool_ids.append(p.get("tool_id", "UNKNOWN"))
            if p.get("is_error"):
                has_error = True
            preview = str(p.get("preview", ""))[:500]
            tool_outputs += f"{p.get('tool_id', '?')}: {preview}\n"

        # Evidence level
        tool_exec_mode = "REAL_TOOL" if tool_events else "NOT_EXECUTED"
        if has_error:
            verification = VERIFICATION_UNVERIFIED
        elif tool_exec_mode == "NOT_EXECUTED":
            verification = VERIFICATION_UNVERIFIED
        else:
            verification = VERIFICATION_MOCK if llm_kind == "MOCK_LLM" else VERIFICATION_REAL

        importance = 0.55 if verification == VERIFICATION_REAL else (
            0.35 if verification == VERIFICATION_MOCK else 0.2)
        confidence = 0.75 if verification == VERIFICATION_REAL else (
            0.3 if verification == VERIFICATION_MOCK else 0.15)

        now_iso = _utc_now()
        source = json.dumps({
            "agent_id": agent_id, "task_id": task_id, "workflow_id": workflow_id,
            "llm_kind": llm_kind, "tool_execution": tool_exec_mode,
            "tool_call_ids": tool_ids, "has_error": has_error,
        }, ensure_ascii=False)

        # EPISODIC memory
        episodic_id = self.db.put_memory(
            agent_id, "episodic",
            f"Task {task_id[:12]}: {'SUCCESS' if not has_error else 'ERROR'}\n{tool_outputs[:1500]}",
            task_id, status="pending",
        )
        self._set_evolved_fields(agent_id, episodic_id, verification=verification,
                                 importance=importance, confidence=confidence,
                                 source_json=source)
        created.setdefault("episodic", []).append(episodic_id)

        # SEMANTIC memory — first tool fact
        if tool_events:
            first = tool_events[0]
            sem_content = f"Tool {first['payload'].get('tool_id','?')}: {str(first['payload'].get('preview',''))[:1200]}"
            sem_id = self.db.put_memory(agent_id, "semantic", sem_content, task_id, status="pending")
            self._set_evolved_fields(agent_id, sem_id, verification=verification,
                                     importance=max(0.05, importance - 0.05),
                                     confidence=confidence, source_json=source)
            created.setdefault("semantic", []).append(sem_id)

        # WORKING memory (TTL)
        working_id = self.db.put_memory(
            agent_id, "working",
            f"Active task {task_id[:12]}: {task.get('goal', '')[:200]}",
            task_id, status="pending",
        )
        self._set_evolved_fields(agent_id, working_id, verification=VERIFICATION_UNVERIFIED,
                                 importance=0.2, confidence=0.15,
                                 expires_at=(datetime.now(timezone.utc) +
                                             __import__('datetime').timedelta(
                                                 seconds=self.working_ttl_seconds)).isoformat())
        created.setdefault("working", []).append(working_id)

        return created

    # -- procedural candidate / promotion ------------------------------------

    def update_procedural_candidate(
        self,
        agent_id: str,
        task_id: str,
        workflow_id: str,
        *,
        verification: str,
        llm_kind: str,
    ) -> str | None:
        """Create or update a procedural candidate from one task's tool sequence.
        Returns candidate_id if created/updated."""
        events = self.db.events(agent_id, task_id)
        tool_events = [e for e in events if e["event_type"] == "tool_result"]
        if not tool_events:
            return None
        tool_seq = [e["payload"].get("tool_id", "UNKNOWN") for e in tool_events]
        if any(e["payload"].get("is_error") for e in tool_events):
            return None  # only all-success sequences contribute

        hints = _argument_hints(tool_events[0].get("payload", {}).get("arguments_key", ""))
        signature = _procedure_signature(agent_id, tool_seq, [hints][:1])
        procedure = json.dumps({
            "steps": [{"tool_id": t, "hint": hints[:200]} for t in tool_seq],
        }, ensure_ascii=False)

        now_iso = _utc_now()
        # Upsert candidate
        with self.db.connection() as conn:
            existing = conn.execute(
                "SELECT * FROM memory_candidates WHERE agent_id=? AND signature=?",
                (agent_id, signature),
            ).fetchone()
            if existing:
                wid_list = json.loads(existing["evidence_workflow_ids_json"]) if existing["evidence_workflow_ids_json"] else []
                if workflow_id not in wid_list:
                    wid_list.append(workflow_id)
                new_count = len(wid_list)
                conn.execute(
                    "UPDATE memory_candidates SET support_count=?, evidence_workflow_ids_json=?,"
                    "evidence_tool_call_ids_json=evidence_tool_call_ids_json || ?,"
                    "last_seen=?, verification_level=? WHERE candidate_id=?",
                    (new_count, json.dumps(wid_list),
                     json.dumps(tool_seq), now_iso, verification,
                     existing["candidate_id"]),
                )
                conn.commit()
                cid = existing["candidate_id"]
                support = new_count
            else:
                import uuid as _uuid
                cid = f"cand_{_uuid.uuid4().hex}"
                conn.execute(
                    "INSERT INTO memory_candidates(candidate_id,agent_id,namespace,signature,"
                    "verification_level,content,procedure_json,support_count,"
                    "evidence_workflow_ids_json,evidence_tool_call_ids_json,"
                    "evidence_task_ids_json,first_seen,last_seen,status) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, agent_id, "experience", signature, verification,
                     f"Procedure {signature[:12]}", procedure, 1,
                     json.dumps([workflow_id]), json.dumps(tool_seq),
                     json.dumps([task_id]), now_iso, now_iso, "candidate"),
                )
                conn.commit()
                support = 1

        # Check promotion threshold
        if verification == VERIFICATION_REAL or (
            verification == VERIFICATION_MOCK and self.allow_mock_promotion
        ):
            conf = min(0.95, 0.75 + 0.05 * support)
            if support >= self.promotion_threshold and conf >= self.promotion_confidence:
                self._promote_candidate(cid, agent_id, conf, verification, llm_kind)
        return cid

    def _promote_candidate(self, candidate_id: str, agent_id: str,
                           confidence: float, verification: str, llm_kind: str) -> str | None:
        with self.db.connection() as conn:
            cand = conn.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if cand is None:
                return None
            content = cand["content"]
            if verification == VERIFICATION_MOCK:
                promotion = PROMOTION_PROVISIONAL
                importance = 0.35
                prefix = "[MOCK SIMULATION — NOT REAL-WORLD VERIFIED] "
                if not content.startswith(prefix):
                    content = prefix + content
            else:
                promotion = PROMOTION_PROMOTED
                importance = 0.9
            dedupe = f"procedural:{cand['signature']}"
            source = json.dumps({
                "origin": "promotion",
                "candidate_id": candidate_id,
                "signature": cand["signature"],
                "support_count": cand["support_count"],
                "llm_kind": llm_kind,
                "verification": verification,
            }, ensure_ascii=False)

            now_iso = _utc_now()
            try:
                memory_id = self.db.put_memory(agent_id, "procedural", content,
                                               cand.get("evidence_task_ids_json", ""),
                                               status="verified")
            except Exception:
                import uuid as _uuid
                memory_id = self.db.put_memory(agent_id, "procedural", content,
                                               f"promoted_{_uuid.uuid4().hex[:12]}",
                                               status="verified")
            self._set_evolved_fields(
                agent_id, memory_id,
                verification=verification, importance=importance,
                confidence=confidence, promotion_state=promotion,
                dedupe_key=dedupe, source_json=source,
            )
            conn.execute(
                "UPDATE memory_candidates SET promoted_memory_id=?, status='promoted' "
                "WHERE candidate_id=?", (memory_id, candidate_id),
            )
            conn.commit()
            return memory_id

    # -- hybrid retrieval ----------------------------------------------------

    def search(
        self,
        agent_id: str,
        query: str,
        *,
        namespace: str | None = None,
        limit: int = 6,
        include_mock: bool = False,
        include_unverified: bool = False,
        workflow_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid retrieval: cosine + Jaccard + importance + verification + type."""
        query_embed = self.embeddings.embed(query) if self.embeddings else []
        query_feat = _features(query)

        rows = self.db.memories(agent_id, verified_only=False, limit=500)
        scored: list[tuple[float, str, dict]] = []
        for m in rows:
            verif = m.get("verification_level", VERIFICATION_UNVERIFIED)
            if not include_mock and verif == VERIFICATION_MOCK:
                continue
            if not include_unverified and verif == VERIFICATION_UNVERIFIED:
                continue
            # Working memory expiry
            if m.get("kind") == "working":
                exp = m.get("expires_at")
                if exp and exp < _utc_now():
                    continue
            if namespace is not None and m.get("namespace", "") != namespace:
                continue

            emb_json = m.get("embedding_json")
            mem_embed = json.loads(emb_json) if emb_json else []
            cos = cosine_similarity(query_embed, mem_embed) if query_embed and mem_embed else 0.0
            jac = _jaccard(query_feat, _features(m.get("content", "")))
            imp = float(m.get("importance", 0.5))
            vw = _VERIF_WEIGHT.get(verif, 0.15)
            tb = {"procedural": 0.1, "semantic": 0.04, "episodic": 0.0, "working": -0.05}.get(
                m.get("kind"), 0.0)
            score = 0.45 * max(0.0, cos) + 0.25 * jac + 0.20 * imp + 0.10 * vw + tb
            scored.append((score, m.get("updated_at", ""), m))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        results = [m for _, _, m in scored[:max(0, limit)]]

        # Record retrieval event
        if results and workflow_id:
            self._record_retrieval(agent_id, query, [r["memory_id"] for r in results],
                                   namespace=namespace or "experience",
                                   workflow_id=workflow_id, task_id=task_id,
                                   execution_id=execution_id)
            self._bump_access(agent_id, [r["memory_id"] for r in results])

        return results

    # -- decay / prune ------------------------------------------------------

    def effective_importance(self, memory: dict) -> float:
        """Decay-adjusted importance."""
        imp = float(memory.get("importance", 0.5))
        verif = memory.get("verification_level", VERIFICATION_UNVERIFIED)
        weight = _VERIF_WEIGHT.get(verif, 0.15)
        created = memory.get("created_at", "")
        try:
            age_days = (datetime.now(timezone.utc) -
                        datetime.fromisoformat(created.replace("Z", "+00:00"))).total_seconds() / 86400
        except Exception:
            age_days = 0
        decay = math.pow(0.5, age_days / max(1, self.decay_half_life_days))
        access = min(0.25, 0.02 * int(memory.get("access_count", 0)))
        return max(0.0, min(1.0, imp * decay * weight * (1.0 + access)))

    def prune(self, agent_id: str) -> list[str]:
        """Delete expired working memories and forget decayed ones."""
        now_iso = _utc_now()
        removed: list[str] = []
        with self.db.connection() as conn:
            # Expired working memories
            expired = conn.execute(
                "SELECT memory_id FROM long_term_memories WHERE agent_id=? AND kind='working' "
                "AND expires_at IS NOT NULL AND expires_at<?",
                (agent_id, now_iso),
            ).fetchall()
            for r in expired:
                conn.execute("DELETE FROM long_term_memories WHERE memory_id=?", (r["memory_id"],))
                removed.append(r["memory_id"])

            # Decayed oblivion
            for m in self.db.memories(agent_id, verified_only=False, limit=1000):
                mid = m["memory_id"]
                prom = m.get("promotion_state", "none")
                verif = m.get("verification_level", "")
                if prom == PROMOTION_PROMOTED or verif == VERIFICATION_REAL:
                    continue
                eff = self.effective_importance(m)
                if eff < self.forget_threshold:
                    conn.execute("DELETE FROM long_term_memories WHERE memory_id=?", (mid,))
                    removed.append(mid)
            conn.commit()
        return removed

    # -- feedback ------------------------------------------------------------

    def record_feedback(
        self,
        retrieval_id: str,
        memory_id: str,
        *,
        agent_id: str,
        used: bool,
        success: bool,
        workflow_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
        evidence: dict | None = None,
    ) -> None:
        """Adjust confidence based on whether the retrieved memory was used successfully."""
        delta = 0.03 if (used and success) else (-0.10 if used else 0.0)
        now_iso = _utc_now()
        import uuid as _uuid
        fid = f"fb_{_uuid.uuid4().hex}"
        with self.db.connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO memory_feedback(feedback_id,retrieval_id,memory_id,"
                    "workflow_id,task_id,execution_id,agent_id,used,success,confidence_delta,"
                    "evidence_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (fid, retrieval_id, memory_id, workflow_id, task_id,
                     execution_id, agent_id, int(used), int(success), delta,
                     json.dumps(evidence or {}, ensure_ascii=False, default=str),
                     now_iso),
                )
                conn.commit()
            except Exception:
                pass  # duplicate (retrieval_id,memory_id) → idempotent
        if delta != 0.0:
            self._adjust_confidence(memory_id, delta)

    # -- internal helpers ----------------------------------------------------

    def _set_evolved_fields(self, agent_id: str, memory_id: str, **kw) -> None:
        sets = []
        vals = []
        for k, v in kw.items():
            col = {"verification": "verification_level", "source_json": "source_json",
                   "expires_at": "expires_at", "promotion_state": "promotion_state",
                   "dedupe_key": "dedupe_key",
                   "importance": "importance", "confidence": "confidence",
                   }.get(k, k)
            sets.append(f"{col}=?")
            vals.append(v if isinstance(v, (int, float)) else str(v))
        if not sets:
            return
        vals.extend([agent_id, memory_id])
        with self.db.connection() as conn:
            conn.execute(
                f"UPDATE long_term_memories SET {', '.join(sets)} WHERE agent_id=? AND memory_id=?",
                tuple(vals),
            )
            conn.commit()

    def _adjust_confidence(self, memory_id: str, delta: float) -> None:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT confidence FROM long_term_memories WHERE memory_id=?", (memory_id,)
            ).fetchone()
            if row is None:
                return
            cur = float(row["confidence"] or 0.15)
            new = max(0.05, min(0.99, cur + delta))
            conn.execute(
                "UPDATE long_term_memories SET confidence=? WHERE memory_id=?", (new, memory_id),
            )
            conn.commit()

    def _record_retrieval(self, agent_id: str, query: str, memory_ids: list[str],
                          *, namespace: str, workflow_id: str | None = None,
                          task_id: str | None = None, execution_id: str | None = None) -> None:
        import uuid as _uuid
        rid = f"ret_{_uuid.uuid4().hex}"
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO retrieval_events(retrieval_id,agent_id,namespace,query,"
                "memory_ids_json,workflow_id,task_id,execution_id,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (rid, agent_id, namespace, query, json.dumps(memory_ids),
                 workflow_id, task_id, execution_id, _utc_now()),
            )
            conn.commit()

    def _bump_access(self, agent_id: str, memory_ids: list[str]) -> None:
        now_iso = _utc_now()
        with self.db.connection() as conn:
            for mid in memory_ids:
                conn.execute(
                    "UPDATE long_term_memories SET access_count=access_count+1, "
                    "last_accessed_at=? WHERE agent_id=? AND memory_id=?",
                    (now_iso, agent_id, mid),
                )
            conn.commit()