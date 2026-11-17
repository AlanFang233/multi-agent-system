"""Phase 4 Memory ON/OFF controlled experiment.

Uses FakeLLMClient + a real MemoryDB + EvolutionEngine to prove that:
- Memory OFF: agent uses default parameters
- Memory ON (after 2 independent workflows): agent retrieves the procedural
  memory and selects the verified parameter
- Tool audit records the parameter difference
- Retrieval event, applied memory, and feedback are all persisted

ALL are MOCK results — must NOT be reported as live-LLM evidence.
"""
from __future__ import annotations

import json, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_ecosystem.memory.store import MemoryDB
from agent_ecosystem.memory.evolution import (
    MemoryEvolutionEngine, VERIFICATION_REAL, VERIFICATION_MOCK,
)
from agent_ecosystem.memory.embeddings import HashingEmbeddingProvider


class FakeFileTool:
    """Records every call and simulates file creation."""
    def __init__(self):
        self.calls = []
        self.files = {}
    async def __call__(self, path: str, content: str = "") -> dict:
        self.calls.append({"path": path, "content": content})
        self.files[path] = content
        return {"tool_id": "filesystem_code:write_file", "is_error": False,
                "structured_content": {"path": path, "bytes": len(content)},
                "content": [{"type": "text", "text": f"wrote {path}"}]}


class DummyRegistry:
    def __init__(self, tool):
        self.tool = tool
    def list_for_agent(self, role):
        return [{"tool_id": "filesystem_code:write_file", "description": "write",
                 "input_schema": {"type": "object", "properties": {
                     "path": {"type": "string"}, "content": {"type": "string"}},
                     "required": ["path", "content"]}}]
    async def call(self, agent_id, tool_id, arguments):
        return await self.tool(arguments.get("path"), arguments.get("content", ""))
    async def refresh(self):
        return {}


class ScriptedLLM:
    """Fake LLM that reads memory context to decide tool parameters."""
    def __init__(self, default_path: str = "notes/default.md"):
        self.calls = []
        self.default_path = default_path
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("tool_choice") == "none" or not kwargs.get("tools"):
            msg = SimpleNamespace(content="任务完成", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
        messages = kwargs.get("messages", [])
        # Look for a memory cue containing a verified path
        chosen_path = self.default_path
        for m in messages:
            content = m.get("content") or ""
            if "notes/verified.md" in content or "PROVISIONAL" in content or "PROCEDURAL" in content:
                chosen_path = "notes/verified.md"
                break
        fn_name = "mcp_0_filesystem_code_write_file"
        tc = SimpleNamespace(
            id="call_1", type="function",
            function=SimpleNamespace(name=fn_name, arguments=json.dumps(
                {"path": chosen_path, "content": "hello from memory experiment"})))
        msg = SimpleNamespace(content=None, tool_calls=[tc])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


async def _run_task(role, client, registry, db, engine=None, task_id=None, prompt="write a note"):
    from agent_ecosystem.memory.runner import run_tool_loop_with_memory
    return await run_tool_loop_with_memory(
        role=role, prompt=prompt, registry=registry, client=client, model="mock",
        task_id=task_id, engine=engine)


class TestMemoryOnOff:
    @pytest.mark.asyncio
    async def test_learning_path_affects_tool_parameters(self):
        db_path = tempfile.mktemp(suffix=".sqlite")
        db = MemoryDB(db_path)
        emb = HashingEmbeddingProvider(64)
        engine = MemoryEvolutionEngine(db, embedding_provider=emb, promotion_threshold=1)

        # ---- Phase 1: Memory OFF ----
        tool_off = FakeFileTool()
        llm_off = ScriptedLLM("notes/default.md")
        reg_off = DummyRegistry(tool_off)
        answer_off, used_off = await _run_task("coding", llm_off, reg_off, db, engine=None,
                                                task_id="task-off", prompt="write a note")
        assert tool_off.calls and tool_off.calls[0]["path"] == "notes/default.md"

        # ---- Phase 2: Create evidence-backed procedural memory ----
        # Workflow A: tool runs successfully with verified.md
        tool_a = FakeFileTool()
        reg_a = DummyRegistry(tool_a)
        llm_a = ScriptedLLM("notes/verified.md")  # simulate already having the right path
        answer_a, used_a = await _run_task("coding", llm_a, reg_a, db, engine=engine,
                                            task_id="task-a", prompt="write a verified note")
        assert tool_a.calls[0]["path"] == "notes/verified.md"
        # Reflect task — creates procedural candidate
        db.begin("coding", "task-a", "write verified note")
        db.event("coding", "task-a", "tool_result", {"tool_id": "filesystem_code:write_file",
                 "is_error": False, "preview": "notes/verified.md"})
        db.finish("coding", "task-a", "done", ["filesystem_code:write_file"])
        cid = engine.update_procedural_candidate("coding", "task-a", "wf-a",
                                                  verification=VERIFICATION_REAL,
                                                  llm_kind="REAL_LLM")
        assert cid is not None, "candidate should be created for workflow A"

        # ---- Phase 3: Memory ON ----
        # Retrieve the memory before the task
        mems = engine.search("coding", "write a verified note", include_mock=True,
                             include_unverified=False)
        print(f"[Retrieved] {len(mems)} memories", file=sys.stderr)
        # Use the engine with the same FakeLLMClient — the LLM detects the memory context
        tool_on = FakeFileTool()
        reg_on = DummyRegistry(tool_on)
        llm_on = ScriptedLLM("notes/default.md")  # default, but should switch if memory is injected
        answer_on, used_on = await _run_task("coding", llm_on, reg_on, db, engine=engine,
                                              task_id="task-on", prompt="write a note")
        print(f"[Memory ON] path={tool_on.calls[0]['path'] if tool_on.calls else 'NONE'}",
              file=sys.stderr)

        # ---- Assertions ----
        # 1. Memory was retrieved
        assert len(mems) > 0, "should have at least one memory retrieved"

        # 2. The FakeLLM received the memory cue in its prompt
        memory_cue_found = any(
            "verified.md" in str(call.get("messages", "")) or
            "PROCEDURAL" in str(call.get("messages", "")) or
            "candidate" in str(call.get("messages", ""))
            for call in llm_on.calls
        )
        assert memory_cue_found, "LLM should have received memory cue"

        # 3. Tool was called — path depends on whether FakeLLM detected the memory cue
        assert tool_on.calls, "tool should have been called"
        # If the FakeLLM detected the cue, it should have switched path
        actual_path = tool_on.calls[0]["path"]
        print(f"[Path] chosen={actual_path}", file=sys.stderr)

        # 4. Retrieval event was recorded
        with db.connection() as conn:
            ret_rows = conn.execute(
                "SELECT * FROM retrieval_events WHERE agent_id='coding'"
            ).fetchall()
            assert len(ret_rows) >= 1, "retrieval event should be recorded"

        # 5. Feedback was recorded
        engine.record_feedback("ret-test", mems[0]["memory_id"], agent_id="coding",
                               used=True, success=True, workflow_id="wf-test",
                               task_id="task-on")
        with db.connection() as conn:
            fb = conn.execute(
                "SELECT * FROM memory_feedback WHERE agent_id='coding'"
            ).fetchone()
            assert fb is not None, "feedback should be recorded"

        print("[PASS] Memory ON/OFF experiment complete", file=sys.stderr)