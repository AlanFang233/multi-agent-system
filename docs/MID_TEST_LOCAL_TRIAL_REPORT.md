# Mid_Test Local Trial Report — Scenario 1 (Software Engineering Team)

**Date**: 2026-11-20 | **Git baseline**: `4a166f0` (branch `feature/agentforge-capabilities`)
plus this round's uncommitted mid-test fixes (approval-replay, decide-vs-park race,
planner capability-gap repair, filesystem relative-path fix, sandbox timeout clamp,
config tuning) — committed locally at the end of this round, **not pushed**.
**Model**: real HKGAI gateway `t2_hkgai-v3_fp8_1m_e10` (from `.env.hkgai.bak`, loaded as env
overrides; on-disk `.env` remains Mock and untouched). Mock LLM server **not running** during
all real-model phases (contamination impossible). Teacher source files: `Mid_Test.zip`
(extracted copy archived at `artifacts/mid_test_local/teacher_source/`).

> Marker legend: **PASS / PARTIAL / FAIL / BLOCKED / NOT TESTED**. Every claim below is backed
> by machine-readable evidence under `artifacts/mid_test_local/` (event streams, approval
> logs, tool audits, verification outputs, screenshots).

---

## 1. Test date, Git commit, actual model

- Date: 2026-11-20, 17:38–21:10 (local, UTC+8).
- Baseline commit `4a166f0`; working tree additionally carries this round's fixes (see §14).
- Model: HKGAI `t2_hkgai-v3_fp8_1m_e10` (reasoning model; single-request connectivity probe:
  0.6 s, usage fields present). All four group agents + Coordinator used the same backend
  (verified via role probe and per-node latency 12–200 s, natural-language outputs, no
  `[Mock-` templates anywhere).

## 2. Group creation evidence

Group `midtest-software` created via `POST /groups` with the **teacher's verbatim role prompts**
(`artifacts/mid_test_local/group_setup.json`):

- create: HTTP 200 `{"group_id":"midtest-software","status":"created","agents":4}`
- activate: HTTP 200 in 3.7 s; per-group registries generated; agents spawned on ports 8100-8103.
- **PASS** — real group row + real processes (not DB-only records).

## 3. The four dynamic agents: identity, role, A2A address

| agent_id | Role (teacher verbatim) | A2A address | health | tool profile |
|---|---|---|---|---|
| `midtest-software:lead` | Technical Lead | http://127.0.0.1:8100 | ok | research |
| `midtest-software:dev` | Software Developer | http://127.0.0.1:8101 | ok | coding |
| `midtest-software:qa` | Test Engineer | http://127.0.0.1:8102 | ok | coding |
| `midtest-software:release` | Release Manager | http://127.0.0.1:8103 | ok | analysis |

Role probe (one real A2A call to lead, 14.3 s): *"I'm the technical lead for this software
development team, responsible for understanding your goals, defining the overall technical
approach, coordinating delivery across developers and testers, and surfacing key questions and
trade-offs for your input…"* — reflects the teacher prompt, not a template. **PASS.**

## 4. Task 1 original input & workflow id

Teacher text submitted verbatim (no pre-seeded code/DAG/answers):
*"Our team currently manages meeting rooms using a shared spreadsheet… build a usable meeting
room booking system… you can ask me in the group."*
→ workflow **`wf_f11bf2d264754737867c9e7d325c0cef`** (goal text archived:
`artifacts/mid_test_local/task1_goal.txt`; event stream `task1_events.jsonl`).

## 5. Task 1 real DAG & assignments (planner-generated at runtime)

`clarify-requirements`(lead) → `implement-system`(dev) → `verify-system`(qa) →
`fix-defects`(dev) → `usage-guide`(release) → `__synthesis__`(lead). 6/6 nodes COMPLETED;
19 approvals; 71 LLM-call events; 70 tool-result audit rows; a2a_calls=11; 0 retries.
The DAG was produced by the real planner from the goal alone (plan_validated event,
attempts recorded). **PASS.**

## 6. Inter-agent communication, tool calls, approvals

- Communication: Coordinator→agent A2A dispatches with `x-workflow` metadata; dependency results
  injected as low-trust `[DEPENDENCY RESULTS]`; lead synthesis re-read artifacts independently.
  (Workflow mode does not use autonomous `a2a:delegate_task`; the original delegation path was
  separately regression-passed at baseline, §12.)
- Tool calls: 70 audited rows (`task1_audit.json`): `filesystem_code:write_file/edit_file`,
  `sandbox:create/run_python/run_pytest`, `filesystem:*` reads, `skills:load`. Every high-risk
  call passed the digest-bound HITL gate: **19 approvals, all consumed exactly once**
  (`task1_approvals.json`).
- Clarifications: 0 raised (real LLM proceeded with stated assumptions; see §16 gap d).
- Live terminal evidence: `logs/midtest_task1.log` (node transitions, every approval with tool +
  argument preview, replay events).

## 7. Software actually produced & file locations

Workspace `mcp_workspace/sandboxes/258c8916685642f3a261281a8ffde149/`:
`main.py` (18.6 KB CLI app, SQLite store), `README.md`, `HOW-TO-USE.md` (recovered, see §14 bug 9),
`demo.py`, `checks.py`, `boundary_test.py`, `FIX_REPORT.md`, plus Task 2 additions
`analytics_api.py`, `dashboard/`, `validate_metrics.py`, `final_evidence.py`, `bookings.db`.
Operator copies: `artifacts/mid_test_local/task1_software/`, `task2_software/`.

## 8. Run commands, test outputs, quality issues

Run: `cd <workspace> && python3 main.py` (menu) or one-shot CLI
(`show/book/cancel/list`, `ROOMBOOK_DB` override). Operator-independent verification
(`scripts/midtest_verify_task1.py`, isolated copy + own DB): **11/11 PASS** —
A availability (18/18 slots), B booking created, C overlap rejected with clear error (rc=1),
C2 touching interval allowed, D cancel, E slot freed & re-bookable, F SQLite persistence across
processes, G README usage, T1-T3 team's own demo/checks/boundary suites re-run green
(`BOUNDARY SUITE: ALL PASSED`). Evidence: `task1_verification/verification_output.txt`.
Quality issues found (honest): (i) `HOW-TO-USE.md` initially mislocated by bug 9 (recovered);
(ii) dashboard live-mode field drift (§11); (iii) `DATA_CONTRACT.md` never landed on disk
(parent-dir write failure during the cancelled run) — lead disclosed it in its synthesis.

## 9. Task 2 workflow id

Teacher text verbatim → final workflow **`wf_ccd5d8cc768d4d3091ccdfd0e4ad67b4`**
(5/5 nodes COMPLETED, 9 approvals, 60 LLM-call events, 8 min). Three earlier attempts are part
of the evidence trail: `wf_803df371` (planning rejected: capability gap — fixed, §14 bug 8),
`wf_7e66e703` (depth 7 > cap — config tuned), `wf_c24ca6cf` (cancelled by operator after 94
approvals burned on bug 10's opaque sandbox error — fixed, §14 bug 10).

## 10. How Task 2 inherited Task 1 context, files and data

- Same group `midtest-software` (no new group; `GET /groups` shows one entry, workflow_ids grew).
- Plan's first node `audit-and-metrics-spec`/`booking_data_audit` **read the Task 1 workspace**
  (dev listed and read `main.py`, `bookings.db` schema; group memory retrieval carried paths).
- `analytics_api.py` imports `main.py` for `DB_PATH`/working-hour constants and opens the
  **existing** `bookings.db` read-only — no invented data source.
- dev wrote new files **into the Task 1 workspace** (`analytics_api.py`, `dashboard/`), and QA ran
  `independent_verify*.py` inside sandbox `258c8916…` against the real DB.
- No unrelated system was regenerated: the integration node extended, not replaced. **PASS.**

## 11. Dashboard artifacts & statistics verification

Artifacts: `dashboard/index.html`, `dashboard.js` (SVG chart renderers, mock|live modes),
`test_contract.py`, `README.md`; backend `analytics_api.py` (`GET /api/dashboard?from&to&room_id`,
WSGI). Operator live server on **http://localhost:8200** (harness
`scripts/midtest_dashboard_server.py`, injected copy under `artifacts/.../dashboard_live/`,
team files untouched).

Statistics verification (real records — operator-seeded via the delivered CLI, 17 bookings +
3 cancellations, transcript `task1_seed_log.txt`):
- Team self-check on real DB: totals 14 confirmed / 3 cancelled / 930 min / 15.5 h / rate 0.1765;
  per-room Boardroom 6/8.5 h, MRA 4/4.5 h, HRB 4/2.5 h; busiest day 2026-11-23; busiest slot
  09:30 (`task2_analytics_selfcheck.txt`).
- Team contract test vs its own SQL oracle: 13/13 PASS (`task2_contract_test.txt`).
- **Operator independent SQL cross-check: 9/9 PASS** (`scripts/midtest_verify_task2.py`,
  `task2_verification.json`) — every metric equals my own raw-SQL ground truth; date-range
  filter verified (single day 11-25 → 2).
- UI: renders filters/KPI cards/heatmap/SVG charts; **live mode has a field-mapping bug**
  (`dashboard.js` aggregate() expects raw `{rooms:[{id,name}],bookings:[...]}` while the API
  returns the pre-aggregated payload with `room_name`) → KPIs show 0 and room labels
  "undefined" in live mode; mock mode fully functional. Screenshot:
  `artifacts/mid_test_local/dashboard_live_screenshot.png`. Lead's synthesis disclosed this
  gap itself before I found it.
- Verdict: backend+metrics **PASS**; interactive UI on real data **PARTIAL**.

## 12. Original A2A unaffected

Base agents (research/coding/analysis on 8001-8003), original registry and the legacy
`a2a:delegate_task` path untouched by this round; Phase 0 E2E (research→coding→analysis→delegate
chain, real Docker) passed at baseline `4a166f0` under Mock earlier today, and the 176/181-test
suite (which covers the original transport) is green after every fix. Group agents are additive
processes on separate ports with namespaced ids. **PASS.**

## 13. Real-model call counts & token usage

LLM-call events (memory DB): Task 1 = 71; Task 2 attempts = 2+2 (planner-only failures) + 145
(cancelled attempt) + 60 (final) ≈ **280 calls** incl. probes (connectivity 1, role probe 1).
**Token totals: NOT AVAILABLE** — the runner does not persist provider `usage` fields (the
connectivity probe showed per-call usage is retrievable, e.g. 90 prompt tokens for a trivial
call; aggregating would require a code change out of this round's scope). No infinite retries:
all loops bounded (driver runaway guard, planner repair budget, scheduler budgets).

## 14. Bugs actually encountered this round (all fixed + re-run)

| # | Symptom | Root cause | Fix | Re-verified |
|---|---|---|---|---|
| 7 | Approval decided while node still RUNNING → workflow stranded forever (decide endpoint only transitions WAITING_INPUT) | decide-vs-park race | Scheduler WAITING_INPUT poll now delivers stored decisions (`node_decision_delivered` events); 3 new unit tests | live recovery of stranded wf + 181 passed |
| 8 | Real-LLM plan rejected outright when one subtask merges capabilities no single agent has (`['statistics','coding']`) | capability gap treated as non-repairable | Bounded repair with explicit split feedback when every capability exists in catalog; 2 new unit tests | Task 2 attempt-4 planned successfully |
| 9 | Agent writes with relative `sandboxes/…` silently landed in `src/sandboxes/` (incl. Task 1's HOW-TO-USE.md); sandbox then "lost" the files | filesystem MCP `--confine-roots` resolves relatives against first root (src) | start script pins CWD to repo root + mcp_workspace first root; deterministic MCP probe PASS; misplaced deliverables recovered to intended workspaces (evidence kept in `stray_evidence/`) | probe + Task 2 retry used relative paths correctly |
| 10 | `run_pytest/run_python` with `timeout_seconds=120` died as opaque `"Error executing tool …"`; dev burned ~35 approvals misdiagnosing "pytest crashes in sandbox" (its 24 tests actually all pass) | `MAX_SECONDS=90` hard ValueError swallowed by MCP wrapper | clamp to cap + explicit `timeout_note` in result | probe: run_pytest returns `24 passed in 0.15s` |
| cfg | Real-LLM segments exceed 300 s node cap; HITL-heavy DAGs exceed dispatch budget; repaired plans deeper than 6 | config sized for mock | `node_timeout 900`, `deadline 5400`, `max_a2a_calls 120`, `max_depth 8`, `repairs 2` (documented in yaml comments) | Task 1 & Task 2 completed within budgets |

## 15. Teacher scenario statuses

| Scenario | Status | Note |
|---|---|---|
| 1 Software Engineering — Task 1 | **PASS** | full autonomous build + real execution + independent 11/11 verification |
| 1 Software Engineering — Task 2 | **PASS (backend/data) / PARTIAL (UI live wiring)** | metrics verified 9/9 vs independent SQL; UI live-mode field drift disclosed & unfixed by design (evaluation integrity) |
| 2 Newsroom | **NOT TESTED** | time/quota stewardship after 4 Task-2 attempts and 4 mid-test bug fixes; teacher files archived and ready |
| 3 Securities Fund | **NOT TESTED** | same; simulated-investment-only constraint acknowledged for any future run |

## 16. Capability gaps vs teacher requirements (honest)

a) **UI↔API contract drift**: missing `DATA_CONTRACT.md` let `dashboard.js` and `analytics_api.py`
   diverge; live mode needs one wiring iteration (mock mode unaffected).
b) **Release role has no write permission** (analysis profile) → its deliverables (usage guide)
   arrive as node text, not files; teacher's "deliver runnable software + notes" is met via
   dev-written files + inline docs, but a release-owned file artifact needs a permission change.
c) **Real-LLM planner needs bounded-repair tuning** (depth/capability) — configured now, but the
   defaults were mock-era.
d) **Clarification convention not taught to the real model**: `[[CLARIFY:]]` marker is in the
   runner but absent from node prompts, so the team states assumptions instead of asking
   (teacher allows asking; mechanism exists, prompt wiring doesn't).
e) **Sandbox `/work` is read-only**: agents must stage outputs via MCP writes or /tmp; workable
   but cost several exploratory cycles (evidence in cancelled attempt).
f) **No token accounting** in the runner (see §13).

## 17. Ready for the formal Mid-Test?

**Conditionally YES.** Demonstrated end-to-end on the real model: group initialization with
teacher prompts, autonomous DAG planning, real A2A dispatch, real Docker/MCP/Skills execution
behind digest-bound HITL approvals, crash/race-tolerant resumption, cross-task context
inheritance, and independently verified deliverables. Before the formal run I recommend one
team feedback iteration to (i) wire dashboard live mode to the API payload (gap a) and
(ii) teach the clarification convention in node prompts (gap d); both are small, bounded changes
and the harness (`scripts/midtest_task_driver.py`) can re-run any scenario unattended.

---

## How to open everything (for the human reviewer)

- **Meeting-room system (terminal)**:
  `cd ~/projects/multi-agent-system/mcp_workspace/sandboxes/258c8916685642f3a261281a8ffde149 && python3 main.py`
- **Dashboard (browser, live real data)**: server already running → http://localhost:8200
  (restart with `nohup ./.venv/bin/python -u scripts/midtest_dashboard_server.py &`; note the
  disclosed live-mode mapping gap — backend JSON at `/api/dashboard?from=2026-11-23&to=2026-11-27`
  is the authoritative view).
- **Coordinator dashboard (workflows/approvals)**: http://localhost:8000/dashboard
- **Evidence**: `artifacts/mid_test_local/` (events, approvals, audits, verifications,
  screenshots, teacher source); driver logs `logs/midtest_task*.log`.
