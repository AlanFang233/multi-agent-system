#!/usr/bin/env python3
"""Prompt-driven team creation E2E (Mock LLM, real MCP/Docker/A2A).

Test A: two-agent team from a prompt — preview/confirm/provision/activate,
        role+prompt+skills+whitelist checks, REAL tool calls through the
        bundle registries (fetch / sandbox:create / skills:load), and a
        forbidden-tool gating probe.
Test B: teacher Scenario-1 four-role team created dynamically from a prompt,
        real A2A workflow with HITL approvals and real sandbox execution.
Test C: permission boundary — reject variant must FAIL the workflow with
        zero high-risk executions (no false COMPLETED).

Evidence JSON under artifacts/prompt_driven/. Exit 0 only if all checks pass.
"""
import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
COORD = "http://127.0.0.1:8000"
MEM_DEFAULT = ROOT / "data" / "agent_memory.sqlite"
ART = ROOT / "artifacts" / "prompt_driven"

RESULTS = []


def record(cid, name, ok, evidence=""):
    print(f"[PD] {'PASS' if ok else 'FAIL'} {cid}: {name} | {evidence}", flush=True)
    RESULTS.append({"id": cid, "name": name, "ok": bool(ok), "evidence": str(evidence)[:500]})


def save(name, obj):
    ART.mkdir(parents=True, exist_ok=True)
    (ART / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")


PROMPT_A = ("创建 2 个智能体:\n"
            "1. Research Analyst: 负责资料研究和事实核查\n"
            "2. Python Developer: 负责 Python 开发、运行和测试")

PROMPT_B = ("Create a 4-agent software development team:\n"
            "1. Technical Lead: 技术规划与协调\n"
            "2. Software Developer: 开发与实现\n"
            "3. QA Engineer (Test Engineer): 测试与质量\n"
            "4. Release Manager: 发布与交付说明")

GOAL_B = ("Write a Python function add(a, b) plus a small self-test, run both in the "
          "sandbox, and summarize the executed output.")


async def poll(client, wf, timeout, auto=None):
    deadline = time.monotonic() + timeout
    decided = 0
    while time.monotonic() < deadline:
        s = (await client.get(f"{COORD}/workflows/{wf}")).json()
        st = s["workflow"]["status"]
        if st in ("completed", "failed", "cancelled"):
            return s, decided
        if auto:
            pend = (await client.get(f"{COORD}/approvals",
                                     params={"workflow_id": wf, "status": "pending"})).json()
            for a in pend:
                await client.post(f"{COORD}/approvals/{a['approval_id']}/decide",
                                  json={"decision": auto, "decided_by": "pd-e2e"})
                print(f"  [hitl] {auto} {a['approval_id']} {a['tool_id']} "
                      f"node={a['node_id'][:12]} agent={a['agent_id']}", flush=True)
                decided += 1
        await asyncio.sleep(2)
    raise TimeoutError(wf)


def bundle_env(runtime: Path) -> dict:
    return {
        "MAS_MCP_REGISTRY": str(runtime / "mcp_registry.yaml"),
        "MAS_AGENT_REGISTRY": str(runtime / "agent_registry.yaml"),
        "MAS_AGENT_SKILLS": str(runtime / "agent_skills.yaml"),
        "MAS_MEMORY_CONFIG": str(runtime / "memory.yaml"),
        "OPENAI_API_KEY": "mock", "OPENAI_BASE_URL": "http://127.0.0.1:9100/v1",
        "OPENAI_MODEL": "mock",
    }


async def probe_tools(runtime: Path, ns_research: str, ns_dev: str) -> dict:
    """Real MCP calls through the group's own bundle registries (same call path
    the runner uses), plus allowlist gating probes."""
    env = bundle_env(runtime)
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    out = {}
    try:
        from agent_ecosystem.agent_skill_registry import AgentSkillRegistry
        from agent_ecosystem.dynamic_delegation import AgentDelegationRegistry
        from agent_ecosystem.tool_registry import ToolRegistry
        mcp = ToolRegistry(os.environ["MAS_MCP_REGISTRY"])
        deleg = AgentDelegationRegistry(mcp, config_path=os.environ["MAS_AGENT_REGISTRY"])
        reg = AgentSkillRegistry(deleg, config_path=os.environ["MAS_AGENT_SKILLS"])
        errors = await reg.refresh()
        out["refresh_errors"] = errors
        allowed_r = {t["tool_id"] for t in reg.list_for_agent(ns_research)}
        allowed_d = {t["tool_id"] for t in reg.list_for_agent(ns_dev)}
        out["allowed_research"] = sorted(allowed_r)
        out["allowed_dev"] = sorted(allowed_d)
        # real fetch call (search-family tool) by the research role; the fetch
        # MCP server refuses loopback addresses by design, so use a public URL
        r = await reg.call(ns_research, "fetch:fetch",
                           {"url": "https://example.com/", "max_length": 200})
        out["fetch_call"] = str(r)[:200]
        # real sandbox:create by the dev role (Docker)
        r2 = await reg.call(ns_dev, "sandbox:create", {})
        out["sandbox_create"] = str(r2)[:200]
        # skills:load exposed and callable per namespace
        r3 = await reg.call(ns_research, "skills:load", {"name": "role-workflow"})
        out["skills_load"] = str(r3)[:150]
        # forbidden tool must be refused by the allowlist (gating probe)
        try:
            await reg.call(ns_research, "filesystem_code:write_file",
                           {"path": "x.py", "content": "x"})
            out["forbidden_write"] = "NOT_BLOCKED"
        except Exception as exc:
            out["forbidden_write"] = f"{type(exc).__name__}: {exc}"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return out


async def main() -> int:
    async with httpx.AsyncClient(timeout=120) as c:
        # pre-clean leftover prompt-driven groups
        for g in (await c.get(f"{COORD}/groups")).json():
            if g["group_id"].startswith("team-"):
                await c.delete(f"{COORD}/groups/{g['group_id']}")

        # ── Test A ────────────────────────────────────────────────────────
        plan = (await c.post(f"{COORD}/groups/plan", json={"prompt": PROMPT_A})).json()
        save("testA_proposal.json", plan)
        spec = plan["spec"]
        record("A1", "Preview: exactly 2 agents from prompt",
               plan.get("status") == "awaiting_confirmation" and spec["agent_count"] == 2
               and len(spec["agents"]) == 2,
               f"roles={[a['role_name'] for a in spec['agents']]}")
        profiles = [a["tool_profile"] for a in spec["agents"]]
        tools = [a["mcp_tools"] for a in spec["agents"]]
        record("A2", "Roles/profiles/tools per prompt intent",
               profiles == ["research", "coding"]
               and any(t.startswith("fetch:") or t.startswith("free_search:")
                       for t in tools[0])
               and any(t.startswith("sandbox:") for t in tools[1]),
               f"profiles={profiles} tools={tools}")
        record("A3", "Skills generated per agent (1-4, instruction-only)",
               all(1 <= len(a["skills"]) <= 4 and a["skills"][0]["instructions"]
                   for a in spec["agents"]),
               f"skills={[ [s['name'] for s in a['skills']] for a in spec['agents']]}")
        r = await c.post(f"{COORD}/groups/provision", json={"group_id": plan["group_id"]})
        record("A4", "Provision without confirm refused", r.status_code == 409,
               f"rc={r.status_code}")
        r = await c.post(f"{COORD}/groups/provision",
                         json={"group_id": plan["group_id"], "confirmed": True})
        prov = r.json()
        urls = prov.get("agents") or {}
        record("A5", "Confirm → provision → activate (2 live agents)",
               r.status_code == 200 and len(urls) == 2, f"urls={urls}")
        gid_a = plan["group_id"]
        runtime = Path(prov["runtime_directory"])
        ns = sorted(urls)
        ns_res = next(n for n in ns if n.endswith(":agent-1"))
        ns_dev = next(n for n in ns if n.endswith(":agent-2"))
        probes = await probe_tools(runtime, ns_res, ns_dev)
        save("testA_tool_probes.json", probes)
        record("A6", "Research role REAL fetch call succeeds",
               "'is_error': False" in probes["fetch_call"],
               probes["fetch_call"][:120])
        record("A7", "Dev role REAL sandbox:create succeeds (Docker)",
               "sandbox_id" in probes["sandbox_create"], probes["sandbox_create"][:120])
        record("A8", "skills:load callable per namespace",
               "role-workflow" in probes["skills_load"] or "MOCK" in probes["skills_load"],
               probes["skills_load"][:100])
        record("A9", "Forbidden tool refused for research role",
               "NOT_BLOCKED" not in probes["forbidden_write"],
               probes["forbidden_write"][:160])
        record("A10", "Whitelist = bundle permissions (minimal, profile-bounded)",
               set(probes["allowed_research"]) <=
               {"fetch:fetch", "filesystem:read_text_file", "filesystem:list_directory",
                "filesystem:read_multiple_files", "filesystem:get_file_info",
                "filesystem:read_file", "skills:load",
                "a2a:delegate_task", "a2a:discover_agents"}
               and "skills:load" in probes["allowed_research"]
               and "skills:load" in probes["allowed_dev"],
               f"research={len(probes['allowed_research'])} dev={len(probes['allowed_dev'])} tools")
        await c.delete(f"{COORD}/groups/{gid_a}")

        # ── Test B ────────────────────────────────────────────────────────
        plan = (await c.post(f"{COORD}/groups/plan", json={"prompt": PROMPT_B})).json()
        spec = plan["spec"]
        record("B1", "4-role team proposed dynamically (not hardcoded ids)",
               spec["agent_count"] == 4 and plan["group_id"].startswith("team-"),
               f"roles={[a['role_name'] for a in spec['agents']]}")
        r = await c.post(f"{COORD}/groups/provision",
                         json={"group_id": plan["group_id"], "confirmed": True})
        prov = r.json()
        urls = prov.get("agents") or {}
        gid_b = plan["group_id"]
        record("B2", "Team provisioned + activated (4 agents)",
               r.status_code == 200 and len(urls) == 4, f"urls={sorted(urls)}")
        r = await c.post(f"{COORD}/groups/{gid_b}/messages", json={"goal": GOAL_B})
        wf = r.json()["workflow_id"]
        snap, decided = await poll(c, wf, 900, auto="approve")
        st = snap["workflow"]["status"]
        agents_used = sorted({n.get("assigned_agent") or "" for n in snap["nodes"]})
        record("B3", "Group workflow COMPLETED via real A2A (>=2 roles)",
               st == "completed" and len(agents_used) >= 2,
               f"wf={wf} status={st} approvals={decided} agents={agents_used}")
        mem_db = Path(prov["runtime_directory"]) / "agent_memory.sqlite"
        conn = sqlite3.connect(str(mem_db), timeout=10)
        rows = conn.execute(
            "SELECT tool_id FROM tool_results WHERE task_id LIKE ? AND tool_id IN "
            "('sandbox:run_python','sandbox:run_pytest')", (wf + ":%",)).fetchall()
        conn.close()
        record("B4", "Real sandbox execution in GROUP memory audit",
               len(rows) >= 1, f"{[r[0] for r in rows]} in {mem_db.name}")
        save("testB_workflow.json", {"workflow_id": wf, "snapshot": snap,
                                     "approvals_decided": decided})

        # ── Test C: reject variant ────────────────────────────────────────
        r = await c.post(f"{COORD}/groups/{gid_b}/messages", json={"goal": GOAL_B})
        wf2 = r.json()["workflow_id"]
        pend = []
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and not pend:
            pend = (await c.get(f"{COORD}/approvals",
                                params={"workflow_id": wf2, "status": "pending"})).json()
            if not pend:
                await asyncio.sleep(2)
        if not pend:
            record("C1", "Reject variant reached approval gate", False, "no pending")
        else:
            await c.post(f"{COORD}/approvals/{pend[0]['approval_id']}/decide",
                         json={"decision": "reject", "decided_by": "pd-e2e"})
            snap2, _ = await poll(c, wf2, 300)
            st2 = snap2["workflow"]["status"]
            conn = sqlite3.connect(str(mem_db), timeout=10)
            danger = conn.execute(
                "SELECT COUNT(*) FROM tool_results WHERE task_id LIKE ? AND tool_id IN "
                "('filesystem_code:write_file','filesystem_code:edit_file',"
                "'sandbox:run_python','sandbox:run_pytest')", (wf2 + ":%",)).fetchone()[0]
            conn.close()
            record("C1", "Rejected approval → workflow FAILED, zero high-risk exec",
                   st2 == "failed" and danger == 0, f"wf={wf2} status={st2} execs={danger}")
        await c.delete(f"{COORD}/groups/{gid_b}")

    save("summary.json", {"results": RESULTS,
                          "failed": [r["id"] for r in RESULTS if not r["ok"]]})
    n_ok = sum(1 for r in RESULTS if r["ok"])
    print(f"\n[PD] ==== {n_ok}/{len(RESULTS)} PASS ====", flush=True)
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
