"""Capability assignment: full coverage, deterministic tie-break, real registry."""
import pytest

from agent_ecosystem.orchestration.assignment import (
    AgentCapabilityCard,
    CapabilityAssigner,
    load_agent_cards,
    normalize_capability,
)
from agent_ecosystem.orchestration.errors import TaskAssignmentError
from orchestration_support import TEST_CARDS, make_assigner


def test_routes_by_capability_to_unique_agent():
    assigner = make_assigner()
    assert assigner.assign(["web_search"]).card.agent_id == "research"
    assert assigner.assign(["sandbox_execution"]).card.agent_id == "coding"
    assert assigner.assign(["statistics"]).card.agent_id == "analysis"


def test_capability_normalization_case_and_hyphen():
    assigner = make_assigner()
    assert assigner.assign(["WEB_SEARCH"]).card.agent_id == "research"
    cards = [AgentCapabilityCard("x", "x", (normalize_capability("Evidence-Synthesis"),), "")]
    assert CapabilityAssigner(cards).assign(["evidence_synthesis"]).card.agent_id == "x"


def test_no_single_agent_covering_all_is_rejected():
    assigner = make_assigner()
    with pytest.raises(TaskAssignmentError, match="no registered agent covers"):
        assigner.assign(["web_search", "sandbox_execution"])


def test_empty_capabilities_rejected():
    with pytest.raises(TaskAssignmentError, match="no required capabilities"):
        make_assigner().assign([])


def test_deterministic_tie_break_prefers_specialist_then_id():
    general = AgentCapabilityCard("zeta", "z", ("web_search", "statistics"), "")
    specialist = AgentCapabilityCard("alpha", "a", ("web_search",), "")
    assigner = CapabilityAssigner([general, specialist])
    # both score 1.0; smaller capability set wins (more specialized)
    assert assigner.assign(["web_search"]).card.agent_id == "alpha"
    twin_a = AgentCapabilityCard("aaa", "a", ("web_search",), "")
    twin_b = AgentCapabilityCard("bbb", "b", ("web_search",), "")
    assert CapabilityAssigner([twin_b, twin_a]).assign(["web_search"]).card.agent_id == "aaa"


def test_candidates_expose_partial_scores():
    cands = make_assigner().candidates(["web_search", "statistics"])
    assert cands[0].score == 0.5
    assert len(cands) == len(TEST_CARDS)


def test_empty_catalog_rejected():
    with pytest.raises(ValueError, match="at least one agent"):
        CapabilityAssigner([])


def test_load_real_agent_registry_covers_three_roles():
    cards = load_agent_cards("config/agent_registry.yaml")
    ids = {c.agent_id for c in cards}
    assert ids == {"research", "coding", "analysis"}
    assigner = CapabilityAssigner(cards)
    # the governance synthesis capability must be assignable in the real registry
    assert assigner.assign(["evidence_synthesis"]).card.agent_id == "research"
    for card in cards:
        assert card.url.startswith("http://127.0.0.1:")
