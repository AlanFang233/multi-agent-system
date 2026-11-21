"""Capability-based assignment of DAG nodes to registered A2A agents.

Adapted from AgentForge@7cee900 src/agentforge/orchestration/assignment.py.
The agent catalog comes from the EXISTING config/agent_registry.yaml
(capabilities keys) — no second agent registry is introduced.

Policy (kept from AgentForge, validated by its test_assignment.py):
- a node is assigned only when one agent covers ALL required capabilities;
- deterministic tie-break: higher score, then smaller capability set (more
  specialized), then agent_id lexicographic;
- partial coverage is an explicit TaskAssignmentError, never a silent downgrade.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import TaskAssignmentError


def normalize_capability(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


@dataclass(frozen=True)
class AgentCapabilityCard:
    """Local projection of config/agent_registry.yaml (not the A2A AgentCard)."""

    agent_id: str
    description: str
    capabilities: tuple[str, ...]
    url: str = ""


@dataclass(frozen=True)
class AssignmentCandidate:
    card: AgentCapabilityCard
    matched: tuple[str, ...]
    score: float


def load_agent_cards(config_path: str | Path = "config/agent_registry.yaml") -> list[AgentCapabilityCard]:
    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    agents: dict[str, Any] = data.get("agents", {})
    cards: list[AgentCapabilityCard] = []
    for name, cfg in agents.items():
        caps = tuple(
            normalize_capability(c) for c in (cfg.get("capabilities") or []) if isinstance(c, str)
        )
        cards.append(
            AgentCapabilityCard(
                agent_id=name,
                description=str(cfg.get("description", name)),
                capabilities=caps,
                url=str(cfg.get("url", "")),
            )
        )
    if not cards:
        raise ValueError(f"no agents found in {config_path}")
    return cards


class CapabilityAssigner:
    def __init__(self, agents: list[AgentCapabilityCard], *, allow_empty: bool = False) -> None:
        if not agents and not allow_empty:
            raise ValueError("at least one agent card is required")
        self._agents = list(agents)

    @property
    def cards(self) -> list[AgentCapabilityCard]:
        return list(self._agents)

    def candidates(self, required_capabilities: list[str]) -> list[AssignmentCandidate]:
        required = {normalize_capability(c) for c in required_capabilities if c and c.strip()}
        if not required:
            raise TaskAssignmentError("task has no required capabilities")
        out: list[AssignmentCandidate] = []
        for card in self._agents:
            available = set(card.capabilities)
            matched = tuple(sorted(required & available))
            out.append(
                AssignmentCandidate(card=card, matched=matched, score=len(matched) / len(required))
            )
        out.sort(key=lambda c: (-c.score, len(c.card.capabilities), c.card.agent_id))
        return out

    def assign(self, required_capabilities: list[str]) -> AssignmentCandidate:
        """Require FULL coverage; refusing is better than mis-routing work."""
        if not self._agents:
            raise TaskAssignmentError("No worker agents are mounted; create and activate a group first")
        best = self.candidates(required_capabilities)[0]
        if best.score < 1.0:
            required = {normalize_capability(c) for c in required_capabilities}
            catalog = ", ".join(f"{c.agent_id}={list(c.capabilities)}" for c in self._agents)
            missing = sorted(required - set(best.card.capabilities))
            raise TaskAssignmentError(
                f"no registered agent covers capabilities {missing}; catalog: {catalog}"
            )
        return best
