# Current Runtime Capability Report

**Date**: 2026-11-17 | **Branch**: feature/agentforge-capabilities | **Commit**: `19f386d`

---

## VERIFIED (real runtime evidence)

| # | Capability | Evidence |
|---|---|---|
| 1 | Original A2A delegation (3 agents) | Phase 0 E2E PASS (TASK_STATE_COMPLETED, 8 tools, Docker output, 3 verified memories) |
| 2 | MCP Tool Registry (56 tools / 7 servers) | Phase 0 E2E uses sandbox/ filesystem/ skills/ fetch tools |
| 3 | Agent Skills (load + execute) | loss-analysis loaded + loss_metrics executed in E2E |
| 4 | Docker Sandbox execution | Real Docker output: uid=65534, NETWORK_BLOCKED, SANDBOX_EXECUTION_OK |
| 5 | Coordinator DAG planning | `wf_ffb82...` → 4 nodes planned, dispatched, completed (37s) |
| 6 | Coordinator Workflow COMPLETED | Real workflow with real A2A dispatch, synthesis node, final report generated |
| 7 | Dashboard accessible | HTTP 200 at http://127.0.0.1:8000/dashboard (0.0.0.0 bind) |
| 8 | SQLite orchestration persistence | Workflow persisted, restart recovery (same-host WAL) |
| 9 | Phase 0/2/3 E2E scenario scripts | All 3 scenarios executable |
| 10 | CLI submission + follow | `python -m agent_ecosystem.cli run "task" --mock` works |

---

## TESTED (automated test passes, real runtime NOT verified)

| # | Capability | Tests |
|---|---|---|
| 1 | DAG topology validation (Kahn) | test_plan_validation.py (12 tests) |
| 2 | Capability assignment | test_capability_assignment.py (8 tests) |
| 3 | Parallel scheduler (barrier proven) | test_dag_scheduler.py (14 tests) |
| 4 | Approval create/approve/reject/consume | test_phase3_features.py (10 approval tests) |
| 5 | Clarification create/answer | test_phase3_features.py (4 tests) |
| 6 | A2A x-workflow metadata parsing | test_a2a_workflow_metadata.py (5 tests) |
| 7 | Memory schema migration | test_phase4_memory.py (migration tests) |
| 8 | Memory evolution engine | test_phase4_memory.py (15 tests) |
| 9 | Hybrid retrieval | test_phase4_memory.py (hybrid search test) |
| 10 | Feedback confidence adjustment | test_phase4_memory.py (feedback test) |

---

## PARTIAL (code exists, not fully wired or tested)

| # | Capability | Gap |
|---|---|---|
| 1 | HITL approve→resume→execute tool | Token propagation from Coordinator to Agent runner incomplete; agent-side approval check exists but resume path NOT verified |
| 2 | Clarification answer→inject→resume | Same token gap; WAITING_INPUT state machine correct |
| 3 | Pause/Resume/Cancel scheduler impact | Flags persist; scheduler checks pause/cancel; resume path verified for basic case |
| 4 | A2A TaskStore persistence | Reverted to InMemoryTaskStore after a2a-sdk 1.3.0 incompatibility (`to_json`/`async` issues) |
| 5 | Crash recovery (Coordinator kill/restart) | Lease expiry code exists; real kill/restart NOT verified |
| 6 | Memory ON/OFF behavior change | Engine works; runner integration is opt-in (`engine=None` default); behavior change NOT end-to-end proven |
| 7 | Mock LLM experience labeling | Correctly tagged as MOCK_SIMULATION + PROVISIONAL; real promotion path NOT exercised |

---

## NOT IMPLEMENTED

| # | Capability |
|---|---|
| 1 | Dashboard Memory panel (API + HTML) |
| 2 | Embedding version management (rebuild, metadata) |
| 3 | Cross-agent shared namespace |
| 4 | Real LLM (HKGAI) live acceptance |
| 5 | Benchmark / ablation / fault injection framework |
| 6 | Provider resilience layer (retry, classification, masking) |
| 7 | CI / deployment docs |

---

## Current Blocker: Approval Resumption

Workflow `wf_81658941c10443209d6bb9f58d248928` demonstrated that:
- `filesystem_code:write_file` was correctly BLOCKED by approval gate
- `sandbox:run_python` was correctly BLOCKED
- But the workflow still reached COMPLETED status (the synthesis node produced a report despite blocked tools)
- The approval gate works at the **block** level, but the **resume-after-approval** chain is incomplete

**Impact**: High-risk tools are safe (blocked), but the workflow reports a misleading COMPLETED when tools were actually blocked. Fix: synthesis node should detect blocked/ failed dependencies and report PARTIAL, not successful completion.

---

## HKGAI Live Mode

- `.env` configured with HKGAI endpoint
- Single-round API call OK (from memory)
- **Not tested** in current DAG workflow (all runs use MOCK)
- Live mode gating (MAS_LIVE_API_ALLOWED) NOT implemented
- **Recommendation**: Do NOT use HKGAI live until approval resumption and stability are verified