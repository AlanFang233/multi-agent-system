"""Phase 0 end-to-end smoke test driver.

Sends ONE task to the Research Agent over A2A (JSON-RPC, 127.0.0.1:8001) and
lets the real runtime drive the chain:
  research --A2A--> coding --(sandbox:create/write_file/run_python, real Docker)--> 
  coding --A2A--> analysis --(skills:load + skills:loss_metrics)--> back up.

The LLM is the local MOCK provider (scripts/mock_llm_server.py). Tool execution,
A2A transport, Docker sandbox, Skills and SQLite memory are all REAL.
This is an OFFLINE mock run and must NOT be reported as live-LLM evidence.
"""
import asyncio
import json
import uuid

import httpx

from agent_ecosystem.memory.store import MemoryDB

TASK = (
    "端到端协作验证任务：研究主题为『训练损失序列的可复现统计对比』。"
    "请完成：1) 委托 Coding Agent 在 Docker 沙箱中创建沙箱、写入 main.py 并真实运行，"
    "计算 A=[2.5,2.0,1.6,1.2,0.9] 与 B=[2.5,1.8,1.2,0.8,0.5] 两组损失序列的统计量，"
    "并验证沙箱内网络被禁用；2) 委托 Analysis Agent 使用 loss-analysis 技能与 "
    "skills:loss_metrics 工具对两组序列做确定性统计比较；3) Research Agent 整合各下游 Agent "
    "的真实执行结果，形成最终报告。"
)


async def send_a2a(url: str, prompt: str, timeout: float = 300.0):
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "ROLE_USER",
                "parts": [{"text": prompt}],
                "metadata": {},
            },
            "configuration": {"blocking": True, "acceptedOutputModes": ["text/plain"]},
        },
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=payload, headers={"A2A-Version": "1.0"})
        r.raise_for_status()
        data = r.json()
    if "error" in data:
        raise RuntimeError(f"A2A error: {data['error']}")
    result = data.get("result", {})
    task = result.get("task")
    state = task.get("status", {}).get("state") if task else None
    parts = []
    if task:
        parts = [p["text"] for art in task.get("artifacts", []) for p in art.get("parts", []) if "text" in p]
    else:
        msg = result.get("message", result)
        parts = [p["text"] for p in msg.get("parts", []) if "text" in p]
    return state, "\n".join(parts)


async def main() -> int:
    print("=== E2E: sending task to Research Agent (A2A http://127.0.0.1:8001) ===")
    state, answer = await send_a2a("http://127.0.0.1:8001", TASK)
    print("final task state:", state)
    print("=== FINAL ANSWER (research, bubbled up) ===")
    print(answer)
    print()

    db = MemoryDB("data/agent_memory.sqlite")
    stats = db.stats()
    print("=== MEMORY STATS AFTER E2E ===")
    print(json.dumps(stats, ensure_ascii=False, indent=2))

    print("=== PER-AGENT TASK TOOL TRACES ===")
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT agent_id, task_id, status, executed_json FROM tasks ORDER BY created_at"
        ).fetchall()
    for r in rows:
        print(f"[{r['agent_id']}] task={r['task_id'][:12]} status={r['status']} executed={r['executed_json']}")

    print("=== TOOL_RESULT EVENTS (proof tools really ran) ===")
    with db.connection() as conn:
        ev = conn.execute(
            "SELECT agent_id, event_type, payload_json FROM task_events "
            "WHERE event_type='tool_result' ORDER BY id"
        ).fetchall()
    for e in ev:
        p = json.loads(e["payload_json"])
        print(f"[{e['agent_id']}] {p.get('tool_id')} is_error={p.get('is_error')}")

    print("=== LONG-TERM MEMORIES (evidence-backed reflection) ===")
    for agent in ("research", "coding", "analysis"):
        mems = db.memories(agent, verified_only=False)
        for m in mems:
            print(f"[{agent}/{m['kind']}/{m['status']}] {m['content'][:80]}")

    ok = state in ("TASK_STATE_COMPLETED", "completed") and bool(answer)
    print("E2E_RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
