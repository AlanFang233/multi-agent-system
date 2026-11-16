"""Offline mock OpenAI-compatible LLM server for end-to-end smoke tests.

Scripts a deterministic tool-call flow per agent role so the full chain
A2A + MCP + Docker Sandbox + Skills + SQLite Memory can be verified WITHOUT
any paid/real LLM API. Results produced with this provider are MOCK results
and must never be reported as live-LLM evidence.

Start:  ./.venv/bin/python scripts/mock_llm_server.py
Listens on 127.0.0.1:9100; point OPENAI_BASE_URL=http://127.0.0.1:9100/v1
and OPENAI_MODEL=mock-gpt in .env.

Scripted flows
- research: a2a:discover_agents -> a2a:delegate_task(coding) -> final report
- coding:   sandbox:create -> filesystem_code:write_file(main.py)
            -> sandbox:run_python -> a2a:delegate_task(analysis) -> final report
- analysis: skills:load(loss-analysis) -> skills:loss_metrics -> final report
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


def _role_of(system_text: str) -> str:
    if "Research Agent" in system_text:
        return "research"
    if "Coding Agent" in system_text:
        return "coding"
    if "Analysis Agent" in system_text:
        return "analysis"
    return "unknown"


def _find_fn(tools: list[dict], suffix: str) -> str | None:
    """Match the sanitized function name that ends with the tool id suffix."""
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
    """Extract (workspace, sandbox_id) from a sandbox:create result.

    The sandbox MCP returns its dict as a TEXT content block, so inside the tool
    message the JSON is escaped (\\\"workspace\\\": \\\"...\\\"). Match the path itself,
    which contains no quotes/backslashes, and derive the id from its last segment.
    """
    ws = _search_tool_results(
        messages, r'([/A-Za-z0-9_.\-]*mcp_workspace/sandboxes/[0-9a-f]{32})'
    )
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


def _next_for_research(messages: list[dict], tools: list[dict]) -> dict:
    step = len(_tool_contents(messages))
    if step == 0:
        fn = _find_fn(tools, "a2a_discover_agents")
        if fn:
            return _completion(None, [_call(fn, {})])
    if step <= 1:
        fn = _find_fn(tools, "a2a_delegate_task")
        if fn:
            return _completion(None, [_call(fn, {"agent_id": "coding", "task": DELEGATE_TO_CODING})])
    coding_result = _search_tool_results(messages, r'"agent_id":\s*"coding".*?"result":\s*"(.*?)"\s*}') \
        or _search_tool_results(messages, r'(\[Mock-Coding\][^"]*)') or "（未获取到下游结果原文）"
    return _completion(
        "[Mock-Research] 端到端任务完成（Mock LLM 编排，工具执行与下游 Agent 结果均为真实产生）。\n"
        "1) 资料整理：本次为离线 Mock 验证，未执行真实网络检索；研究主题（训练损失对比与沙箱验证）"
        "已转化为可执行的子任务。\n"
        f"2) Coding Agent 实测结果：{coding_result[:1500]}\n"
        "3) 结论：A2A 委托、Docker 沙箱执行与 Analysis 统计链路均已打通。"
    )


def _next_for_coding(messages: list[dict], tools: list[dict]) -> dict:
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
    analysis_out = _search_tool_results(messages, r'(\[Mock-Analysis\][^"]*)') or "（未获取到分析结果）"
    return _completion(
        "[Mock-Coding] 程序已在 Docker 沙箱中真实执行完毕。\n"
        f"沙箱输出摘录：{run_out[:800]}\n"
        f"Analysis Agent 统计报告：{analysis_out[:1200]}"
    )


def _next_for_analysis(messages: list[dict], tools: list[dict]) -> dict:
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
        f"[Mock-Analysis] 已加载 loss-analysis 技能并用 skills:loss_metrics 完成确定性统计："
        f"A 组总降幅 {metrics}；两组均值/标准差/单调性见工具原始输出。"
        "B 组收敛更快、末值更低。数据来源为任务给定的示例序列，非真实实验日志。"
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> dict:
    body = await request.json()
    messages = body.get("messages", [])
    tools = body.get("tools") or []
    tool_choice = body.get("tool_choice")
    system = messages[0].get("content", "") if messages and messages[0].get("role") == "system" else ""
    role = _role_of(system)

    if tool_choice == "none" or not tools:
        return _completion(f"[Mock-{role}] 工具预算已用尽，基于已有工具结果结束（Mock LLM）。")

    handler = {"research": _next_for_research, "coding": _next_for_coding, "analysis": _next_for_analysis}.get(role)
    if handler is None:
        return _completion("[Mock] 未识别的角色，直接结束。")
    result = handler(messages, tools)
    print(f"[mock-llm] role={role} step={len(_tool_contents(messages))} -> "
          f"{'tool_call' if result['choices'][0]['message'].get('tool_calls') else 'final'}", flush=True)
    return result


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9100, log_level="warning")
