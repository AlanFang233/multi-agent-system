# Future Development Roadmap

**Date**: 2026-11-17 | **Branch**: feature/agentforge-capabilities

---

## P0 — Must Fix (Runtime Stability & UX)

| # | Issue | Priority | Difficulty | Why |
|---|---|---|---|---|
| P0-1 | **CLI real-time event streaming** — current CLI polls REST; needs SSE or event-driven push for sub-second latency | 🔴 CRITICAL | Medium | User experience; course demo requires live feedback |
| P0-2 | **Approval resume chain** — approve→re-dispatch node with token→agent consumes→tool executes | 🔴 CRITICAL | Medium | Core HITL flow broken; tools blocked but workflow incorrectly marked COMPLETED |
| P0-3 | **Synthesis detect blocked nodes** — synthesis should report PARTIAL when dependencies were blocked, not fabricate success | 🔴 CRITICAL | Low | Misleading workflow status |
| P0-4 | **A2A TaskStore compatibility** — fix SQLiteTaskStore to work with a2a-sdk 1.3.0 call conventions | 🔴 CRITICAL | Medium | Currently reverted to InMemory; necessary for restart recovery |
| P0-5 | **Coordinator < 127.0.0.1 0.0.0.0 > bind** — verify Dashboard actual access from Windows browser with real user | 🟡 HIGH | Low | Already fixed (0.0.0.0 bind) but user has not confirmed in-session |

---

## P1 — Course Demo & Scoring (Direct Value)

| # | Feature | Priority | Difficulty | Why |
|---|---|---|---|---|
| P1-1 | **Real HKGAI live integration** — staged acceptance (single call → plan → execute → full DAG) with MAS_LIVE_API_ALLOWED gate | 🔴 CRITICAL | Medium | Course requirement; all current evidence is MOCK |
| P1-2 | **Dashboard Memory panel** — show 4 memory types, candidates, promotions, retrieval events, feedback | 🟡 HIGH | Medium | User requested; complements the full narrative arc |
| P1-3 | **Memory ON/OFF evidence** — prove procedural memory changes tool behavior in controlled E2E | 🟡 HIGH | High | Key selling point for the project; currently only engine tests |
| P1-4 | **Benchmark framework** — port AgentForge BenchmarkRunner/Verifier, adapt for 3-agent topology, create 20+ tasks | 🟡 HIGH | High | Objective scoring; distinguishes from qualitative demos |
| P1-5 | **Crash recovery proof** — real kill/restart + verify no duplicate side effects + reconciliation | 🟡 HIGH | Medium | Reliability claim requires evidence |
| P1-6 | **CLI polish** — color output, agent-specific prefix, approval prompt in-terminal, tool evidence summary | 🟡 HIGH | Low | Course presentation |

---

## P2 — Enhancement (Differentiators)

| # | Feature | Priority | Difficulty | Why |
|---|---|---|---|---|
| P2-1 | **Embedding version management** — provider ID, version, dimension metadata; index rebuild from source text | 🟢 MEDIUM | Medium | Production-readiness for memory system |
| P2-2 | **Cross-agent shared namespace** — publish/revoke/audit for procedural memory | 🟢 MEDIUM | Medium | Collaborative learning across agents |
| P2-3 | **Provider resilience layer** — retry/classification/masking/budget (port from AgentForge openai_compatible.py) | 🟢 MEDIUM | Medium | Required for reliable live HKGAI runs |
| P2-4 | **Multi-agent visualization** — richer DAG rendering in Dashboard (force-graph or expandable tree) | 🟢 LOW | High | Eye candy for demo |
| P2-5 | **Deployment docs** — WSL2/Windows step-by-step, .env template, compose extension | 🟢 LOW | Low | Reproducibility |
| P2-6 | **Ruff/CI** — GitHub Actions (pytest + ruff matrix) | 🟢 LOW | Low | Professional polish |

---

## Acceptance Criteria Per Item

| Item | Acceptance |
|---|---|
| P0-1 CLI streaming | `python -m agent_ecosystem.cli run "..." --mock` displays per-node progress, tool calls, final result in real time |
| P0-2 Approval resume | Approve in Dashboard → node resumes → tool executes → evidence collected → node COMPLETED |
| P0-3 Synthesis detects blocked | Blocked tools → synthesis reports "PARTIAL: X nodes blocked by approval" instead of fabricated success |
| P1-1 HKGAI live | Single goal → plan valid → DAG dispatched → completed (with token budget + authorization gate) |
| P1-3 Memory ON/OFF | Two distinct workflows create procedural memory → third workflow retrieves and uses it → parameter change proven via tool audit |
| P1-4 Benchmark | 20+ tasks, CSV/JSON/MD report, A/B/C ablation, MOCK/LIVE banner enforced |