#!/usr/bin/env python3
"""Mid_Test Phase 1: create group `midtest-software` with the teacher's ORIGINAL
role prompts (verbatim from Mid_Test.zip scenario_1_software_engineering/
01_role_initialization.md), activate 4 real agent processes, health-check them,
and run ONE small direct-A2A role probe against the real LLM.

Capabilities/tool_profiles mirror the validated SCENARIO_1_ROLES structure so
the planner's capability-coverage assignment works; only the identity prompts
and role names are the teacher's verbatim text.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
COORD = "http://127.0.0.1:8000"
GID = "midtest-software"
ART = ROOT / "artifacts" / "mid_test_local"

# ── Teacher's verbatim role prompts (Mid_Test.zip, scenario 1) ──────────────
TEACHER_ROLES = [
    {
        "agent_id": "lead",
        "role_name": "Technical Lead",
        "role_prompt": (
            "You are the technical lead of a software development team. You are "
            "responsible for understanding the user\u2019s goals, shaping the overall "
            "technical approach, coordinating software delivery, and discussing "
            "important questions and trade-offs with the user."
        ),
        "capabilities": ["planning", "task_decomposition", "delegation",
                         "synthesis", "evidence_synthesis", "web_search"],
        "tool_profile": "research",
    },
    {
        "agent_id": "dev",
        "role_name": "Software Developer",
        "role_prompt": (
            "You are the software developer on a software development team. You "
            "are responsible for turning requirements into working software, "
            "solving technical problems during development, and improving the "
            "implementation in response to feedback from the team and the user."
        ),
        "capabilities": ["coding", "sandbox_execution", "file_editing",
                         "python_debugging"],
        "tool_profile": "coding",
    },
    {
        "agent_id": "qa",
        "role_name": "Test Engineer",
        "role_prompt": (
            "You are the test engineer on a software development team. You are "
            "responsible for examining the software from the user\u2019s perspective "
            "and assessing its quality. Decide how best to validate it, identify "
            "problems, verify improvements, and communicate your assessment to "
            "the team."
        ),
        "capabilities": ["testing", "sandbox_execution", "statistics",
                         "numerical_analysis"],
        "tool_profile": "coding",
    },
    {
        "agent_id": "release",
        "role_name": "Release Manager",
        "role_prompt": (
            "You are the release manager of a software development team. You are "
            "responsible for version management, delivering runnable software, "
            "and preparing release notes so that users can obtain and use the "
            "team\u2019s work. You also support subsequent version updates."
        ),
        "capabilities": ["review", "synthesis", "file_editing",
                         "documentation_research"],
        "tool_profile": "analysis",
    },
]


async def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    results = {"group_id": GID, "teacher_prompts_verbatim": TEACHER_ROLES}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{COORD}/groups",
                         json={"group_id": GID,
                               "group_name": "MidTest Software Team (teacher scenario 1)",
                               "agents": TEACHER_ROLES})
        print("create:", r.status_code, r.text[:200], flush=True)
        results["create_rc"] = r.status_code
        if r.status_code != 200:
            return 1

        t0 = time.monotonic()
        r = await c.post(f"{COORD}/groups/{GID}/activate")
        act = r.json()
        urls = act.get("agents") or {}
        print(f"activate: rc={r.status_code} in {time.monotonic()-t0:.1f}s", flush=True)
        for aid, u in sorted(urls.items()):
            print(f"  agent {aid} -> {u}", flush=True)
        results["activate"] = act

        # health check each spawned agent
        healthy = {}
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and len(healthy) < len(urls):
            for aid, u in urls.items():
                if aid in healthy:
                    continue
                try:
                    rr = await c.get(f"{u}/health", timeout=5)
                    if rr.status_code == 200:
                        healthy[aid] = rr.json()
                        print(f"  healthy: {aid} {rr.json()}", flush=True)
                except httpx.HTTPError:
                    pass
            await asyncio.sleep(2)
        results["healthy"] = healthy
        if len(healthy) != 4:
            print(f"SETUP: FAIL only {len(healthy)}/4 agents healthy")
            (ART / "group_setup.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            return 1

        # ONE role probe through real A2A (legacy direct path, no workflow gate):
        # the answer must reflect the teacher's Technical Lead identity and must
        # NOT be a mock template ("[Mock-").
        from agent_ecosystem.a2a_delegate import send_a2a_text
        lead_url = urls.get(f"{GID}:lead")
        probe_q = ("In one or two sentences, introduce your role on this team and "
                   "your main responsibilities. Do not call any tools.")
        t0 = time.monotonic()
        state, text = await send_a2a_text(lead_url, probe_q, timeout=180.0,
                                          return_status=True)
        dt = time.monotonic() - t0
        print(f"\nrole probe ({dt:.1f}s) state={state}")
        print("lead says:", text[:600], flush=True)
        is_mock = "[Mock-" in text
        results["role_probe"] = {"url": lead_url, "state": state,
                                 "latency_s": round(dt, 1), "reply": text,
                                 "looks_like_mock": is_mock}
        ok = (state in ("completed", "TASK_STATE_COMPLETED")) and text and not is_mock
        (ART / "group_setup.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\nSETUP:", "PASS" if ok else "FAIL")
        return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
