"""Role-specific Agent Skills overlay for the existing MCP+A2A registry.

- Uses SKILL.md metadata/instructions, no arbitrary scripts are executed.
- Exposes skills:load to the matching agent only.
- Demonstrates a deterministic, analysis-only skill:loss_metrics capability.
- Delegation and MCP permissions remain enforced by the underlying registry.
"""
from __future__ import annotations

import math
import re
import statistics
from pathlib import Path
from typing import Any

import yaml
from jsonschema import validate

NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class AgentSkillRegistry:
    LOAD = "skills:load"
    LOSS_METRICS = "skills:loss_metrics"

    def __init__(
        self,
        delegate_registry: Any,
        config_path: str | Path = "config/agent_skills.yaml",
    ) -> None:
        self._delegate = delegate_registry
        config_file = Path(config_path).resolve(strict=True)
        config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        if not isinstance(config, dict):
            raise ValueError("Skill configuration must be a YAML mapping")
        folder = config.get("root", "skills")
        self.root = (config_file.parent.parent / folder).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"Skills directory not found: {self.root}")
        assigned = config.get("agents", {})
        if not isinstance(assigned, dict):
            raise ValueError("'agents' must be a mapping")
        self._skills: dict[str, dict[str, dict[str, str]]] = {}
        for role, names in assigned.items():
            if not isinstance(role, str) or not NAME_PATTERN.fullmatch(role):
                raise ValueError(f"Invalid role: {role!r}")
            if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                raise ValueError(f"Invalid skill list for {role}")
            if len(set(names)) != len(names):
                raise ValueError(f"Repeated skill for {role}")
            self._skills[role] = {}
            for name in names:
                if not NAME_PATTERN.fullmatch(name):
                    raise ValueError(f"Invalid skill name: {name!r}")
                self._skills[role][name] = self._read_skill(role, name)

    def _read_skill(self, role: str, name: str) -> dict[str, str]:
        allowed_root = (self.root / role).resolve()
        location = (allowed_root / name / "SKILL.md").resolve(strict=True)
        if not location.is_relative_to(allowed_root):
            raise PermissionError("Skill path escapes the assigned role directory")
        raw = location.read_text(encoding="utf-8")
        if len(raw) > 10000:
            raise ValueError(f"Skill too long (maximum 10000 chars): {name}")
        lines = raw.splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError(f"SKILL.md missing YAML frontmatter: {name}")
        try:
            end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        except StopIteration as exc:
            raise ValueError(f"SKILL.md frontmatter not closed: {name}") from exc
        meta = yaml.safe_load("\n".join(lines[1:end]))
        if not isinstance(meta, dict) or meta.get("name") != name:
            raise ValueError(f"SKILL.md name must match directory name: {name}")
        description = meta.get("description")
        if not isinstance(description, str) or not (1 <= len(description) <= 1024):
            raise ValueError(f"SKILL.md invalid description: {name}")
        instructions = "\n".join(lines[end + 1:]).strip()
        if not instructions:
            raise ValueError(f"Empty skill instructions: {name}")
        return {"name": name, "description": description, "instructions": instructions}

    async def refresh(self) -> dict[str, str]:
        return await self._delegate.refresh()

    def list_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        tools = list(self._delegate.list_for_agent(agent_id))
        available = self._skills.get(agent_id, {})
        if available:
            descriptions = "; ".join(
                f"{name}: {item['description']}" for name, item in available.items()
            )
            tools.append({
                "tool_id": self.LOAD,
                "description": (
                    "当需要特定专业流程时，加载当前 Agent 专属 Skill 的完整执行规范；"
                    "应按技能方法执行，但不能越过工具权限或安全限制。"
                    f"当前可加载：{descriptions}"
                )[:1024],
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "enum": sorted(available)}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            })
        if agent_id == "analysis" and "loss-analysis" in available:
            tools.append({
                "tool_id": self.LOSS_METRICS,
                "description": (
                    "Analysis 专属确定性统计函数：对两组真实提取或明确标记为示例的 loss 数据计算"
                    "均值、样本标准差、单调性、总降幅及逐 epoch 差分。"
                    "先确认数据来源；输入必须是数值数组。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "series_a": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 200},
                        "series_b": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 200},
                        "label_a": {"type": "string", "maxLength": 50},
                        "label_b": {"type": "string", "maxLength": 50},
                    },
                    "required": ["series_a", "series_b"],
                    "additionalProperties": False,
                },
            })
        return tools

    async def call(
        self, agent_id: str, tool_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if tool_id not in {self.LOAD, self.LOSS_METRICS}:
            return await self._delegate.call(agent_id=agent_id, tool_id=tool_id, arguments=arguments)

        allowed = {item["tool_id"]: item for item in self.list_for_agent(agent_id)}
        if tool_id not in allowed:
            raise PermissionError(f"Agent {agent_id!r} cannot use {tool_id!r}")
        validate(instance=arguments, schema=allowed[tool_id]["input_schema"])
        if tool_id == self.LOAD:
            name = arguments["name"]
            skill = self._skills[agent_id][name]
            print(f"[Skill 已加载] {agent_id} -> {name}", flush=True)
            result: dict[str, Any] = dict(skill)
        else:
            result = self._loss_metrics(arguments)
            print(f"[Skill 已执行] {agent_id} -> loss-metrics", flush=True)
        return {
            "tool_id": tool_id,
            "is_error": False,
            "structured_content": result,
            "content": [],
        }

    @staticmethod
    def _loss_metrics(arguments: dict[str, Any]) -> dict[str, Any]:
        def summarize(raw: list[float]) -> dict[str, Any]:
            vals = [float(v) for v in raw]
            if any(not math.isfinite(v) for v in vals):
                raise ValueError("All loss values must be finite")
            decreases = [a - b for a, b in zip(vals, vals[1:])]
            total = vals[0] - vals[-1]
            return {
                "count": len(vals),
                "first": vals[0],
                "last": vals[-1],
                "mean": statistics.mean(vals),
                "sample_stdev": statistics.stdev(vals),
                "min": min(vals),
                "max": max(vals),
                "total_decrease": total,
                "total_decrease_percent": (total / vals[0] * 100) if vals[0] else None,
                "average_decrease_per_interval": statistics.mean(decreases),
                "decrease_per_interval": decreases,
                "decrease_sample_stdev": statistics.stdev(decreases) if len(decreases) > 1 else None,
                "strictly_decreasing": all(x > 0 for x in decreases),
                "nonincreasing": all(x >= 0 for x in decreases),
            }

        a = [float(v) for v in arguments["series_a"]]
        b = [float(v) for v in arguments["series_b"]]
        sa = summarize(a)
        sb = summarize(b)
        return {
            "data_source_warning": "These statistics only validate the provided arrays, not whether notebook outputs are genuine.",
            "series_a": {"label": arguments.get("label_a", "A"), **sa},
            "series_b": {"label": arguments.get("label_b", "B"), **sb},
            "comparison": {
                "last_b_minus_a": sb["last"] - sa["last"],
                "total_decrease_b_over_a": sb["total_decrease"] / sa["total_decrease"] if sa["total_decrease"] else None,
                "mean_b_minus_a": sb["mean"] - sa["mean"],
                "b_lower_than_a_count": sum(y < x for x, y in zip(a, b)) if len(a) == len(b) else None,
            },
        }
