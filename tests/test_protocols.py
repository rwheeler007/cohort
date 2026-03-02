"""Test that Protocol duck typing works with runtime_checkable."""

from cohort.registry import AgentProfile


class SimpleAgent:
    """Duck-typed agent -- no explicit inheritance from AgentProfile."""

    def __init__(self, name: str, role: str, capabilities: list[str]) -> None:
        self.name = name
        self.role = role
        self.capabilities = capabilities

    def relevance_score(self, topic: str) -> float:
        keywords = set(topic.lower().split())
        matches = sum(1 for c in self.capabilities if c.lower() in keywords)
        return min(1.0, matches / max(len(self.capabilities), 1))

    def can_contribute(self, context: dict) -> bool:
        return bool(self.capabilities)


class MinimalAgent:
    """Bare-minimum attributes, no extra methods."""

    name = "minimal"
    role = "test"
    capabilities = ["testing"]

    def relevance_score(self, topic: str) -> float:
        return 0.5

    def can_contribute(self, context: dict) -> bool:
        return True


class NotAnAgent:
    """Missing required attributes -- should NOT satisfy AgentProfile."""

    name = "broken"
    # missing: role, capabilities, relevance_score, can_contribute


def test_duck_typed_agent_satisfies_protocol():
    agent = SimpleAgent("architect", "developer", ["python", "api"])
    assert isinstance(agent, AgentProfile)


def test_minimal_agent_satisfies_protocol():
    agent = MinimalAgent()
    assert isinstance(agent, AgentProfile)


def test_class_level_isinstance():
    assert isinstance(MinimalAgent(), AgentProfile)


def test_incomplete_class_does_not_satisfy():
    broken = NotAnAgent()
    assert not isinstance(broken, AgentProfile)


def test_relevance_score_returns_float():
    agent = SimpleAgent("tester", "tester", ["testing", "qa"])
    score = agent.relevance_score("testing strategy")
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_can_contribute_returns_bool():
    agent = SimpleAgent("designer", "designer", ["ui", "ux"])
    assert agent.can_contribute({}) is True
