"""Trusted Coordinator workflows and templates, separate from worker permissions."""
from __future__ import annotations

from pathlib import Path

import yaml

from agent_ecosystem.agent_skill_registry import AgentSkillRegistry


class _NoWorkerTools:
    def list_for_agent(self, agent_id):
        return []


class CoordinatorSkills:
    def __init__(self, root: Path) -> None:
        config_path = root / "config/coordinator_skills.yaml"
        self.registry = AgentSkillRegistry(_NoWorkerTools(), config_path)
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.names = config["agents"]["coordinator"]
        if set(self.names) != {"write-skills", "register-tools"}:
            raise ValueError("Coordinator must have write-skills and register-tools")
        self.templates: dict[str, str] = {}
        for name, files in config.get("templates", {}).items():
            if name not in self.names or not isinstance(files, list):
                raise ValueError("Templates must belong to a Coordinator skill")
            folder = (self.registry.root / name / "assets").resolve(strict=True)
            if not folder.is_relative_to(self.registry.root):
                raise PermissionError("Template directory escapes the Skills root")
            for filename in files:
                if not isinstance(filename, str) or Path(filename).name != filename:
                    raise ValueError("Template names must be simple filenames")
                location = (folder / filename).resolve(strict=True)
                if not location.is_relative_to(folder):
                    raise PermissionError("Template path escapes the assets directory")
                content = location.read_text(encoding="utf-8")
                if not content.strip() or len(content) > 10000:
                    raise ValueError("Template must contain 1-10000 characters")
                self.templates[f"{name}/{filename}"] = content
        required = {
            "write-skills/agent-skill-template.md", "write-skills/team-spec-example.json",
            "register-tools/tool-registration-template.yaml", "register-tools/agent-role-template.md",
        }
        if not required <= self.templates.keys():
            raise ValueError("Coordinator template configuration is incomplete")

    async def context(self) -> dict:
        skills = []
        for name in self.names:
            result = await self.registry.call("coordinator", "skills:load", {"name": name})
            skills.append(result["structured_content"])
        return {"skills": skills, "templates": self.templates}

    def describe(self) -> dict:
        return {
            "agent_id": "coordinator",
            "skills": [
                {"name": name, "display_name": {
                    "write-skills": "Write_Skills", "register-tools": "Register Tools",
                }[name]} for name in self.names
            ],
            "templates": list(self.templates),
        }
