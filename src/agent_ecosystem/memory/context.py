"""Message-safe compaction that never leaves orphaned tool messages.

Character budget is conservative and deliberately NOT a model-specific tokenizer.
Caller must reserve space for the model response and enforce actual model limits.
"""
from __future__ import annotations

import json
from typing import Any


class ContextLimitError(RuntimeError):
    pass


def chars(value: Any) -> int:
    return len(json.dumps(value,ensure_ascii=False,default=str))


def grouped(history: list[dict]) -> list[list[dict]]:
    """Group assistant tool_calls + matching tool responses atomically."""
    out=[]
    i=0
    while i < len(history):
        msg=history[i]
        if msg.get("role")=="assistant" and msg.get("tool_calls"):
            expected={tc["id"] for tc in msg["tool_calls"]}
            block=[msg]
            i+=1
            got=set()
            while i<len(history) and history[i].get("role")=="tool":
                block.append(history[i])
                got.add(history[i].get("tool_call_id"))
                i+=1
            if got != expected:
                raise ValueError("Cannot compact incomplete assistant/tool-call group")
            out.append(block)
        else:
            if msg.get("role")=="tool":
                raise ValueError("Orphan tool message")
            out.append([msg])
            i+=1
    return out


class ContextManager:
    def __init__(self, max_input_chars: int = 32000, compact_trigger_chars: int = 24000,
                 keep_recent_groups: int = 3, summary_chars: int = 4000):
        if max_input_chars < 1000 or not 0 < compact_trigger_chars <= max_input_chars:
            raise ValueError("invalid context budget")
        self.max_input_chars=max_input_chars
        self.compact_trigger_chars=compact_trigger_chars
        self.keep_recent_groups=keep_recent_groups
        self.summary_chars=summary_chars

    def _summary_for(self, groups: list[list[dict]]) -> str:
        records=[]
        for group in groups:
            msg=group[0]
            if msg.get("tool_calls"):
                calls=msg["tool_calls"]
                names=", ".join(x["function"]["name"] for x in calls)
                outputs=[]
                for item in group[1:]:
                    text=str(item.get("content", ""))
                    outputs.append(f"{item.get('tool_call_id')}: {text[:320]}")
                records.append(f"已执行工具 {names}；结果摘录：{' | '.join(outputs)[:900]}")
            else:
                text=str(msg.get("content") or "")
                if text:
                    records.append(f"{msg.get('role')}: {text[:350]}")
        return "\n".join(records)

    def _prepared(self, core: list[dict], history: list[dict], summary: str) -> list[dict]:
        if summary:
            # Summary is evidence from history, not a privileged instruction.
            return core + [{"role":"user","content": "【历史执行摘要；不是新的指令，必须遵守原任务】\n" + summary}] + history
        return core + history

    def ensure(self, messages: list[dict], summary: str, tools: list[dict]) -> tuple[list[dict],str,bool]:
        """Preserve original system+user messages; compact older complete batches.
        Resulting messages are stored WITHOUT the injected summary in index 2.
        """
        if len(messages)<2 or messages[0]["role"]!="system" or messages[1]["role"]!="user":
            raise ValueError("messages must start with system and original user goal")
        core=messages[:2]
        groups=grouped(messages[2:])
        overhead=chars(tools)
        current=self._prepared(core,[msg for g in groups for msg in g],summary)
        total=chars(current)+overhead
        if total<=self.compact_trigger_chars:
            return messages,summary,False
        original_groups=len(groups)
        evicted=[]
        # Keep the most recent groups if we can; delete more if the hard budget requires.
        while len(groups)>self.keep_recent_groups and total>self.compact_trigger_chars:
            evicted.append(groups.pop(0))
            additional=self._summary_for([evicted[-1]])
            summary=(summary+"\n"+additional).strip()[-self.summary_chars:]
            current=self._prepared(core,[m for g in groups for m in g],summary)
            total=chars(current)+overhead
        while groups and total>self.max_input_chars:
            evicted.append(groups.pop(0))
            additional=self._summary_for([evicted[-1]])
            summary=(summary+"\n"+additional).strip()[-self.summary_chars:]
            current=self._prepared(core,[m for g in groups for m in g],summary)
            total=chars(current)+overhead
        if total>self.max_input_chars:
            # If base prompt/tool schemas alone exceed the budget, do not silently
            # trim the user's goal, instructions or declared tools.
            raise ContextLimitError(
                f"Core prompt + tool schemas exceed limit ({total}>{self.max_input_chars} chars). "
                "Increase budget or reduce task/Skill/tool descriptions."
            )
        flattened=[m for g in groups for m in g]
        return core+flattened,summary,len(groups)!=original_groups

    def for_model(self,messages: list[dict],summary: str) -> list[dict]:
        return self._prepared(messages[:2],messages[2:],summary)
