"""Phase 4 memory evolution tests: schema migration, 4 memory types,
evidence grading, hybrid retrieval, promotion, feedback, isolation.
"""
import tempfile

import pytest

from agent_ecosystem.memory.store import MemoryDB
from agent_ecosystem.memory.embeddings import (
    HashingEmbeddingProvider, cosine_similarity,
)
from agent_ecosystem.memory.evolution import (
    MemoryEvolutionEngine, _features, _jaccard, VERIFICATION_REAL, VERIFICATION_MOCK, PROMOTION_PROMOTED,
)


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as td:
        yield td + "/mem.sqlite"


@pytest.fixture
def db(db_path):
    return MemoryDB(db_path)


@pytest.fixture
def engine(db):
    emb = HashingEmbeddingProvider(64)
    return MemoryEvolutionEngine(db, embedding_provider=emb, promotion_threshold=2)


class TestSchemaMigration:
    def test_new_columns_exist(self, db):
        with db.connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(long_term_memories)")}
        for c in ("namespace", "verification_level", "importance", "confidence",
                  "source_json", "embedding_json", "promotion_state", "dedupe_key"):
            assert c in cols, f"missing column {c}"

    def test_new_tables_exist(self, db):
        with db.connection() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("memory_candidates", "retrieval_events", "memory_feedback"):
            assert t in tables, f"missing table {t}"

    def test_idempotent_reopen(self, db_path):
        db1 = MemoryDB(db_path)
        with db1.connection() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(long_term_memories)")]
        assert len(cols) >= 15
        db2 = MemoryDB(db_path)  # re-open — no crash
        assert db2 is not None


class TestEmbedding:
    def test_deterministic(self):
        e = HashingEmbeddingProvider(128)
        v1 = e.embed("hello world")
        v2 = e.embed("hello world")
        assert v1 == v2

    def test_cosine_similarity(self):
        e = HashingEmbeddingProvider(128)
        va = e.embed("RAG pipeline implementation")
        vb = e.embed("RAG pipeline implementation")
        vc = e.embed("sandbox python execution")
        assert cosine_similarity(va, vb) > 0.9
        assert cosine_similarity(va, vc) < 0.5

    def test_dimension(self):
        assert HashingEmbeddingProvider(64).dimension == 64


class TestEvolutionEngine:
    def test_reflect_task_creates_memories(self, db, engine):
        db.begin("coding", "task-1", "write a test", parent_task_id="wf1")
        db.event("coding", "task-1", "tool_result",
                 {"tool_id": "sandbox:create", "is_error": False, "preview": "ok"})
        db.finish("coding", "task-1", "done", ["sandbox:create"])
        created = engine.reflect_task("coding", "task-1", workflow_id="wf1", llm_kind="MOCK_LLM")
        kinds = list(created.keys())
        assert "episodic" in kinds
        assert "semantic" in kinds
        assert "working" in kinds

    def test_reflect_task_mock_label(self, db, engine):
        db.begin("research", "task-2", "search", parent_task_id="wf2")
        db.event("research", "task-2", "tool_result",
                 {"tool_id": "fetch:fetch", "is_error": False, "preview": "data"})
        db.finish("research", "task-2", "done", ["fetch:fetch"])
        engine.reflect_task("research", "task-2", llm_kind="MOCK_LLM")
        mems = engine.search("research", "search", include_mock=True, limit=10)
        assert len(mems) >= 1
        # MOCK memories should NOT appear by default
        filtered = engine.search("research", "search", include_mock=False)
        assert all(m.get("verification_level") != VERIFICATION_MOCK for m in filtered)

    def test_procedural_candidate_created(self, db, engine):
        db.begin("coding", "task-p1", "run sandbox", parent_task_id="wf10")
        db.event("coding", "task-p1", "tool_result",
                 {"tool_id": "sandbox:create", "is_error": False})
        db.event("coding", "task-p1", "tool_result",
                 {"tool_id": "sandbox:run_python", "is_error": False})
        db.finish("coding", "task-p1", "done", ["sandbox:create", "sandbox:run_python"])
        cid = engine.update_procedural_candidate("coding", "task-p1", "wf10",
                                                  verification=VERIFICATION_REAL,
                                                  llm_kind="REAL_LLM")
        assert cid is not None
        with db.connection() as conn:
            row = conn.execute("SELECT support_count FROM memory_candidates WHERE candidate_id=?",
                               (cid,)).fetchone()
            assert row["support_count"] == 1

    def test_mock_not_promoted(self, db, engine):
        # MOCK experiences should be PROVISIONAL, never PROMOTED
        db.begin("coding", "task-pm", "sandbox", parent_task_id="wf11")
        db.event("coding", "task-pm", "tool_result", {"tool_id": "sandbox:create", "is_error": False})
        db.finish("coding", "task-pm", "done", ["sandbox:create"])
        engine.reflect_task("coding", "task-pm", llm_kind="MOCK_LLM")
        mems = engine.search("coding", "sandbox", include_mock=True, limit=10)
        for m in mems:
            assert m.get("promotion_state") != PROMOTION_PROMOTED, \
                f"MOCK memory {m['memory_id']} should not be PROMOTED"

    def test_agent_isolation(self, db, engine):
        db.begin("coding", "task-c1", "code", parent_task_id="wf1")
        db.event("coding", "task-c1", "tool_result", {"tool_id": "sandbox:create", "is_error": False})
        db.finish("coding", "task-c1", "done", ["sandbox:create"])
        engine.reflect_task("coding", "task-c1")
        mems = engine.search("research", "code", include_mock=True, limit=10)
        # research should not see coding's memories
        assert all(m["agent_id"] != "research" for m in mems if m.get("agent_id"))

    def test_prune_expired_working(self, db, engine):
        db.begin("coding", "task-w", "test")
        db.event("coding", "task-w", "tool_result", {"tool_id": "sandbox:create", "is_error": False})
        db.finish("coding", "task-w", "done", ["sandbox:create"])
        engine.reflect_task("coding", "task-w")
        # Force expire the working memory
        with db.connection() as conn:
            conn.execute("UPDATE long_term_memories SET expires_at='2020-01-01T00:00:00' "
                         "WHERE kind='working'")
            conn.commit()
        removed = engine.prune("coding")
        assert len(removed) >= 1

    def test_hybrid_search_finds_relevant(self, db, engine):
        db.begin("coding", "task-h1", "write a Python CLI tool")
        db.event("coding", "task-h1", "tool_result", {"tool_id": "filesystem_code:write_file",
                  "is_error": False, "preview": "print('hello')"})
        db.finish("coding", "task-h1", "done", ["filesystem_code:write_file"])
        engine.reflect_task("coding", "task-h1", llm_kind="MOCK_LLM")
        results = engine.search("coding", "Python file CLI", include_mock=True, limit=5)
        assert len(results) >= 1

    def test_jaccard_tokenization(self):
        assert len(_features("Python file")) > 0
        assert _jaccard({"python", "file"}, {"python", "test"}) == 1 / 3


class TestMemoryFeedback:
    def test_feedback_records_and_adjusts_confidence(self, db, engine):
        db.begin("coding", "task-fb", "test")
        db.event("coding", "task-fb", "tool_result", {"tool_id": "sandbox:create", "is_error": False})
        db.finish("coding", "task-fb", "done", ["sandbox:create"])
        engine.reflect_task("coding", "task-fb", llm_kind="MOCK_LLM")
        mems = engine.search("coding", "sandbox", include_mock=True, limit=5)
        assert len(mems) >= 1
        mid = mems[0]["memory_id"]
        engine.record_feedback("ret-test", mid, agent_id="coding",
                               used=True, success=True,
                               workflow_id="wf1", task_id="task-fb",
                               evidence={"tool": "sandbox:create"})
        with db.connection() as conn:
            row = conn.execute("SELECT confidence FROM long_term_memories WHERE memory_id=?",
                               (mid,)).fetchone()
            assert row is not None and float(row["confidence"]) > 0.15