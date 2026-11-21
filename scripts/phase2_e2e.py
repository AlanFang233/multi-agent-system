"""Phase 2 end-to-end demo: Scenarios A/B/C via Coordinator REST + real A2A.

All LLM decisions are MOCK (mock LLM server's planner heuristics + agent scripts).
Tool execution, A2A dispatch, Docker sandbox and evidence collection are all
REAL. Results must NOT be reported as live-LLM evidence.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx

SCENARIOS = {
    "A_simple": (
        "A: 简单链式任务",
        "让 Research Agent 查询 RAG 技术的相关资料，然后让 Analysis Agent 对查到的资料进行总结分析。",
        {"min_nodes": 2, "expect_synthesis": True, "expect_agents": {"research", "analysis"}},
    ),
    "B_complex": (
        "B: 复杂 ML 项目",
        (
            "研究一个机器学习项目：研究 Random Forest 算法的理论基础和最新进展，"
            "分析该算法在不同数据集上的性能特征，编写 Python 实验代码，"
            "在 Docker 沙箱中执行实验验证，最后形成完整的分析报告。"
        ),
        {"min_nodes": 4, "expect_synthesis": True, "expect_agents": {"research", "coding", "analysis"}},
    ),
    "C_parallel": (
        "C: 三主题并行",
        "分别研究三个独立主题：Transformer 注意力机制、图神经网络 GNN、联邦学习 Federated Learning，最后综合成一份报告。",
        {"min_nodes": 3, "expect_synthesis": True, "expect_parallel": True, "expect_agents": {"research"}},
    ),
}

COORDINATOR_URL = "http://127.0.0.1:8000"


async def poll(workflow_id: str, timeout: float = 900.0) -> dict:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=30) as client:
        while time.monotonic() < deadline:
            r = await client.get(f"{COORDINATOR_URL}/workflows/{workflow_id}")
            snap = r.json()
            status = snap["workflow"]["status"]
            if status in ("completed", "failed", "cancelled"):
                return snap
            # Phase 3+: coding nodes hit the real HITL approval gate; approve
            # through the REST flow so the tools actually execute.
            apr = await client.get(f"{COORDINATOR_URL}/approvals",
                                   params={"workflow_id": workflow_id, "status": "pending"})
            for a in apr.json():
                await client.post(
                    f"{COORDINATOR_URL}/approvals/{a['approval_id']}/decide",
                    json={"decision": "approve", "decided_by": "phase2_e2e"})
                print(f"  auto-approved {a['approval_id']} ({a['tool_id']})", flush=True)
            await asyncio.sleep(1.5)
    raise TimeoutError(f"workflow {workflow_id} did not finish in {timeout}s")


async def run_scenario(name: str, title: str, goal: str, checks: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n{'='*60}\n{title}\ngoal: {goal[:100]}...\n{'='*60}")
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{COORDINATOR_URL}/workflows", json={"goal": goal})
        if r.status_code != 202:
            raise RuntimeError(f"submit failed: {r.status_code} {r.text}")
        wf_id = r.json()["workflow_id"]
        print(f"workflow_id={wf_id}")
    snap = await poll(wf_id)
    elapsed = time.monotonic() - t0
    status = snap["workflow"]["status"]
    nodes = snap["nodes"]
    final = snap["workflow"].get("final_result") or ""
    error = snap["workflow"].get("error") or ""
    print(f"status={status}  nodes={len(nodes)}  elapsed={elapsed:.1f}s")
    pass_checks = True
    plan_nodes = [n for n in nodes if n.get("kind") == "generic"]
    synthesis = [n for n in nodes if n.get("kind") == "synthesis"]
    # structural checks
    if len(plan_nodes) < checks["min_nodes"]:
        print(f"FAIL: expected >= {checks['min_nodes']} plan nodes, got {len(plan_nodes)}")
        pass_checks = False
    else:
        print(f"PASS: plan_nodes={len(plan_nodes)} >= {checks['min_nodes']}")
    if checks.get("expect_synthesis"):
        if not synthesis:
            print("FAIL: expected synthesis node, got 0")
            pass_checks = False
        else:
            print("PASS: synthesis node present")
    agents = {n.get("assigned_agent") for n in plan_nodes if n.get("assigned_agent")}
    missing = checks.get("expect_agents", set()) - agents
    if missing:
        print(f"FAIL: missing agent roles {missing} (got {agents})")
        pass_checks = False
    else:
        print(f"PASS: agents={agents} covers expected roles")
    # parallel overlap check for Scenario C
    if checks.get("expect_parallel"):
        plan_times = [
            (n.get("plan_key"), n.get("started_at"), n.get("finished_at"))
            for n in plan_nodes
        ]
        enters = [t[1] for t in plan_times if t[1]]
        exits = [t[2] for t in plan_times if t[2]]
        if enters and exits and max(enters) < min(exits):
            print("PASS: parallel overlap detected (max_enter < min_exit)")
        else:
            print(f"FAIL: no parallel overlap (enters={enters}, exits={exits})")
            pass_checks = False
    # evidence count
    for n in nodes:
        ev = n.get("metadata", {}).get("evidence") or {}
        tools = ev.get("executed_tools") or []
        if n.get("kind") != "synthesis" and not tools:
            print(f"WARN: {n['plan_key']} has no tool evidence")
    report = {
        "scenario": name, "title": title, "goal": goal,
        "workflow_id": wf_id, "status": status, "elapsed_s": round(elapsed, 1),
        "final_result": final[:2000], "error": error,
        "nodes": nodes, "verdict": "PASS" if (status == "completed" and pass_checks) else "FAIL",
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    # Markdown summary
    md = f"# {title}\n\n"
    md += f"**Verdict**: {'✅ PASS' if report['verdict'] == 'PASS' else '❌ FAIL'}\n\n"
    md += f"**Goal**: {goal}\n\n**Status**: {status} | **Elapsed**: {elapsed:.1f}s\n\n"
    md += "| Node | Agent | Status | Kind | Tool Events | Executed Tools |\n"
    md += "|------|-------|--------|------|-------------|----------------|\n"
    for n in nodes:
        ev = n.get("metadata", {}).get("evidence") or {}
        tools = ev.get("executed_tools") or []
        md += f"| {n['plan_key'][:20]} | {n.get('agent','')} | {n['status']} | {n.get('kind','')} | {len(ev.get('tool_events',[]))} | {', '.join(tools[:4])} |\n"
    md += f"\n**DAG**: {len(plan_nodes)} plan + {len(synthesis)} synthesis\n"
    md += f"**Final Result** (excerpt):\n```\n{final[:800]}\n```\n\n"
    md += "> ⚠️ **MOCK LLM RESULT.** All LLM decisions are heuristic / scripted. "
    md += "Tool execution and A2A dispatch are real. This is NOT live-LLM evidence.\n"
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    return report


async def main() -> int:
    results = {}
    for key, (title, goal, checks) in SCENARIOS.items():
        results[key] = await run_scenario(
            key, title, goal, checks,
            Path(__file__).resolve().parents[1] / "data" / "phase2_e2e" / key,
        )
    passed = [k for k, r in results.items() if r["verdict"] == "PASS"]
    failed = [k for k, r in results.items() if r["verdict"] != "PASS"]
    print(f"\n{'='*60}\nE2E SUMMARY: {len(passed)}/3 PASSED")
    for k, r in results.items():
        print(f"  {k}: {r['verdict']} ({r['status']}, {len(r['nodes'])} nodes, {r['elapsed_s']}s)")
    if failed:
        print(f"FAILED: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))