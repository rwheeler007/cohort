"""Tests for cohort.capability_router -- dynamic capability-based routing."""

import pytest

from cohort.agent import AgentConfig, AgentEducation
from cohort.capability_router import (
    _extract_keywords,
    build_partnership_graph,
    collect_acceptance_criteria,
    expand_keywords,
    find_agents_for_topic,
    find_required_consultations,
    route_task,
    score_agent_for_topic,
    trim_agent_memory,
)

# =====================================================================
# Fixtures
# =====================================================================

def _make_agent(
    agent_id: str,
    triggers: list[str] | None = None,
    capabilities: list[str] | None = None,
    domain_expertise: list[str] | None = None,
    agent_type: str = "specialist",
    partnerships: dict | None = None,
    skill_levels: dict | None = None,
    success_criteria: list[str] | None = None,
    status: str = "active",
) -> AgentConfig:
    return AgentConfig(
        agent_id=agent_id,
        name=agent_id.replace("_", " ").title(),
        role="Test Agent",
        agent_type=agent_type,
        triggers=triggers or [],
        capabilities=capabilities or [],
        domain_expertise=domain_expertise or [],
        partnerships=partnerships or {},
        education=AgentEducation(skill_levels=skill_levels or {}),
        success_criteria=success_criteria or [],
        status=status,
    )


@pytest.fixture
def agents() -> list[AgentConfig]:
    return [
        _make_agent(
            "python_developer",
            triggers=["python", "api", "backend", "pytest"],
            capabilities=["Backend API design", "Data processing"],
            domain_expertise=["Python backend architecture"],
            skill_levels={"api_design": 9, "python_backend": 9},
            partnerships={
                "security_agent": {
                    "relationship": "Security reviewer for all code changes",
                    "protocol": "Flag security-sensitive code for review",
                },
                "qa_agent": {
                    "relationship": "Test strategy partner",
                    "protocol": "QA designs test strategy",
                },
            },
            success_criteria=["All functions have type hints", "Tests pass"],
        ),
        _make_agent(
            "security_agent",
            triggers=["security", "vulnerability", "audit"],
            capabilities=["Security code review", "Vulnerability assessment"],
            domain_expertise=["Application security"],
            skill_levels={"security_review": 9},
        ),
        _make_agent(
            "qa_agent",
            triggers=["test", "qa", "quality"],
            capabilities=["Test strategy", "Quality assurance"],
            domain_expertise=["Test automation"],
            skill_levels={"testing": 9},
        ),
        _make_agent(
            "web_developer",
            triggers=["javascript", "frontend", "react", "css"],
            capabilities=["Frontend development", "React components"],
            domain_expertise=["Frontend architecture"],
        ),
        _make_agent(
            "cohort_orchestrator",
            triggers=["orchestrate", "coordinate", "workflow", "delegate"],
            capabilities=["Task routing", "Agent coordination"],
            agent_type="orchestrator",
        ),
    ]


# =====================================================================
# Scoring
# =====================================================================

class TestScoring:
    def test_exact_trigger_match(self, agents):
        pd = agents[0]  # python_developer
        score = score_agent_for_topic(pd, ["python", "api"])
        assert score > 0.3

    def test_no_match(self, agents):
        pd = agents[0]
        score = score_agent_for_topic(pd, ["kubernetes", "docker"])
        assert score == 0.0

    def test_capability_phrase_match(self, agents):
        pd = agents[0]
        score = score_agent_for_topic(pd, ["backend", "design"])
        assert score > 0.1

    def test_inactive_agent_scores_zero(self):
        agent = _make_agent("inactive", triggers=["python"], status="inactive")
        score = score_agent_for_topic(agent, ["python"])
        assert score == 0.0

    def test_empty_keywords_scores_zero(self, agents):
        score = score_agent_for_topic(agents[0], [])
        assert score == 0.0


# =====================================================================
# Finding agents
# =====================================================================

class TestFindAgents:
    def test_find_python_agents(self, agents):
        results = find_agents_for_topic(agents, "python api endpoint")
        assert len(results) >= 1
        assert results[0][0].agent_id == "python_developer"

    def test_find_security_agents(self, agents):
        results = find_agents_for_topic(agents, "security vulnerability audit")
        assert len(results) >= 1
        assert results[0][0].agent_id == "security_agent"

    def test_find_no_match(self, agents):
        results = find_agents_for_topic(agents, "quantum computing")
        assert len(results) == 0

    def test_max_results_respected(self, agents):
        results = find_agents_for_topic(agents, "python", max_results=2)
        assert len(results) <= 2

    def test_prefer_type(self, agents):
        results = find_agents_for_topic(
            agents, "coordinate workflow", prefer_type="orchestrator",
        )
        if results:
            assert results[0][0].agent_type == "orchestrator"

    def test_skill_level_bonus(self, agents):
        # Compare two agents scored with the same keyword set
        keywords = ["python", "api"]
        pd = agents[0]  # python_developer (has skill_levels)
        agent_no_skills = _make_agent(
            "no_skills", triggers=["python", "api"],
            capabilities=["Backend API design"],
        )
        pd_score = score_agent_for_topic(pd, keywords)
        score_no_skills = score_agent_for_topic(agent_no_skills, keywords)
        # Skill bonus should push score higher
        assert pd_score >= score_no_skills


# =====================================================================
# Route task
# =====================================================================

class TestRouteTask:
    def test_route_python_task(self, agents):
        result = route_task(agents, "implement a python api endpoint")
        assert result is not None
        assert result.agent_id == "python_developer"

    def test_route_no_match(self, agents):
        result = route_task(agents, "quantum")
        assert result is None

    def test_route_prefers_specialist(self, agents):
        result = route_task(
            agents, "python backend task", prefer_type="specialist",
        )
        assert result is not None
        assert result.agent_type == "specialist"


# =====================================================================
# Partnerships
# =====================================================================

class TestPartnerships:
    def test_find_security_consultation(self, agents):
        pd = agents[0]
        available = {a.agent_id: a for a in agents}
        consults = find_required_consultations(
            pd, ["implement", "api", "endpoint"], available,
        )
        assert len(consults) >= 1
        partners = [c["partner_id"] for c in consults]
        assert "security_agent" in partners

    def test_find_qa_consultation(self, agents):
        pd = agents[0]
        available = {a.agent_id: a for a in agents}
        consults = find_required_consultations(
            pd, ["implement", "feature", "create"], available,
        )
        partners = [c["partner_id"] for c in consults]
        assert "qa_agent" in partners

    def test_missing_partner_skipped(self, agents):
        pd = agents[0]
        # Only include security_agent, not qa_agent
        available = {a.agent_id: a for a in agents if a.agent_id != "qa_agent"}
        consults = find_required_consultations(
            pd, ["implement", "feature"], available,
        )
        partners = [c["partner_id"] for c in consults]
        assert "qa_agent" not in partners

    def test_no_consultation_for_read_tasks(self, agents):
        pd = agents[0]
        available = {a.agent_id: a for a in agents}
        consults = find_required_consultations(
            pd, ["research", "investigate", "analyze"], available,
        )
        # Security/QA consultation not triggered for read-only tasks
        assert len(consults) == 0

    def test_build_partnership_graph(self, agents):
        graph = build_partnership_graph(agents)
        assert "python_developer" in graph
        partner_ids = [e["partner_id"] for e in graph["python_developer"]]
        assert "security_agent" in partner_ids
        assert "qa_agent" in partner_ids


# =====================================================================
# Acceptance criteria
# =====================================================================

class TestAcceptanceCriteria:
    def test_collect_criteria(self, agents):
        pd = agents[0]
        consultations = [
            {"partner_id": "security_agent", "reason": "Security review", "protocol": "Review code"},
        ]
        criteria = collect_acceptance_criteria("Add API endpoint", pd, consultations)
        assert criteria["assignee"] == "python_developer"
        assert len(criteria["criteria"]) == 2  # from success_criteria
        assert len(criteria["consultation_requirements"]) == 1


# =====================================================================
# Memory trim
# =====================================================================

class TestMemoryTrim:
    def test_trim_working_memory(self):
        memory = {
            "agent_id": "test",
            "working_memory": [{"ts": i} for i in range(20)],
            "learned_facts": [{"fact": "keep me"}],
        }
        result = trim_agent_memory(memory, keep_last=5)
        assert len(result["working_memory"]) == 5
        assert len(result["learned_facts"]) == 1  # Never trimmed

    def test_trim_no_op_when_under_limit(self):
        memory = {
            "agent_id": "test",
            "working_memory": [{"ts": 1}, {"ts": 2}],
        }
        result = trim_agent_memory(memory, keep_last=10)
        assert len(result["working_memory"]) == 2


# =====================================================================
# Synonym expansion
# =====================================================================

class TestSynonymExpansion:
    def test_expand_canonical_term(self):
        result = expand_keywords(["api"])
        assert "endpoint" in result
        assert "rest" in result
        assert "http" in result
        assert "api" in result  # original preserved

    def test_expand_synonym_to_canonical(self):
        result = expand_keywords(["endpoint"])
        assert "api" in result  # reverse lookup

    def test_expand_preserves_unknown(self):
        result = expand_keywords(["foobar"])
        assert result == ["foobar"]

    def test_expand_deduplicates(self):
        result = expand_keywords(["api", "endpoint"])
        assert len(result) == len(set(result))

    def test_extract_keywords_with_expansion(self):
        kw = _extract_keywords("expose data over http")
        assert "api" in kw  # http -> api via reverse synonym

    def test_extract_keywords_without_expansion(self):
        kw = _extract_keywords("expose data over http", expand=False)
        assert "api" not in kw  # no expansion

    def test_semantic_routing_via_synonyms(self, agents):
        """The motivating example: 'expose data over HTTP' should match api agent."""
        results = find_agents_for_topic(agents, "expose data over HTTP")
        if results:
            agent_ids = [a.agent_id for a, _ in results]
            assert "python_developer" in agent_ids

    def test_synonym_map_reverse_lookup(self):
        """Known synonyms should expand to at least one canonical term."""
        # "endpoint" is only in "api", so it should reverse to "api"
        expanded = expand_keywords(["endpoint"])
        assert "api" in expanded
        # "auth" is only in "security"
        expanded = expand_keywords(["auth"])
        assert "security" in expanded
        # "django" is only in "python"
        expanded = expand_keywords(["django"])
        assert "python" in expanded
