"""Dynamic DAG planner: LLM proposal + strict program-side validation.

validate_plan / extract_json_payload adapted from AgentForge@7cee900
(src/agentforge/orchestration/planner.py and agents/base.py). The LLM proposes
a plan; this module is the ONLY gate into the scheduler — invalid JSON, invalid
schema, cycles, unknown/self dependencies, oversized or too-deep plans and
plans the agent fleet cannot cover are all rejected here, never repaired
silently and never replaced by a hardcoded fallback plan.

Structured output adaptation (per PHASE1_API_AND_DATA_DESIGN §9):
- mode "function": force a submit_plan function call (providers with native
  function-calling; the project default via AsyncOpenAI);
- mode "json": response_format json_object;
- mode "text": plain content + bounded JSON extraction;
- mode "auto" (default): start with "function"; if the provider rejects the
  tools/response_format payload, downgrade ONCE (logged as capability
  detection), then keep using the working mode for subsequent attempts.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .assignment import AssignmentCandidate, CapabilityAssigner
from .errors import PlanValidationError, PlanningError, TaskAssignmentError
from .models import ExecutionPlan, SubtaskSpec

PLANNER_PROTOCOL = """You are the planning module of the Coordinator.
Decompose the user goal into a DAG of independent subtasks for a team of
specialist agents reachable over A2A. Rules:
1. Reply with exactly one JSON object: {"goal_summary": str, "subtasks": [...]}
2. Each subtask: {"key", "description", "required_capabilities", "depends_on",
   "priority", "kind"} — key unique; required_capabilities MUST come from the
   agent catalog below; depends_on lists subtask keys (no cycles, no unknown
   keys, no self-reference); priority 0..9 (higher runs first among ready nodes).
3. Split by real dependency and specialty, not by a fixed template: the number
   of subtasks and the graph shape must follow the goal. Independent work must
   be independent nodes (they run in parallel).
4. Do NOT add synthesis/summary/review tasks; the platform appends its own
   synthesis node. Do NOT assign agents; capability coverage is checked by code.
5. Each description must be a self-contained instruction for the executing
   agent (include data handed over by dependencies' results).
"""


def build_planner_messages(
    goal: str, catalog: list[AssignmentCandidate] | list[Any], max_subtasks: int,
    max_depth: int,
) -> list[dict[str, Any]]:
    """Catalog items are AgentCapabilityCard-like; render one compact line each."""
    lines = []
    for item in catalog:
        card = getattr(item, "card", item)
        lines.append(
            f"- {card.agent_id}: {card.description} "
            f"(capabilities={','.join(card.capabilities)})"
        )
    user = (
        f"[GOAL]\n{goal}\n\n"
        f"[AGENT CATALOG]\n" + "\n".join(lines) + "\n\n"
        f"[LIMITS] max_subtasks={max_subtasks}, max_dependency_depth={max_depth}\n"
        "Return the plan JSON object only."
    )
    return [
        {"role": "system", "content": PLANNER_PROTOCOL},
        {"role": "user", "content": user},
    ]


PLAN_FUNCTION_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_plan",
        "description": "Submit the decomposed execution plan as one JSON object.",
        "parameters": {
            "type": "object",
            "properties": {
                "goal_summary": {"type": "string", "maxLength": 2000},
                "subtasks": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string", "minLength": 1, "maxLength": 80},
                            "description": {"type": "string", "minLength": 1},
                            "required_capabilities": {
                                "type": "array", "minItems": 1, "items": {"type": "string"}
                            },
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                            "priority": {"type": "integer", "minimum": 0, "maximum": 9},
                            "kind": {"type": "string"},
                        },
                        "required": ["key", "description", "required_capabilities"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["subtasks"],
            "additionalProperties": False,
        },
    },
}


def extract_json_payload(raw: str) -> Any:
    """Bounded 3-level JSON extraction (AgentForge agents/base.py)."""
    text = (raw or "").strip()
    if not text:
        raise PlanValidationError("empty planner response")
    candidates: list[str] = []
    if text.startswith("```"):
        fenced = text.strip("`")
        if fenced.lower().startswith("json"):
            fenced = fenced[4:]
        candidates.append(fenced.strip())
    candidates.append(text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    errors: list[str] = []
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    raise PlanValidationError(
        f"planner response is not valid JSON ({'; '.join(errors[:2])}): {text[:300]}"
    )


def validate_plan(
    plan: ExecutionPlan, *, max_subtasks: int, max_depth: int = 6
) -> list[SubtaskSpec]:
    """Kahn topological sort + structural checks. Returns ordered subtasks.

    Adapted from AgentForge orchestration/planner.py (semantics preserved),
    extended with a longest-dependency-chain depth cap.
    """
    if not plan.subtasks:
        raise PlanValidationError("execution plan contains no subtasks")
    if len(plan.subtasks) > max_subtasks:
        raise PlanValidationError(
            f"execution plan has {len(plan.subtasks)} subtasks, exceeding max {max_subtasks}"
        )
    by_key = {s.key: s for s in plan.subtasks}
    for subtask in plan.subtasks:
        if subtask.key in subtask.depends_on:
            raise PlanValidationError(f"subtask {subtask.key!r} depends on itself")
        unknown = [d for d in subtask.depends_on if d not in by_key]
        if unknown:
            raise PlanValidationError(
                f"subtask {subtask.key!r} has unknown dependencies: {sorted(unknown)}"
            )
    # Kahn with deterministic tie-break on original order.
    order_index = {s.key: i for i, s in enumerate(plan.subtasks)}
    indegree = {s.key: len(set(s.depends_on)) for s in plan.subtasks}
    dependents: dict[str, list[str]] = {s.key: [] for s in plan.subtasks}
    for s in plan.subtasks:
        for dep in set(s.depends_on):
            dependents[dep].append(s.key)
    ready = sorted([k for k, d in indegree.items() if d == 0], key=lambda k: order_index[k])
    ordered: list[SubtaskSpec] = []
    depth_of: dict[str, int] = {}
    while ready:
        key = ready.pop(0)
        spec = by_key[key]
        ordered.append(spec)
        depth_of[key] = 1 + max((depth_of[d] for d in set(spec.depends_on)), default=0)
        if depth_of[key] > max_depth:
            raise PlanValidationError(
                f"execution plan dependency depth {depth_of[key]} exceeds max {max_depth}"
            )
        for dependent in dependents[key]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
        ready.sort(key=lambda k: order_index[k])
    if len(ordered) != len(plan.subtasks):
        cyclic = sorted(k for k, d in indegree.items() if d > 0)
        raise PlanValidationError(
            f"execution plan contains a dependency cycle involving {cyclic}"
        )
    return ordered


def check_capability_coverage(
    ordered: list[SubtaskSpec], assigner: CapabilityAssigner
) -> list[AssignmentCandidate]:
    """Assign EVERY subtask before any node exists (fail-fast, zero residue)."""
    assignments: list[AssignmentCandidate] = []
    for spec in ordered:
        assignments.append(assigner.assign(spec.required_capabilities))
    return assignments


@dataclass
class PlanningAttempt:
    raw: str = ""
    error: str | None = None
    output_mode: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_preview": self.raw[:600],
            "error": self.error,
            "output_mode": self.output_mode,
            "duration_ms": round((self.finished_at - self.started_at) * 1000, 1),
        }


@dataclass
class PlanningResult:
    plan: ExecutionPlan
    ordered_subtasks: list[SubtaskSpec]
    assignments: list[AssignmentCandidate]
    attempts: list[dict[str, Any]] = field(default_factory=list)
    output_mode: str = "function"


class Planner:
    """LLM-backed dynamic planner with bounded repair and capability detection."""

    def __init__(
        self,
        client: Any,
        model: str,
        *,
        output_mode: str = "auto",
        max_repairs: int = 1,
        max_subtasks: int = 8,
        max_depth: int = 6,
    ) -> None:
        if output_mode not in {"auto", "function", "json", "text"}:
            raise ValueError(f"unknown planner output_mode {output_mode!r}")
        self.client = client
        self.model = model
        self.output_mode = output_mode
        self._resolved_mode = "function" if output_mode in ("auto", "function") else output_mode
        self.max_repairs = max(0, max_repairs)
        self.max_subtasks = max_subtasks
        self.max_depth = max_depth

    async def plan_goal(
        self, goal: str, catalog: list[Any], assigner: CapabilityAssigner
    ) -> PlanningResult:
        messages = build_planner_messages(goal, catalog, self.max_subtasks, self.max_depth)
        attempts: list[PlanningAttempt] = []
        repairs_used = 0
        downgrades_used = 0
        last_error = "planner did not run"
        while True:
            attempt = PlanningAttempt(started_at=time.monotonic(), output_mode=self._resolved_mode)
            finish_reason: str | None = None
            plan: ExecutionPlan | None = None
            ordered: list[SubtaskSpec] = []
            assignments: list[AssignmentCandidate] = []
            validated = False
            try:
                raw, finish_reason = await self._call_llm(messages)
                attempt.raw = raw
                if finish_reason != "length":
                    payload = extract_json_payload(raw)
                    plan = ExecutionPlan.model_validate(payload)
                    ordered = validate_plan(
                        plan, max_subtasks=self.max_subtasks, max_depth=self.max_depth
                    )
                    assignments = check_capability_coverage(ordered, assigner)
                    validated = True
            except ValidationError as exc:
                last_error = f"plan failed schema validation: {_condense(str(exc))}"
            except PlanValidationError as exc:
                last_error = str(exc)
            except TaskAssignmentError as exc:
                # Capability gaps: fail fast ONLY when a required capability is
                # absent from the whole catalog (no repair can invent an agent).
                # When every capability exists but no SINGLE agent covers one
                # subtask, the planner can split it — spend one bounded repair
                # with explicit feedback (real-LLM Mid_Test Task 2 failure mode:
                # ['statistics','coding'] merged into one subtask).
                from .assignment import normalize_capability
                catalog_caps: set[str] = set()
                for card in getattr(assigner, "cards", []):
                    catalog_caps.update(card.capabilities)
                required_all = {normalize_capability(c)
                                for st in ordered for c in st.required_capabilities}
                if required_all - catalog_caps:
                    attempt.error = str(exc)
                    attempts.append(self._finish(attempt))
                    raise PlanningError(
                        f"plan rejected: {exc}", attempts=[a.to_dict() for a in attempts]
                    ) from exc
                last_error = (
                    f"capability coverage rejected the plan: {exc} Every required "
                    "capability exists in the catalog, but no single agent fully "
                    "covers at least one subtask. Re-split that subtask into pieces "
                    "that one agent can each fully cover, or adjust "
                    "required_capabilities to match a single agent."
                )
            except _ProviderCapabilityError as exc:
                attempt.error = f"provider capability: {exc}"
                attempts.append(self._finish(attempt))
                downgrades_used += 1
                if downgrades_used > 2:
                    raise PlanningError(
                        f"provider does not support structured planner output: {exc}",
                        attempts=[a.to_dict() for a in attempts],
                    ) from exc
                last_error = str(exc)
                continue  # downgrade does not consume the repair budget
            except Exception as exc:  # provider/transport errors: explicit, no fallback plan
                attempt.error = f"{type(exc).__name__}: {exc}"
                attempts.append(self._finish(attempt))
                raise PlanningError(
                    f"planner LLM call failed: {type(exc).__name__}: {exc}",
                    attempts=[a.to_dict() for a in attempts],
                ) from exc

            if validated and plan is not None and finish_reason != "length":
                attempts.append(self._finish(attempt))
                return PlanningResult(
                    plan=plan,
                    ordered_subtasks=ordered,
                    assignments=assignments,
                    attempts=[a.to_dict() for a in attempts],
                    output_mode=self._resolved_mode,
                )

            # Validation failure or truncation: consume one bounded repair.
            if finish_reason == "length":
                last_error = "response truncated (finish_reason=length)"
            attempt.error = last_error
            attempts.append(self._finish(attempt))
            repairs_used += 1
            if repairs_used > self.max_repairs:
                raise PlanningError(
                    f"planner failed after {len(attempts)} attempt(s): {last_error}",
                    attempts=[a.to_dict() for a in attempts],
                )
            if finish_reason == "length":
                messages = self._retry_message(messages)
            else:
                messages = self._repair_message(messages, last_error, attempt.raw)

    # -- internals ---------------------------------------------------------

    async def _call_llm(self, messages: list[dict[str, Any]]) -> tuple[str, str | None]:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        mode = self._resolved_mode
        if mode == "function":
            kwargs["tools"] = [PLAN_FUNCTION_SPEC]
            kwargs["tool_choice"] = {"type": "function", "function": {"name": "submit_plan"}}
        elif mode == "json":
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = await self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if (
                mode in ("function", "json")
                and self.output_mode == "auto"
                and _is_capability_error(exc)
            ):
                self._resolved_mode = "json" if mode == "function" else "text"
                raise _ProviderCapabilityError(f"{type(exc).__name__}: {exc}") from exc
            raise
        message = response.choices[0].message
        finish_reason = getattr(response.choices[0], "finish_reason", None)
        tool_calls = getattr(message, "tool_calls", None) or []
        if self._resolved_mode == "function" and tool_calls:
            return tool_calls[0].function.arguments or "", finish_reason
        content = message.content or ""
        if not content and tool_calls:
            return tool_calls[0].function.arguments or "", finish_reason
        return content, finish_reason

    @staticmethod
    def _finish(attempt: PlanningAttempt) -> PlanningAttempt:
        attempt.finished_at = time.monotonic()
        return attempt

    @staticmethod
    def _repair_message(
        messages: list[dict[str, Any]], error: str, raw: str | None
    ) -> list[dict[str, Any]]:
        repaired = list(messages)
        if raw:
            repaired.append({"role": "assistant", "content": raw[:600]})
        repaired.append({
            "role": "user",
            "content": (
                f"[REPAIR] Your previous plan was invalid: {error[:600]}\n"
                "Return ONLY the corrected JSON plan object, same schema."
            ),
        })
        return repaired

    @staticmethod
    def _retry_message(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # finish_reason=length: do NOT echo the truncated content back (keeps the
        # prompt small; lesson from AgentForge HKGAI incidents).
        return list(messages) + [{
            "role": "user",
            "content": (
                "[RETRY] Your previous response was truncated. Return ONLY the complete "
                "JSON plan object, concise descriptions, same schema and limits."
            ),
        }]


def _condense(text: str) -> str:
    return " ".join(str(text).split())[:600]


class _ProviderCapabilityError(RuntimeError):
    """Provider rejected the structured-output payload shape (tools/response_format)."""


_CAPABILITY_MARKERS = (
    "response_format",
    "tool_choice",
    "tools",
    "unsupported",
    "not supported",
    "invalid_request_error",
)


def _is_capability_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    status = getattr(getattr(exc, "response", None), "status_code", None) \
        or getattr(exc, "status_code", None)
    if status == 400 and any(m in text for m in _CAPABILITY_MARKERS):
        return True
    return any(m in text for m in ("unsupported", "not supported", "response_format", "tool_choice"))
