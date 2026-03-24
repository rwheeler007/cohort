"""Tests for cohort.embeddings -- semantic matching via embeddings."""

import pytest

from cohort.agent import AgentConfig, AgentEducation
from cohort.capability_router import score_agent_for_topic
from cohort.embeddings import EmbeddingCache, _cosine_similarity


# =====================================================================
# Cosine similarity math
# =====================================================================

class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert _cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert _cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_empty_vectors(self):
        assert _cosine_similarity([], []) == 0.0

    def test_mismatched_lengths(self):
        assert _cosine_similarity([1, 0], [1, 0, 0]) == 0.0

    def test_zero_vector(self):
        assert _cosine_similarity([0, 0], [1, 1]) == 0.0


# =====================================================================
# EmbeddingCache without client
# =====================================================================

class TestEmbeddingCacheNoClient:
    def test_not_available(self):
        cache = EmbeddingCache(ollama_client=None)
        assert cache.available is False

    def test_embed_returns_none(self):
        cache = EmbeddingCache(ollama_client=None)
        assert cache.embed("test") is None

    def test_precompute_returns_zero(self):
        cache = EmbeddingCache(ollama_client=None)
        assert cache.precompute_agent_embeddings([]) == 0

    def test_semantic_score_returns_none(self):
        cache = EmbeddingCache(ollama_client=None)
        agent = AgentConfig(
            agent_id="test",
            name="Test",
            role="Test",
            triggers=["python"],
        )
        assert cache.semantic_score("python api", agent) is None


# =====================================================================
# EmbeddingCache with mock client
# =====================================================================

class _MockOllama:
    """Fake Ollama client that returns deterministic embeddings."""

    def embed(self, model: str, input_text: str) -> list[float] | None:
        # Simple hash-based embedding for deterministic tests
        words = input_text.lower().split()
        # Create a 4-dimensional embedding based on word presence
        dims = ["python", "api", "security", "frontend"]
        return [1.0 if d in words else 0.0 for d in dims]


def _make_agent(agent_id: str, **kwargs) -> AgentConfig:
    return AgentConfig(
        agent_id=agent_id,
        name=agent_id.replace("_", " ").title(),
        role=kwargs.pop("role", "Test Agent"),
        triggers=kwargs.pop("triggers", []),
        capabilities=kwargs.pop("capabilities", []),
        domain_expertise=kwargs.pop("domain_expertise", []),
        education=AgentEducation(skill_levels=kwargs.pop("skill_levels", {})),
        **kwargs,
    )


class TestEmbeddingCacheWithMock:
    def test_available(self):
        cache = EmbeddingCache(ollama_client=_MockOllama())
        assert cache.available is True

    def test_embed(self):
        cache = EmbeddingCache(ollama_client=_MockOllama())
        vec = cache.embed("python api")
        assert vec is not None
        assert len(vec) == 4
        assert vec[0] == 1.0  # "python" present
        assert vec[1] == 1.0  # "api" present

    def test_semantic_score(self):
        cache = EmbeddingCache(ollama_client=_MockOllama())
        agent = _make_agent("py_dev", role="python api developer")
        score = cache.semantic_score("python api", agent)
        assert score is not None
        assert score > 0.5  # should be similar

    def test_semantic_score_dissimilar(self):
        cache = EmbeddingCache(ollama_client=_MockOllama())
        agent = _make_agent("sec", role="security audit")
        score = cache.semantic_score("frontend", agent)
        assert score is not None
        # security and frontend are orthogonal in our mock
        assert score < 0.8

    def test_precompute(self):
        cache = EmbeddingCache(ollama_client=_MockOllama())
        agents = [
            _make_agent("a", role="python"),
            _make_agent("b", role="security"),
        ]
        count = cache.precompute_agent_embeddings(agents)
        assert count == 2
        assert "a" in cache._agent_embeddings
        assert "b" in cache._agent_embeddings

    def test_query_caching(self):
        cache = EmbeddingCache(ollama_client=_MockOllama())
        vec1 = cache.embed("python api")
        vec2 = cache.embed("python api")
        assert vec1 == vec2  # should be cached


# =====================================================================
# Integration with score_agent_for_topic
# =====================================================================

class TestScoringWithEmbeddings:
    def test_without_embeddings_unchanged(self):
        agent = _make_agent("dev", triggers=["python", "api"])
        score_without = score_agent_for_topic(agent, ["python", "api"])
        score_with_none = score_agent_for_topic(
            agent, ["python", "api"], embedding_cache=None
        )
        assert score_without == score_with_none

    def test_with_embeddings_uses_hybrid_weights(self):
        cache = EmbeddingCache(ollama_client=_MockOllama())
        agent = _make_agent("dev", triggers=["python", "api"], role="python api")
        score = score_agent_for_topic(
            agent, ["python", "api"], embedding_cache=cache
        )
        assert score > 0  # should produce a valid score
