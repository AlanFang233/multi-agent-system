# Project Version Comparison — Original vs Enhanced

**Original (main)**: `a95995d` (Phase 0 baseline)
**Enhanced (feature/agentforge-capabilities)**: `19f386d` (current HEAD)
**Reference**: AgentForge@7cee900 (read-only, D:\AgentForge-audit)
**Date**: 2026-11-17

---

## 1. Original A2A Architecture & Autonomous Delegation

| | Original (main) | Enhanced (current) |
|---|---|---|
| Exists? | ✅ Research/Coding/Analysis 3 A2A JSON-RPC servers (a2a-sdk 1.3.0) | ✅ Preserved unchanged |
| Code | `src/agent_ecosystem/a2a_server.py` (200→263 lines) | + `_normalize_metadata` (protobuf Struct fix) + `_workflow_context` (x-workflow extract) |
| Runtime | Phase 0 E2E passed | Phase 0 E2E STILL passed (backward compatible) |
| Dynamic delegation | route/hop/call budget enforcement | Preserved; agents inside DAG nodes keep full autonomy |
| Tests | test_agent_delegation, test_dynamic_delegation | All original tests preserved |

**Verdict**: Original A2A is intact and forward-compatible.

---

## 2. Original MCP Tool Registry

| | Original | Enhanced |
|---|---|---|
| Exists? | ✅ 7 servers, 56 tools, role-based allowlist | + `_expand` ($HOME/~ portability) |
| Code | `src/agent_ecosystem/tool_registry.py` (114→121 lines) | Minimal changes |
| Runtime | Phase 0 E2E tools executed | Preserved |

---

## 3. Original Agent Skills

| | Original | Enhanced |
|---|---|---|
| Exists? | ✅ SKILL.md frontmatter, skills:load + loss_metrics | Preserved |
| Code | `src/agent_ecosystem/agent_skill_registry.py` | Unchanged |

---

## 4. Original Docker Sandbox

| | Original | Enhanced |
|---|---|---|
| Exists? | ✅ --network none, --read-only, --cap-drop ALL, uid 65534 | Preserved; P3 added `tool_policy.py` approval gate for high-risk sandbox ops |
| Code | `src/agent_ecosystem/sandbox_mcp_server.py` | Unchanged |

---

## 5. Original SQLite Memory

| | Original | Enhanced |
|---|---|---|
| Exists? | ✅ 5 tables, checkpoint, lexical search, reflection | **+12 columns ALTER, +3 new tables** (memory_candidates, retrieval_events, memory_feedback) |
| Code | `src/agent_ecosystem/memory/store.py` (247→250 lines) | + `memory/evolution.py` (350 lines), `memory/embeddings.py` (80), `memory/schema_v4.py` (70) |
| Runtime | original reflection rules still fire | Evolution engine runs AFTER task completion (optional, off by default) |
| Tests | test_agent_memory.py | + test_phase4_memory.py (15 tests) |

---

## 6. Original Context Compaction

| | Original | Enhanced |
|---|---|---|
| Exists? | ✅ `context.py` group-safe compression, char budget | Preserved unchanged |
| Code | `src/agent_ecosystem/memory/context.py` | Unchanged |

---

## 7. NEW: Coordinator Dynamic Task Planning

| | Enhanced |
|---|---|
| Code | `src/agent_ecosystem/orchestration/planner.py` (~300 lines) |
| Runtime | ✅ Wired via Coordinator POST /workflows |
| Tests | test_planner_llm.py (12 tests), test_plan_validation.py (12 tests) |
| Evidence | **COMPLETED workflow** `wf_ffb82...` — 4 nodes, real DAG, real A2A dispatch, real synthesis |
| Limits | Mock LLM uses heuristic planner; REAL_LLM path NOT VERIFIED |

---

## 8. NEW: DAG Topology Validation + Parallel Scheduler

| | Enhanced |
|---|---|
| Code | `orchestration/scheduler.py` (~280 lines), `orchestration/state_machine.py` (80) |
| Runtime | ✅ True asyncio parallelism (barrier-proven), single-writer safety |
| Tests | test_dag_scheduler.py (14 tests including concurrent barrier proof) |
| Evidence | Cross-process probe: OVERLAP 12.5s, synthesis wait, all nodes completed |

---

## 9. NEW: Capability Assignment

| | Enhanced |
|---|---|
| Code | `orchestration/assignment.py` (80 lines) — ported from AgentForge |
| Runtime | ✅ Uses existing `config/agent_registry.yaml` capabilities |
| Tests | test_capability_assignment.py (8 tests) |

---

## 10. NEW: Workflow REST API

| | Enhanced |
|---|---|
| Code | `src/agent_ecosystem/coordinator.py` (43→270 lines) |
| Endpoints | POST /workflows, GET /workflows/{id}, GET /workflows/{id}/result, POST /workflows/{id}/cancel, GET /approvals, POST /approvals/{id}/decide, GET /clarifications, POST /clarifications/{id}/answer, POST /workflows/{id}/pause, POST /workflows/{id}/resume, GET /dashboard |
| Tests | test_coordinator_api.py (6 tests) |

---

## 11. NEW: Workflow SQLite Persistence

| | Enhanced |
|---|---|
| Code | `orchestration/sqlite_store.py` (330 lines) |
| Runtime | ✅ Production path uses SQLiteWorkflowStore (WAL, CAS, lease recovery) |
| Tests | test_phase3_features.py (10 SQLite tests) |

---

## 12. NEW: HITL Approval / Clarification / Pause / Resume / Cancel

| | Enhanced |
|---|---|
| Code | `orchestration/hitl.py` (400 lines), `orchestration/suspension.py`, `orchestration/tool_policy.py` |
| Runtime | ⚠️ Approval service wired at Coordinator; agent-side check in runner.py; A2A INPUT_REQUIRED signal chain complete |
| Evidence | Pending approval visibility in Dashboard + CLI; approve/reject works at SQL level |
| Limits | **Approve→resume→execute tool** E2E NOT YET VERIFIED (approval token propagation gap) |

---

## 13. NEW: Dashboard

| | Enhanced |
|---|---|
| Code | HTML embedded in `coordinator.py` |
| Runtime | ✅ Accessible at http://127.0.0.1:8000/dashboard (0.0.0.0 bind) |
| Panels | Workflow submit, DAG tree view, approval/clarification, pause/resume/cancel |
| Missing | Memory panel (P4), embedding panel (P4) |

---

## 14. NEW: Memory Evolution

| | Enhanced |
|---|---|
| Code | `memory/evolution.py` (350 lines) |
| Runtime | ⚠️ Engine wired but OFF by default in production (runner uses `engine=None`); tests exercise full pipeline |
| Tests | test_phase4_memory.py (15 tests) |
| Evidence | Full pipeline proven in tests: reflection→candidate→promotion→retrieval→feedback |

---

## 15. NEW: Embedding + Hybrid Retrieval

| | Enhanced |
|---|---|
| Code | `memory/embeddings.py` (80 lines) |
| Runtime | ⚠️ HashingEmbeddingProvider available; hybrid search functional in engine |
| Limits | NOT semantic AI model; deterministic feature hash only; index versioning NOT built |

---

## 16. NEW: Evidence Provenance & Feedback

| | Enhanced |
|---|---|
| Code | In `memory/evolution.py` (retrieval_events + memory_feedback tables) |
| Runtime | ✅ Proven via 15 tests |
| Limits | Applied vs Retrieved distinction tracked but LLM behavior change not end-to-end proven |

---

## 17. NEW: CLI

| | Enhanced |
|---|---|
| Code | `src/agent_ecosystem/cli.py` (260 lines) |
| Commands | `run`, `status`, `approvals`, `approve`, `reject`, `clarify`, `dashboard` |

---

## 18. NEW: Tests & E2E Scripts

| Phase | Tests | Added |
|---|---|---|
| Phase 0 | 46 | Baseline (original) |
| Phase 2 | +78 | DAG planner, scheduler, service, coordinator API, x-workflow metadata |
| Phase 3 | +31 | SQLite persistence, approval lifecycle, HITL gate, task store, CAS |
| Phase 4 | +15 | Memory evolution, hybrid retrieval, embedding, feedback |
| Total | **169** (1 known skip from A2A TaskStore reversion) | |