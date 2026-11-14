"""Evidence-backed, rule-based lessons: no unverified LLM claim is auto-approved."""
from __future__ import annotations

from collections import Counter
from typing import Any

from .store import MemoryDB


def reflect(db: MemoryDB, agent_id: str, task_id: str) -> list[str]:
    events=db.events(agent_id,task_id)
    emitted=[]
    error_ids=set()
    successes=set()
    for item in events:
        if item["event_type"]!="tool_result":
            continue
        p=item["payload"]
        text=str(p.get("preview", "")).lower()
        tool=p.get("tool_id", "")
        if p.get("is_error") is not True:
            successes.add(tool)
            continue
        if tool.startswith("filesystem:") and (
            "access denied" in text or "outside allowed directories" in text
        ):
            error_ids.add("filesystem_permission")
        if tool=="fetch:fetch" and ("file:///" in text or "robots.txt file:" in text):
            error_ids.add("fetch_file_scheme")
    if "filesystem_permission" in error_ids:
        emitted.append(db.put_memory(
            agent_id,"procedural",
            "Filesystem MCP 曾返回访问越权错误。今后遇到 Access denied 应停止重复访问被禁止的路径，改用有授权的工具或专业 Agent；最新权限以当前 Registry 配置为准。",
            task_id,status="verified"))
    if "fetch_file_scheme" in error_ids:
        emitted.append(db.put_memory(
            agent_id,"procedural",
            "Fetch MCP 曾无法读取 file:// 本地文件。应使用获授权的 Filesystem/Notebook 工具读取本地文件，不要把 file:// 当作网页 URL。",
            task_id,status="verified"))
    # Tool success certifies a useful route, NOT correctness of all output claims.
    known_routes={
      "git:git_status": "Git 状态检查可优先使用只读 git:git_status 工具；必须使用实际 Git 仓库路径。工具输出只代表检查当时的状态。",
      "skills:loss_metrics": "训练损失数值的基础统计可使用确定性 skills:loss_metrics 工具；输入来源与真实实验运行情况需要另行验证。",
      "a2a:delegate_task": "任务需要其他专业能力时，已成功使用 a2a:delegate_task 调用下游 Agent；TASK_STATE_COMPLETED 不等于报告所有事实均已验证。",
    }
    for tool,lesson in known_routes.items():
        if tool in successes:
            emitted.append(db.put_memory(agent_id,"procedural",lesson,task_id,status="verified"))
    # Merely observing a successful tool request is not evidence of factual correctness.
    # Candidate memories require manual promotion before retrieval.
    ids=[(x["payload"].get("tool_id"),x["payload"].get("arguments_key"))
         for x in events if x["event_type"]=="tool_result"]
    counts=Counter(ids)
    if any(n>=3 for (tool,key),n in counts.items() if tool and key and not tool.startswith("memory:")):
        emitted.append(db.put_memory(
            agent_id,"procedural",
            "本次任务同一工具和相同参数至少调用了三次。建议复核结果缓存及重复调用停止条件，避免无收益的反复探索。",
            task_id,status="pending"))
    return emitted
