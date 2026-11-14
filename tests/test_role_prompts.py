from agent_ecosystem.a2a_server import PROMPTS


def test_agent_roles_are_independent():
    assert set(PROMPTS) == {"research", "coding", "analysis"}
    assert len(set(PROMPTS.values())) == 3
