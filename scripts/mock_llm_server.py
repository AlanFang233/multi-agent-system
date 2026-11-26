"""Offline mock OpenAI-compatible LLM server for E2E testing.

Supports three request kinds:
1. PLANNER mode  — system prompt contains "planning module": builds an
   ExecutionPlan JSON from the goal using catalog-driven keyword heuristics
   (bilingual zh/en). Never hardcoded to one scenario: node set and
   capabilities are derived from the goal keywords and the ACTUAL agent
   catalog embedded in the prompt, so it works for the default 3 agents and
   for dynamic group agents alike.
2. WORKFLOW-NODE mode — user prompt contains "[WORKFLOW TASK]": role handlers
   execute tools when relevant (coding roles run the real Docker sandbox flow)
   and never delegate autonomously (the Coordinator owns the DAG).
3. AUTONOMOUS mode (legacy Phase 0) — discover/delegate chains unchanged.

Test hooks (explicit, documented):
- "[MOCK_PLAN_RAW]\n<text>" in the goal → returned verbatim (invalid-JSON tests)
- "[FORCE_CLARIFY]" in a workflow prompt → agent asks a clarification once

All results produced with this provider are MOCK and must never be reported
as live-LLM evidence.

Start:  ./.venv/bin/python scripts/mock_llm_server.py   (127.0.0.1:9100)
"""
from __future__ import annotations

import json
import re
import time
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request

LOSS_A = [2.5, 2.0, 1.6, 1.2, 0.9]
LOSS_B = [2.5, 1.8, 1.2, 0.8, 0.5]

MAIN_PY = '''"""Loss statistics demo — executed inside the Docker sandbox."""
import urllib.request

LOSS_A = [2.5, 2.0, 1.6, 1.2, 0.9]
LOSS_B = [2.5, 1.8, 1.2, 0.8, 0.5]


def summarize(name, values):
    mean = sum(values) / len(values)
    print(f"{name}: n={len(values)} first={values[0]} last={values[-1]} "
          f"mean={mean:.4f} min={min(values)} total_drop={values[0] - values[-1]:.4f}")


summarize("A", LOSS_A)
summarize("B", LOSS_B)

try:
    urllib.request.urlopen("http://example.com", timeout=3)
    print("NETWORK_UNEXPECTEDLY_AVAILABLE")
except Exception as exc:
    print(f"NETWORK_BLOCKED_AS_EXPECTED: {type(exc).__name__}")

print("SANDBOX_EXECUTION_OK")
'''

PRIMES_PY = '''"""Prime numbers from 1 to 100 — executed inside the Docker sandbox."""
primes = [n for n in range(2, 101)
          if all(n % d for d in range(2, int(n ** 0.5) + 1))]
print(f"PRIME_COUNT={len(primes)}")
print(f"PRIME_SUM={sum(primes)}")
print(f"PRIMES={primes}")
print("SANDBOX_EXECUTION_OK")
'''

CALC_PY = '''"""Simple calculator with self-tests — executed inside the Docker sandbox."""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


assert add(2, 3) == 5
assert add(-1, 1) == 0
assert subtract(5, 3) == 2
assert subtract(0, 7) == -7
print(f"CALC_TESTS_PASSED add(2,3)={add(2, 3)} subtract(5,3)={subtract(5, 3)}")
print("SANDBOX_EXECUTION_OK")
'''

DELEGATE_TO_CODING = (
    "请在 Docker 沙箱中实现并验证一个小程序：创建沙箱，写入 main.py，"
    f"计算两组训练损失序列 A={LOSS_A} 与 B={LOSS_B} 的数量、首末值、均值、最小值与总降幅，"
    "同时验证沙箱内无法访问网络，然后运行该程序并返回真实输出。"
    "完成执行后，请把两组序列委托给 Analysis Agent 做统计分析。"
)
DELEGATE_TO_ANALYSIS = (
    "请使用 loss-analysis 技能和 skills:loss_metrics 工具，对以下两组训练损失序列做统计比较，"
    f"并给出简明分析报告：A={LOSS_A}, B={LOSS_B}。"
)

app = FastAPI(title="Mock LLM Server")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-llm"}


# ── helpers ──────────────────────────────────────────────────────────────────


def _role_of(system_text: str) -> str:
    if "planning module" in system_text:
        return "planner"
    # Dynamic group roles (Scenario 1 dev team)
    if "Technical Lead" in system_text:
        return "lead"
    if "Software Developer" in system_text:
        return "dev"
    if "QA Engineer" in system_text or "QA (Quality Assurance) Engineer" in system_text:
        return "qa"
    if "Release Manager" in system_text:
        return "release"
    if "Research Agent" in system_text:
        return "research"
    if "Coding Agent" in system_text:
        return "coding"
    if "Analysis Agent" in system_text:
        return "analysis"
    return "unknown"


def _find_fn(tools: list[dict], suffix: str) -> str | None:
    for t in tools or []:
        name = t.get("function", {}).get("name", "")
        if name.endswith(suffix):
            return name
    return None


def _tool_contents(messages: list[dict]) -> list[str]:
    return [m.get("content") or "" for m in messages if m.get("role") == "tool"]


def _search_tool_results(messages: list[dict], pattern: str) -> str | None:
    for content in reversed(_tool_contents(messages)):
        match = re.search(pattern, content, re.S)
        if match:
            return match.group(1)
    return None


def _sandbox_paths(messages: list[dict]) -> tuple[str | None, str | None]:
    # Matches both the legacy layout (sandboxes/<32-hex>) and per-group bundle
    # layouts (sandboxes/<group-id>/workspace/<32-hex>) produced by
    # team_builder.write_bundle via MAS_SANDBOX_ROOT.
    ws = _search_tool_results(
        messages, r'([/A-Za-z0-9_.\-]*mcp_workspace/sandboxes/'
                  r'(?:[a-z0-9\-]+/workspace/)?[0-9a-f]{32})')
    if ws:
        return ws, ws.rstrip("/").split("/")[-1]
    return None, None


def _completion(content: str | None, tool_calls: list[dict] | None = None) -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["content"] = None
        message["tool_calls"] = tool_calls
    return {
        "id": f"chatcmpl-mock-{uuid4().hex[:10]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "mock-gpt",
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": "tool_calls" if tool_calls else "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _call(fn_name: str, arguments: dict) -> dict:
    return {
        "id": f"call-mock-{uuid4().hex[:10]}",
        "type": "function",
        "function": {"name": fn_name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


def _user_text(messages: list[dict]) -> str:
    """The task prompt. The runner may append extra user messages AFTER it
    (long-term memory retrieval context, clarification answers), so pick the
    first user message carrying a task marker instead of the last one."""
    users = [m.get("content") or "" for m in messages if m.get("role") == "user"]
    for content in users:
        if "[WORKFLOW TASK]" in content or "[GOAL]" in content:
            return content
    return users[0] if users else ""


def _has_marker(messages: list[dict], marker: str) -> bool:
    return any(marker in (m.get("content") or "")
               for m in messages if m.get("role") == "user")


# ── planner: catalog-driven heuristic planning ──────────────────────────────

def _goal_text(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "user":
            content = m.get("content") or ""
            match = re.search(r"\[GOAL\]\n(.*?)(?:\n\n\[|$)", content, re.S)
            if match:
                return match.group(1).strip()
            return content
    return ""


def _parse_catalog(messages: list[dict]) -> dict[str, list[str]]:
    """{agent_id: [caps]} from the [AGENT CATALOG] section (ids may be namespaced)."""
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content") or ""
        cat: dict[str, list[str]] = {}
        for line in content.splitlines():
            match = re.search(r"^-\s+([\w:.-]+):.*?capabilities=([\w,]+)", line)
            if match:
                cat[match.group(1)] = [c for c in match.group(2).split(",") if c]
        if cat:
            return cat
    return {}


# keyword → capability preference lists (checked in order against the real pool)
_KW = {
    "research": (r"研究|调研|资料|检索|查询|research|retriev|investigat|look\s*up",
                 ["web_search", "documentation_research", "research"]),
    "code": (r"代码|编程|程序|实现|编写|开发|code|coding|implement|program|build|write|develop",
             ["coding", "file_editing", "python_debugging", "sandbox_execution"]),
    "test": (r"测试|试验|实验|验证|质检|test|verif|qa\b",
             ["testing", "sandbox_execution", "numerical_analysis"]),
    "analyze": (r"分析|数据|统计|analy|data|statis|metric",
                ["statistics", "numerical_analysis", "data_analysis", "loss_metrics"]),
    "report": (r"总结|报告|汇总|综述|交付|summary|summar|report|release|conclu",
               ["synthesis", "statistics", "evidence_synthesis", "review"]),
}


def _kw_hit(kind: str, text: str) -> bool:
    return re.search(_KW[kind][0], text, re.I) is not None


def _pick_cap(kind: str, pool: set[str]) -> str | None:
    for cap in _KW[kind][1]:
        if cap in pool:
            return cap
    return None


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
_EN_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


def _count_parallel_topics(goal: str) -> int:
    if not (re.search(r"分别|独立主题|parallel|respectively|independent", goal, re.I)):
        return 0
    m = re.search(r"(\d+|[一二两三四五六七八])\s*[个项种]?", goal)
    if m:
        raw = m.group(1)
        if raw.isdigit():
            return min(int(raw), 8)
        return _CN_NUM.get(raw, 0)
    m = re.search(r"\b(one|two|three|four|five|six|seven)\b", goal, re.I)
    if m:
        return _EN_NUM.get(m.group(1).lower(), 0)
    return 3


def _subtask(key: str, description: str, cap: str, deps: list[str], priority: int = 0) -> dict:
    return {"key": key, "description": description, "required_capabilities": [cap],
            "depends_on": deps, "priority": priority, "kind": "generic"}


def _build_plan(goal: str, cat: dict[str, list[str]]) -> dict:
    pool: set[str] = set()
    for caps in cat.values():
        pool.update(caps)
    if not pool:
        pool = {"web_search"}

    n_parallel = _count_parallel_topics(goal)
    if n_parallel >= 2:
        cap = _pick_cap("research", pool) or sorted(pool)[0]
        subs = [_subtask(f"topic_{i+1}",
                         f"独立研究主题 {i+1}（{goal[:100]}）", cap, [])
                for i in range(n_parallel)]
        return {"goal_summary": goal[:200], "subtasks": subs}

    subs: list[dict] = []
    deps: list[str] = []
    if _kw_hit("research", goal):
        cap = _pick_cap("research", pool)
        if cap:
            subs.append(_subtask("research_background",
                                 f"研究背景与资料收集：{goal[:150]}", cap, []))
            deps = ["research_background"]
    if _kw_hit("code", goal):
        cap = _pick_cap("code", pool)
        if cap:
            subs.append(_subtask("implement",
                                 f"实现（编写并准备代码）：{goal[:150]}", cap, list(deps)))
            deps = deps + ["implement"]
    if _kw_hit("test", goal):
        cap = _pick_cap("test", pool)
        if cap:
            subs.append(_subtask("test_verify",
                                 f"在 Docker 沙箱中测试并验证实现，运行代码并报告真实输出：{goal[:150]}",
                                 cap, list(deps)))
            deps = deps + ["test_verify"]
    if _kw_hit("analyze", goal) and not subs:
        cap = _pick_cap("analyze", pool)
        if cap:
            subs.append(_subtask("analyze", f"分析：{goal[:150]}", cap, list(deps)))
            deps = deps + ["analyze"]
    if _kw_hit("report", goal):
        cap = _pick_cap("report", pool)
        if cap:
            subs.append(_subtask("final_report",
                                 f"基于全部前序结果产出最终报告/摘要：{goal[:150]}",
                                 cap, list(deps), priority=1))
    if not subs:
        cap_r = _pick_cap("research", pool) or sorted(pool)[0]
        cap_s = _pick_cap("report", pool) or cap_r
        subs = [
            _subtask("gather", f"查询并整理相关资料：{goal[:150]}", cap_r, []),
            _subtask("summarize", f"综合分析并总结：{goal[:150]}", cap_s, ["gather"]),
        ]
    return {"goal_summary": goal[:200], "subtasks": subs}


def _planner_response(goal: str, cat: dict[str, list[str]], tools: list[dict]) -> dict:
    raw_marker = re.search(r"\[MOCK_PLAN_RAW\]\n(.*)", goal, re.S)
    if raw_marker:
        return _completion(raw_marker.group(1).strip())
    plan = _build_plan(goal, cat)
    fn = _find_fn(tools, "submit_plan")
    if fn:
        return _completion(None, [_call(fn, plan)])
    return _completion(json.dumps(plan, ensure_ascii=False))


# ── script selection for sandbox flows ──────────────────────────────────────

def _select_script(prompt: str) -> tuple[str, str]:
    low = prompt.lower()
    if "prime" in low or "质数" in prompt or "素数" in prompt:
        return PRIMES_PY, "primes.py"
    if "calculator" in low or "计算器" in prompt:
        return CALC_PY, "calculator.py"
    return MAIN_PY, "main.py"


_OUTPUT_PATTERN = (
    r'(PRIME_COUNT=\d+[\s\\n]*PRIME_SUM=\d+|CALC_TESTS_PASSED[^"\\]*'
    r'|A: n=5.*?SANDBOX_EXECUTION_OK|SANDBOX_EXECUTION_OK)'
)


# ── workflow-node mode handlers (no autonomous delegation) ───────────────────


def _wf_text(role_label: str, messages: list[dict]) -> dict:
    """Text-only workflow handler for research/lead/release style nodes."""
    prompt = _user_text(messages)
    if "[SYNTHESIS]" in prompt:
        match = re.search(r"\[DEPENDENCY RESULTS\].*?(?=\n\n\[|$)", prompt, re.S)
        dep_text = match.group(0)[:2500] if match else "（无上游结果）"
        return _completion(
            f"[Mock-Synthesis/{role_label}] 综合报告（MOCK LLM 编排，下游结果为真实产生）\n"
            f"{dep_text}\n"
            "结论：各节点结果已整合；未在上述材料中出现的内容一律未声称。"
        )
    match = re.search(r"\[DEPENDENCY RESULTS\](.*?)\n\n\[", prompt, re.S)
    deps = match.group(1).strip()[:800] if match else ""
    task_m = re.search(r"\[WORKFLOW TASK\]\s*(.*)", prompt)
    task_desc = task_m.group(1).strip()[:150] if task_m else ""
    body = (f"[Mock-{role_label}/WF] 节点任务完成（MOCK LLM 编排）：{task_desc}\n"
            "本节点为离线 Mock 决策，未执行真实网络检索。")
    if deps and deps != "(none)":
        body += f"\n上游结果摘要：{deps}"
    return _completion(body)


def _wf_sandbox(role_label: str, messages: list[dict], tools: list[dict]) -> dict:
    """Sandbox-executing workflow handler for coding/qa style nodes."""
    prompt = _user_text(messages)
    if not re.search(r"沙箱|执行|运行|代码|程序|实现|测试|sandbox|run|execut|implement|test|code",
                     prompt, re.I):
        return _wf_text(role_label, messages)
    script, filename = _select_script(prompt)
    step = len(_tool_contents(messages))
    if step == 0:
        fn = _find_fn(tools, "sandbox_create")
        if fn:
            return _completion(None, [_call(fn, {})])
    if step == 1:
        workspace, _ = _sandbox_paths(messages)
        fn = _find_fn(tools, "filesystem_code_write_file")
        if fn and workspace:
            return _completion(None, [_call(fn, {"path": f"{workspace}/{filename}",
                                                 "content": script})])
    if step == 2:
        _, sandbox_id = _sandbox_paths(messages)
        fn = _find_fn(tools, "sandbox_run_python")
        if fn and sandbox_id:
            return _completion(None, [_call(fn, {"sandbox_id": sandbox_id,
                                                 "script": filename,
                                                 "timeout_seconds": 30})])
    run_out = _search_tool_results(messages, _OUTPUT_PATTERN) or "（见工具原始输出）"
    return _completion(
        f"[Mock-{role_label}/WF] 程序已在 Docker 沙箱中真实执行完毕（MOCK 编排）。\n"
        f"沙箱输出：{run_out[:800]}"
    )


# ── autonomous delegation handlers (legacy Phase 0 path, unchanged) ─────────


def _research_auto(messages: list[dict], tools: list[dict]) -> dict:
    step = len(_tool_contents(messages))
    if step == 0:
        fn = _find_fn(tools, "a2a_discover_agents")
        if fn:
            return _completion(None, [_call(fn, {})])
    if step <= 1:
        fn = _find_fn(tools, "a2a_delegate_task")
        if fn:
            return _completion(None, [_call(fn, {"agent_id": "coding",
                                                 "task": DELEGATE_TO_CODING})])
    coding_result = _search_tool_results(messages, r'(\[Mock-Coding\][^"]*)') or "（未获取到）"
    return _completion(
        "[Mock-Research] 端到端任务完成（Mock LLM 编排）。\n"
        f"Coding Agent 实测结果：{coding_result[:1200]}\n"
        "结论：A2A、Docker、Analysis 链路已打通（Mock 编排）。"
    )


def _coding_auto(messages: list[dict], tools: list[dict]) -> dict:
    step = len(_tool_contents(messages))
    if step == 0:
        fn = _find_fn(tools, "sandbox_create")
        if fn:
            return _completion(None, [_call(fn, {})])
    if step == 1:
        workspace, _ = _sandbox_paths(messages)
        fn = _find_fn(tools, "filesystem_code_write_file")
        if fn and workspace:
            return _completion(None, [_call(fn, {"path": f"{workspace}/main.py",
                                                 "content": MAIN_PY})])
    if step == 2:
        _, sandbox_id = _sandbox_paths(messages)
        fn = _find_fn(tools, "sandbox_run_python")
        if fn and sandbox_id:
            return _completion(None, [_call(fn, {"sandbox_id": sandbox_id,
                                                 "script": "main.py",
                                                 "timeout_seconds": 30})])
    if step == 3:
        fn = _find_fn(tools, "a2a_delegate_task")
        if fn:
            return _completion(None, [_call(fn, {"agent_id": "analysis",
                                                 "task": DELEGATE_TO_ANALYSIS})])
    run_out = _search_tool_results(messages, _OUTPUT_PATTERN) or "（见工具原始输出）"
    analysis_out = _search_tool_results(messages, r'(\[Mock-Analysis\][^"]*)') or "（未获取到）"
    return _completion(
        "[Mock-Coding] 程序已在 Docker 沙箱中真实执行完毕。\n"
        f"沙箱输出摘录：{run_out[:800]}\n"
        f"Analysis Agent 统计报告：{analysis_out[:1200]}"
    )


def _analysis_auto(messages: list[dict], tools: list[dict]) -> dict:
    step = len(_tool_contents(messages))
    prompt = _user_text(messages)
    if step == 0 and re.search(r"A=\[|loss|损失", prompt, re.I):
        fn = _find_fn(tools, "skills_load")
        if fn:
            return _completion(None, [_call(fn, {"name": "loss-analysis"})])
    if step == 1:
        fn = _find_fn(tools, "skills_loss_metrics")
        if fn:
            return _completion(None, [_call(fn, {
                "series_a": LOSS_A, "series_b": LOSS_B, "label_a": "A", "label_b": "B",
            })])
    metrics = _search_tool_results(messages, r'"total_decrease":\s*([0-9.]+)')
    if metrics:
        return _completion(
            f"[Mock-Analysis] 已加载 loss-analysis 技能并用 skills:loss_metrics 完成确定性统计："
            f"A 组总降幅 {metrics}；B 组收敛更快、末值更低。数据为任务给定示例序列，非真实实验日志。"
        )
    return _wf_text("Analysis", messages)


# ── request routing ─────────────────────────────────────────────────────────


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> dict:
    body = await request.json()
    messages = body.get("messages", [])
    tools = body.get("tools") or []
    tool_choice = body.get("tool_choice")
    system = (messages[0].get("content", "")
              if messages and messages[0].get("role") == "system" else "")
    role = _role_of(system)

    if isinstance(tool_choice, dict) and tool_choice.get("function", {}).get("name") == "submit_team":
        # This deterministic fixture supports explicitly numbered roles, not general NLP.
        payload = json.loads(messages[1]["content"])
        prompt = payload["request"]
        roles = re.findall(r"(?m)^\s*\d+[.)、:：]\s*(.+)$", prompt)
        if not roles or len(roles) > 8:
            return _completion(None, [_call("submit_team", {
                "error": "Mock team planning requires 1-8 numbered role lines (1. Lead: ...)"
            })])
        agents = []
        for i, line in enumerate(roles):
            role_title = re.split(r"[:：]", line, maxsplit=1)[0]
            coding = bool(re.search(r"开发|测试|implement|develop|test", role_title, re.I))
            profile = "coding" if coding else "research"
            caps = ["file_editing", "sandbox_execution"] if coding else ["documentation_research"]
            if i == 0:
                caps.append("evidence_synthesis")
            tools_for_role = (["filesystem_code:write_file", "sandbox:create",
                               "sandbox:run_python", "sandbox:run_pytest"] if coding
                              else ["fetch:fetch"])
            agents.append({
                "agent_id": f"agent-{i+1}", "role_name": role_title[:100],
                "responsibilities": [line], "capabilities": caps,
                "tool_profile": profile, "mcp_tools": tools_for_role,
                "skills": [{
                    "name": "role-workflow", "description": f"Mock workflow for role {i+1}",
                    "instructions": (
                        f"MOCK-generated instructions. Responsibility: {line}\n"
                        "Before execution inspect inputs. Use only authorized tools. "
                        "Cite actual tool evidence and verify acceptance criteria. "
                        "Report missing information using [[CLARIFY: question]]."
                    ),
                }],
            })
        declared = re.search(r"(?:创建|create)\s*(\d+)\s*(?:个|agents?)", prompt, re.I)
        count = int(declared.group(1)) if declared else len(roles)
        return _completion(None, [_call("submit_team", {
            "group_name": "Mock Generated Team", "agent_count": count, "agents": agents,
        })])

    if role == "planner":
        goal = _goal_text(messages)
        cat = _parse_catalog(messages)
        result = _planner_response(goal, cat, tools)
        n = 0
        try:
            payload = result["choices"][0]["message"]
            if payload.get("tool_calls"):
                n = len(json.loads(payload["tool_calls"][0]["function"]["arguments"])["subtasks"])
            else:
                n = len(json.loads(payload["content"])["subtasks"])
        except Exception:
            pass
        print(f"[mock-llm] planner subtasks={n} goal={goal[:60]!r}", flush=True)
        return result

    if tool_choice == "none" or not tools:
        return _completion(f"[Mock-{role}] 工具预算已用尽，基于已有工具结果结束（Mock LLM）。")

    workflow_mode = _has_marker(messages, "[WORKFLOW TASK]")

    # Test hook: forced clarification request (once; answered prompts pass
    # through). Synthesis nodes never clarify — the [FORCE_CLARIFY] marker
    # leaks into every node prompt via [WORKFLOW GOAL], but only worker nodes
    # should ask; synthesis just integrates whatever the workers produced.
    if workflow_mode and "[FORCE_CLARIFY]" in _user_text(messages) \
            and "[SYNTHESIS]" not in _user_text(messages) \
            and "[CLARIFICATION_ANSWER]" not in "".join(
                m.get("content") or "" for m in messages):
        print(f"[mock-llm] {role} -> clarify (forced)", flush=True)
        return _completion(
            "[[CLARIFY: 请确认期望的输出格式：JSON 还是 Markdown？]]"
        )

    if workflow_mode:
        handlers = {
            "research": lambda: _wf_text("Research", messages),
            "lead": lambda: _wf_text("Lead", messages),
            "release": lambda: _wf_text("Release", messages),
            "coding": lambda: _wf_sandbox("Coding", messages, tools),
            "dev": lambda: _wf_sandbox("Dev", messages, tools),
            "qa": lambda: _wf_sandbox("QA", messages, tools),
            "analysis": lambda: _analysis_auto(messages, tools),
        }
        handler = handlers.get(role)
        result = handler() if handler else _wf_text(role, messages)
        print(f"[mock-llm] WF role={role} step={len(_tool_contents(messages))} -> "
              f"{'tool_call' if result['choices'][0]['message'].get('tool_calls') else 'final'}",
              flush=True)
        return result

    handlers = {"research": _research_auto, "coding": _coding_auto,
                "analysis": _analysis_auto}
    handler = handlers.get(role)
    if handler is None:
        return _completion(f"[Mock-{role}] 未识别的角色（非工作流模式），直接结束。")
    result = handler(messages, tools)
    print(f"[mock-llm] role={role} step={len(_tool_contents(messages))} -> "
          f"{'tool_call' if result['choices'][0]['message'].get('tool_calls') else 'final'}",
          flush=True)
    return result


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9100, log_level="warning")
