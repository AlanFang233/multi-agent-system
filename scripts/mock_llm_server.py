"""Offline mock OpenAI-compatible LLM server for E2E testing.

Extended for Phase 2: planner mode (heuristic DAG generation from goal + catalog)
and workflow-node mode (agent dispatched by Coordinator, no autonomous delegation).

Start:  ./.venv/bin/python scripts/mock_llm_server.py
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


# ── helpers ──────────────────────────────────────────────────────────────────


def _role_of(system_text: str) -> str:
    if "planning module of the Coordinator" in system_text or "planning module" in system_text:
        return "planner"
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
        match = re.search(pattern, content)
        if match:
            return match.group(1)
    return None


def _sandbox_paths(messages: list[dict]) -> tuple[str | None, str | None]:
    ws = _search_tool_results(messages, r'([/A-Za-z0-9_.\-]*mcp_workspace/sandboxes/[0-9a-f]{32})')
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


def _has_marker(messages: list[dict], marker: str) -> bool:
    for m in reversed(messages):
        if m.get("role") == "user" and marker in (m.get("content") or ""):
            return True
    return False


def _goal_text(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "user":
            content = m.get("content") or ""
            match = re.search(r"\[GOAL\]\n(.*?)(?:\n\n\[|$)", content, re.S)
            if match:
                return match.group(1).strip()
            return content[:500]
    return ""


def _parse_catalog(messages: list[dict]) -> dict[str, list[str]]:
    """Return {agent_id: [cap1,cap2,...]} from the AGENT CATALOG section."""
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content") or ""
        cat: dict[str, list[str]] = {}
        for line in content.splitlines():
            match = re.search(r"- (\w+):.*?capabilities=([\w,]+)", line)
            if match:
                cat[match.group(1)] = [c.strip() for c in match.group(2).split(",") if c.strip()]
        if cat:
            return cat
    return {}


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}


def _count_topics(goal: str) -> int:
    # "分别研究三个独立主题" → 3
    m = re.search(r"(\d+|[一二三四五六七八九])\s*[个项种类]", goal)
    if m:
        raw = m.group(1)
        if raw.isdigit():
            return int(raw)
        return sum(_CN_NUM.get(ch, 0) for ch in raw) or len(raw)
    # keyword "分别研究" alone suggests 2+
    if "分别" in goal and ("研究" in goal or "主题" in goal):
        return 3
    return 0


def _pick_cap(cat: dict[str, list[str]], agent: str, preferred: str, fallback: str) -> list[str]:
    """Return [cap] from agent's catalog, preferring the named cap, falling back."""
    caps = cat.get(agent, [])
    if preferred in caps:
        return [preferred]
    if fallback and fallback in caps:
        return [fallback]
    return [caps[0]] if caps else ["web_search"]


# ── plan object construction ─────────────────────────────────────────────────

def _plan_json(subtasks: list[dict]) -> dict:
    return {"goal_summary": "mock plan", "subtasks": subtasks}


def _subtask(key: str, description: str, caps: list[str],
             deps: list[str] | None = None, priority: int = 0,
             kind: str = "generic") -> dict:
    return {
        "key": key, "description": description, "required_capabilities": caps,
        "depends_on": deps or [], "priority": priority, "kind": kind,
    }


def _planner_response(goal: str, cat: dict[str, list[str]],
                      tools: list[dict]) -> dict:
    """Heuristic plan builder respecting the actual agent catalog."""
    # [MOCK_PLAN_RAW] → raw JSON test (invalid / cycle / schema-violation)
    raw_marker = re.search(r'\[MOCK_PLAN_RAW\]\n(.*)', goal, re.S)
    if raw_marker:
        return _completion(raw_marker.group(1).strip())

    # Scenario C: parallel independent topics
    n = _count_topics(goal)
    if n >= 2 and "综合" in goal and "独立" in goal or n >= 3:
        subtasks = [
            _subtask(f"topic_{i+1}", f"研究主题 {i+1}", _pick_cap(cat, "research", "web_search", "evidence_synthesis"))
            for i in range(n)
        ]
        return _respond_plan(subtasks, tools)

    # Scenario B: ML chain (research→data→code→experiment→report)
    if re.search(r"实验|代码|编程|程序|项目", goal) and re.search(r"报[告]|总结|分析", goal):
        subtasks = [
            _subtask("research_ml", "研究相关理论与方法",
                     _pick_cap(cat, "research", "web_search", "evidence_synthesis")),
            _subtask("analyze_data", "分析数据集特征与需求",
                     _pick_cap(cat, "analysis", "numerical_analysis", "statistics"), ["research_ml"]),
            _subtask("write_code", "编写核心实现代码",
                     _pick_cap(cat, "coding", "file_editing", "python_debugging"), ["analyze_data"]),
            _subtask("run_experiment", "在 Docker 沙箱中执行实验并返回真实输出",
                     _pick_cap(cat, "coding", "sandbox_execution", "python_debugging"), ["write_code"]),
            _subtask("final_report", "整合各阶段结果形成完整分析报告",
                     _pick_cap(cat, "analysis", "statistics", "numerical_analysis"),
                     ["research_ml", "analyze_data", "write_code", "run_experiment"], priority=9),
        ]
        return _respond_plan(subtasks, tools)

    # Scenario A / default: chain (research → analysis/summary)
    subtasks = [
        _subtask("gather", "查询并整理相关资料",
                 _pick_cap(cat, "research", "web_search", "evidence_synthesis")),
        _subtask("summarize", "对整理后的资料进行综合分析总结",
                 _pick_cap(cat, "analysis", "statistics", "numerical_analysis"), ["gather"]),
    ]
    return _respond_plan(subtasks, tools)


def _respond_plan(subtasks: list[dict], tools: list[dict]) -> dict:
    plan_dict = _plan_json(subtasks)
    fn = _find_fn(tools, "submit_plan")
    if fn:
        return _completion(None, [_call(fn, plan_dict)])
    return _completion(json.dumps(plan_dict, ensure_ascii=False))


# ── workflow-node mode handlers (no autonomous delegation) ───────────────────


def _wf_research(messages: list[dict], tools: list[dict]) -> dict:
    if _has_marker(messages, "[SYNTHESIS]"):
        deps = ""
        for m in messages:
            if m.get("role") == "user":
                deps = str(m.get("content", ""))
                break
        match = re.search(r"\[DEPENDENCY RESULTS\].*?(?=\n\n\[|$)", deps, re.S)
        dep_text = match.group(0)[:1500] if match else "（无上游结果）"
        return _completion(
            "[Mock-Synthesis] 综合报告（MOCK LLM 编排）\n"
            f"各节点真实执行结果摘要（来自工具审计，非 Agent 自述）：\n{dep_text}\n"
            "结论：全链路协同完成，实际工具输出已整合（Mock 编排，工具真实执行）。"
        )
    return _completion(
        "[Mock-Research/WF] 节点任务完成（MOCK LLM 编排，离线验证）。"
        "已按要求整理相关材料（离线模拟，无真实网络检索；如需真实检索请在 live provider 下运行）。"
    )


def _wf_coding(messages: list[dict], tools: list[dict]) -> dict:
    prompt = ""
    for m in messages:
        if m.get("role") == "user":
            prompt = m.get("content") or ""
            break
    if re.search(r'沙箱|执行|运行|代码|程序|sandbox|run', prompt):
        # Sandbox flow WITHOUT delegation (Coordinator handles the DAG).
        step = len(_tool_contents(messages))
        if step == 0:
            fn = _find_fn(tools, "sandbox_create")
            if fn:
                return _completion(None, [_call(fn, {})])
        if step == 1:
            workspace, _ = _sandbox_paths(messages)
            fn = _find_fn(tools, "filesystem_code_write_file")
            if fn and workspace:
                return _completion(None, [_call(fn, {"path": f"{workspace}/main.py", "content": MAIN_PY})])
        if step == 2:
            _, sandbox_id = _sandbox_paths(messages)
            fn = _find_fn(tools, "sandbox_run_python")
            if fn and sandbox_id:
                return _completion(None, [_call(fn, {"sandbox_id": sandbox_id, "script": "main.py", "timeout_seconds": 30})])
        run_out = _search_tool_results(messages, r'(SANDBOX_EXECUTION_OK)') or "（见工具原始输出）"
        return _completion(
            f"[Mock-Coding/WF] 沙箱执行完成（MOCK 编排）。输出：{run_out}"
        )
    return _completion("[Mock-Coding/WF] 节点任务完成（MOCK 编排，无工具调用需求）。")


def _wf_analysis(messages: list[dict], tools: list[dict]) -> dict:
    prompt = ""
    for m in messages:
        if m.get("role") == "user":
            prompt = m.get("content") or ""
            break
    if re.search(r'A=\[|B=\[|损失|lost|loss_metrics', prompt):
        return _analysis_auto(messages, tools)
    return _completion("[Mock-Analysis/WF] 节点任务完成（MOCK 编排，统计推断基于给定数据）。")


# ── autonomous delegation handlers (no WF check — dispatch choice is outside) ───────

def _research_auto(messages: list[dict], tools: list[dict]) -> dict:
    step = len(_tool_contents(messages))
    if step == 0:
        fn = _find_fn(tools, "a2a_discover_agents")
        if fn:
            return _completion(None, [_call(fn, {})])
    if step <= 1:
        fn = _find_fn(tools, "a2a_delegate_task")
        if fn:
            return _completion(None, [_call(fn, {"agent_id": "coding", "task": DELEGATE_TO_CODING})])
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
            return _completion(None, [_call(fn, {"path": f"{workspace}/main.py", "content": MAIN_PY})])
    if step == 2:
        _, sandbox_id = _sandbox_paths(messages)
        fn = _find_fn(tools, "sandbox_run_python")
        if fn and sandbox_id:
            return _completion(None, [_call(fn, {"sandbox_id": sandbox_id, "script": "main.py", "timeout_seconds": 30})])
    if step == 3:
        fn = _find_fn(tools, "a2a_delegate_task")
        if fn:
            return _completion(None, [_call(fn, {"agent_id": "analysis", "task": DELEGATE_TO_ANALYSIS})])
    run_out = _search_tool_results(messages, r'(A: n=5.*?SANDBOX_EXECUTION_OK)') \
        or _search_tool_results(messages, r'(SANDBOX_EXECUTION_OK)') or "（见工具原始输出）"
    analysis_out = _search_tool_results(messages, r'(\[Mock-Analysis\][^"]*)') or "（未获取到）"
    return _completion(
        "[Mock-Coding] Program executed in Docker sandbox.\n"
        f"Sandbox output: {run_out[:800]}\n"
        f"Analysis: {analysis_out[:1200]}"
    )


def _analysis_auto(messages: list[dict], tools: list[dict]) -> dict:
    step = len(_tool_contents(messages))
    if step == 0:
        fn = _find_fn(tools, "skills_load")
        if fn:
            return _completion(None, [_call(fn, {"name": "loss-analysis"})])
    if step == 1:
        fn = _find_fn(tools, "skills_loss_metrics")
        if fn:
            return _completion(None, [_call(fn, {
                "series_a": LOSS_A, "series_b": LOSS_B, "label_a": "A", "label_b": "B",
            })])
    metrics = _search_tool_results(messages, r'"total_decrease":\s*([0-9.]+)') or "?"
    return _completion(
        f"[Mock-Analysis] 已加载 loss-analysis 技能并完成确定性统计：A 组总降幅 {metrics}；"
        "B 组收敛更快、末值更低。该数据来自任务给定的示例序列，非真实实验日志。"
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> dict:
    body = await request.json()
    messages = body.get("messages", [])
    tools = body.get("tools") or []
    tool_choice = body.get("tool_choice")
    system = messages[0].get("content", "") if messages and messages[0].get("role") == "system" else ""
    role = _role_of(system)

    if role == "planner":
        goal = _goal_text(messages)
        cat = _parse_catalog(messages)
        result = _planner_response(goal, cat, tools)
        print(f"[mock-llm] role=planner goal={goal[:60]}... -> plan", flush=True)
        return result

    if tool_choice == "none" or not tools:
        return _completion(f"[Mock-{role}] 工具预算已用尽，基于已有工具结果结束（Mock LLM）。")

    # Dispatch: check WF mode first (Coordinator-dispatched node), then autonomous.
    if _has_marker(messages, "[WORKFLOW TASK]"):
        wf_handlers = {"research": _wf_research, "coding": _wf_coding, "analysis": _wf_analysis}
        handler = wf_handlers.get(role)
    else:
        handlers = {"research": _research_auto, "coding": _coding_auto, "analysis": _analysis_auto}
        handler = handlers.get(role)
    if handler is None:
        return _completion("[Mock] 未识别的角色，直接结束。")
    result = handler(messages, tools)
    wf = "WF" if _has_marker(messages, "[WORKFLOW TASK]") else ""
    print(f"[mock-llm] {wf}role={role} step={len(_tool_contents(messages))} -> "
          f"{'tool_call' if result['choices'][0]['message'].get('tool_calls') else 'final'}", flush=True)
    return result


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9100, log_level="warning")