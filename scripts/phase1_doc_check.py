"""Phase 1 doc-consistency check.

Verifies factual claims in docs/PHASE1_*.md against the two real trees:
- AgentForge audit clone (read-only):  D:/AgentForge-audit
- Main project (this repo, WSL copy accessed via UNC or local checkout)

Run:  python scripts/phase1_doc_check.py [--agentforge D:/AgentForge-audit] [--main <repo root>]
Pure stdlib; read-only. Prints PASS/FAIL per claim and a final summary.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    RESULTS.append((ok, label))
    print(("PASS " if ok else "FAIL ") + label)


def count_py_lines(root: Path) -> tuple[int, int]:
    files = [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]
    lines = sum(len(p.read_text(encoding="utf-8", errors="replace").splitlines()) for p in files)
    return len(files), lines


def wc(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agentforge", default="D:/AgentForge-audit")
    ap.add_argument("--main", default=str(Path(__file__).resolve().parents[1]))
    args = ap.parse_args()
    af = Path(args.agentforge)
    mp = Path(args.main)

    # ---- 1. corpus sizes claimed in AUDIT doc ----
    n, lines = count_py_lines(af / "src")
    check((n, lines) == (98, 25620), f"AgentForge src = 98 files / 25620 lines (actual {n}/{lines})")
    tn, tlines = count_py_lines(af / "tests")
    check((tn, tlines) == (42, 9602), f"AgentForge tests = 42 files / 9602 lines (actual {tn}/{tlines})")

    # ---- 2. per-file line counts cited in docs ----
    cited_af = {
        "src/agentforge/orchestration/planner.py": 65,
        "src/agentforge/orchestration/orchestrator.py": 1750,
        "src/agentforge/orchestration/state_machine.py": 82,
        "src/agentforge/orchestration/assignment.py": 82,
        "src/agentforge/orchestration/errors.py": 54,
        "src/agentforge/orchestration/control.py": 120,
        "src/agentforge/agents/coordinator.py": 221,
        "src/agentforge/agents/base.py": 982,
        "src/agentforge/agents/factory.py": 126,
        "src/agentforge/agents/registry.py": 55,
        "src/agentforge/models/task.py": 156,
        "src/agentforge/models/workflow.py": 82,
        "src/agentforge/models/execution.py": 41,
        "src/agentforge/models/common.py": 27,
        "src/agentforge/persistence/database.py": 984,
        "src/agentforge/persistence/events.py": 39,
        "src/agentforge/interaction/approval_service.py": 362,
        "src/agentforge/interaction/clarification_service.py": 216,
        "src/agentforge/interaction/repository.py": 327,
        "src/agentforge/tools/executor.py": 693,
        "src/agentforge/tools/approval.py": 104,
        "src/agentforge/tools/base.py": 241,
        "src/agentforge/tools/policy.py": 129,
        "src/agentforge/sandbox/docker.py": 478,
        "src/agentforge/sandbox/base.py": 120,
        "src/agentforge/sandbox/manager.py": 63,
        "src/agentforge/llm/base.py": 143,
        "src/agentforge/llm/openai_compatible.py": 529,
        "src/agentforge/llm/mock.py": 870,
        "src/agentforge/memory/engine.py": 654,
        "src/agentforge/memory/evolution_store.py": 1202,
        "src/agentforge/memory/embeddings.py": 71,
        "src/agentforge/memory/models.py": 151,
        "src/agentforge/memory/experiment.py": 396,
        "src/agentforge/evaluation/metrics.py": 154,
        "src/agentforge/config/settings.py": 181,
        "src/agentforge/runtime.py": 586,
        "src/agentforge/messaging/bus.py": 193,
        "src/agentforge/messaging/conversation.py": 74,
        "src/agentforge/benchmark/runner.py": 603,
        "src/agentforge/benchmark/verification.py": 495,
        "src/agentforge/benchmark/models.py": 480,
        "src/agentforge/benchmark/metrics.py": 301,
        "src/agentforge/benchmark/report.py": 355,
        "src/agentforge/benchmark/ablation.py": 160,
        "src/agentforge/benchmark/failures.py": 151,
        "src/agentforge/benchmark/execution.py": 218,
        "src/agentforge/benchmark/provider.py": 359,
        "src/agentforge/benchmark/live.py": 312,
        "src/agentforge/benchmark/live_memory.py": 214,
        "src/agentforge/benchmark/live_stages.py": 605,
        "benchmarks/dev/build_tasks.py": 2310,
    }
    for rel, want in cited_af.items():
        p = af / rel
        ok = p.exists() and wc(p) == want
        actual = wc(p) if p.exists() else "MISSING"
        check(ok, f"AF {rel} = {want} lines (actual {actual})")

    # aggregate claims
    bm = [p for p in (af / "src/agentforge/benchmark").glob("*.py")]
    bm_lines = sum(wc(p) for p in bm)
    check(len(bm) == 19, f"benchmark/ = 19 py files (actual {len(bm)})")
    check(6300 <= bm_lines <= 6600, f"benchmark/ = 6479 lines (actual {bm_lines})")
    llm = [p for p in (af / "src/agentforge/llm").glob("*.py") if p.name != "__init__.py"]
    llm_lines = sum(wc(p) for p in llm)
    check(len(llm) == 6 and 1600 <= llm_lines <= 1800, f"llm/ = 6 files ~1700 lines (actual {len(llm)}/{llm_lines})")
    sb_lines = sum(wc(af / f"src/agentforge/sandbox/{n}") for n in ("base.py", "docker.py", "manager.py"))
    check(sb_lines == 661, f"sandbox/ = 661 lines (actual {sb_lines})")
    live_lines = sum(wc(af / f"src/agentforge/benchmark/{n}") for n in ("live.py", "live_memory.py", "live_stages.py"))
    check(live_lines == 1131, f"live*.py = 1131 lines (actual {live_lines})")

    # ---- 3. key symbols / formulas cited in docs ----
    def grep(rel: str, pattern: str, label: str) -> None:
        text = (af / rel).read_text(encoding="utf-8", errors="replace")
        check(re.search(pattern, text) is not None, label)

    grep("src/agentforge/orchestration/planner.py", r"def validate_plan\(\s*plan: ExecutionPlan, \*, max_subtasks: int", "planner.validate_plan signature")
    grep("src/agentforge/orchestration/state_machine.py", r"ALLOWED_TRANSITIONS", "state_machine.ALLOWED_TRANSITIONS exists")
    grep("src/agentforge/orchestration/assignment.py", r"class CapabilityAssigner", "assignment.CapabilityAssigner exists")
    grep("src/agentforge/memory/engine.py", r"0\.75 \+ 0\.05 \* merged\.support_count", "promotion formula 0.75+0.05n (engine)")
    grep("src/agentforge/memory/engine.py", r"memory_promotion_threshold", "promotion threshold setting used")
    grep("src/agentforge/memory/evolution_store.py", r"0\.45 \* max\(0\.0, semantic\)", "hybrid rank weight 0.45 semantic")
    grep("src/agentforge/memory/evolution_store.py", r"decay_half_life_days", "decay half-life parameter")
    grep("src/agentforge/memory/embeddings.py", r"class HashingEmbeddingProvider", "HashingEmbeddingProvider exists")
    grep("src/agentforge/tools/approval.py", r"def arguments_digest", "arguments_digest exists")
    grep("src/agentforge/tools/approval.py", r"sort_keys=True", "digest canonical sort_keys")
    grep("src/agentforge/interaction/repository.py", r"consumed_at IS NULL AND status", "approval CAS single-consume SQL")
    grep("src/agentforge/tools/executor.py", r"def _find_replay", "executor replay cache exists")
    grep("src/agentforge/tools/executor.py", r"replay_mode", "replay gated by replay_mode")
    grep("src/agentforge/llm/openai_compatible.py", r"RETRYABLE_STATUS_CODES = frozenset\(\{408, 409, 429, 500, 502, 503, 504\}\)", "provider retryable status set")
    grep("src/agentforge/llm/base.py", r"no_response_disconnect", "error class taxonomy")
    grep("src/agentforge/benchmark/ablation.py", r"A_single_agent", "ablation config A")
    grep("src/agentforge/benchmark/execution.py", r'SOLO_AGENT_ID = "solo"', "solo agent baseline id")
    grep("src/agentforge/config/settings.py", r"max_plan_subtasks: int = Field\(default=8", "budget default max_plan_subtasks=8")
    grep("src/agentforge/persistence/database.py", r"UNIQUE\(workflow_id, task_id, tool_name, argument_digest\)", "reconciliation UNIQUE constraint")

    db_text = (af / "src/agentforge/persistence/database.py").read_text(encoding="utf-8")
    check(db_text.count("CREATE TABLE IF NOT EXISTS") == 13, f"database.py has 13 tables (actual {db_text.count('CREATE TABLE IF NOT EXISTS')})")
    es_text = (af / "src/agentforge/memory/evolution_store.py").read_text(encoding="utf-8")
    check(es_text.count("CREATE TABLE IF NOT EXISTS") == 5, f"evolution_store.py has 5 tables (actual {es_text.count('CREATE TABLE IF NOT EXISTS')})")

    # approvals 21 columns / clarifications 14 columns
    m = re.search(r"CREATE TABLE IF NOT EXISTS approvals \((.*?)\);", db_text, re.S)
    cols = [c for c in re.findall(r"^\s*(\w+) TEXT|^\s*(\w+) INTEGER|^\s*(\w+) REAL", m.group(1), re.M)] if m else []
    appr_cols = len([ln for ln in m.group(1).splitlines() if ln.strip() and not ln.strip().startswith(("PRIMARY", ")"))]) if m else 0
    check(appr_cols == 21, f"approvals table = 21 columns (actual {appr_cols})")
    m2 = re.search(r"CREATE TABLE IF NOT EXISTS clarifications \((.*?)\);", db_text, re.S)
    cl_cols = len([ln for ln in m2.group(1).splitlines() if ln.strip() and not ln.strip().startswith(("PRIMARY", ")"))]) if m2 else 0
    check(cl_cols == 14, f"clarifications table = 14 columns (actual {cl_cols})")

    # ---- 4. main-project current facts ----
    cited_mp = {
        "src/agent_ecosystem/tool_agent.py": 393,
        "src/agent_ecosystem/dynamic_delegation.py": 180,
        "src/agent_ecosystem/agent_delegation.py": 163,
        "src/agent_ecosystem/agent_skill_registry.py": 193,
        "src/agent_ecosystem/memory/store.py": 247,
        "src/agent_ecosystem/memory/runner.py": 261,
        "src/agent_ecosystem/memory/context.py": 119,
        "src/agent_ecosystem/memory/reflection.py": 59,
        "src/agent_ecosystem/sandbox_mcp_server.py": 210,
        "src/agent_ecosystem/coordinator.py": 43,
        "src/agent_ecosystem/a2a_delegate.py": 90,
    }
    for rel, want in cited_mp.items():
        p = mp / rel
        ok = p.exists() and wc(p) == want
        actual = wc(p) if p.exists() else "MISSING"
        check(ok, f"MP {rel} = {want} lines (actual {actual})")
    # a2a_server/tool_registry grew in Phase 0; assert current post-fix sizes
    for rel, want in {"src/agent_ecosystem/a2a_server.py": 220, "src/agent_ecosystem/tool_registry.py": 122}.items():
        actual = wc(mp / rel)
        check(abs(actual - want) <= 2, f"MP {rel} ≈ {want} lines (actual {actual})")

    src_files = [p for p in (mp / "src").rglob("*.py") if "__pycache__" not in p.parts]
    src_lines = sum(wc(p) for p in src_files)
    check(len(src_files) == 22 and 2400 <= src_lines <= 2650,
          f"MP src = 22 py files, ~2.5k lines (actual {len(src_files)}/{src_lines})")

    failed = [label for ok, label in RESULTS if not ok]
    print(f"\n==== {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed ====")
    if failed:
        print("FAILED CLAIMS:")
        for f_ in failed:
            print(" -", f_)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
