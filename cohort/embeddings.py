"""Embedding-based semantic matching for cohort agent routing.

Pre-computes embeddings for agent capability descriptions and provides
cosine similarity scoring against query text.  Falls back gracefully
when Ollama or the embedding model is unavailable.

Zero pip dependencies -- uses :class:`~cohort.local.ollama.OllamaClient`.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from cohort.agent import AgentConfig

logger = logging.getLogger(__name__)

# Default embedding model -- Ollama must have this pulled
DEFAULT_EMBED_MODEL = "nomic-embed-text"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors.  Returns 0.0 on error."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _agent_text(agent: AgentConfig) -> str:
    """Build the combined text representation of an agent for embedding."""
    parts = []
    if agent.role:
        parts.append(agent.role)
    parts.extend(agent.triggers)
    parts.extend(agent.capabilities)
    parts.extend(agent.domain_expertise)
    return " ".join(parts)


class EmbeddingCache:
    """Cache of agent embeddings for semantic routing.

    Parameters
    ----------
    ollama_client:
        An :class:`~cohort.local.ollama.OllamaClient` instance.
        If *None*, all operations return None/0.0 (graceful fallback).
    model:
        Ollama embedding model name.
    """

    def __init__(
        self,
        ollama_client: Any | None = None,
        model: str = DEFAULT_EMBED_MODEL,
    ) -> None:
        self._client = ollama_client
        self._model = model
        self._agent_embeddings: dict[str, list[float]] = {}
        self._query_cache: dict[str, list[float]] = {}

    @property
    def available(self) -> bool:
        """Whether the embedding backend is usable."""
        return self._client is not None

    def embed(self, text: str) -> list[float] | None:
        """Embed text via Ollama.  Returns None if unavailable."""
        if not self._client:
            return None
        # Check query cache
        if text in self._query_cache:
            return self._query_cache[text]
        result = self._client.embed(self._model, text)
        if result is not None:
            self._query_cache[text] = result
        return result

    def precompute_agent_embeddings(self, agents: list[AgentConfig]) -> int:
        """Pre-compute and cache embeddings for all agents.

        Returns the number of agents successfully embedded.
        """
        if not self._client:
            return 0
        count = 0
        for agent in agents:
            if agent.agent_id in self._agent_embeddings:
                continue
            text = _agent_text(agent)
            if not text.strip():
                continue
            vec = self._client.embed(self._model, text)
            if vec is not None:
                self._agent_embeddings[agent.agent_id] = vec
                count += 1
        logger.info("Pre-computed embeddings for %d agents", count)
        return count

    def semantic_score(self, query_text: str, agent: AgentConfig) -> float | None:
        """Cosine similarity between query and agent's cached embedding.

        Returns None if embeddings are unavailable for either side.
        Returns a float in [0.0, 1.0] on success.
        """
        if not self._client:
            return None

        # Get agent embedding (from cache or compute)
        agent_vec = self._agent_embeddings.get(agent.agent_id)
        if agent_vec is None:
            text = _agent_text(agent)
            agent_vec = self._client.embed(self._model, text)
            if agent_vec is not None:
                self._agent_embeddings[agent.agent_id] = agent_vec
            else:
                return None

        # Get query embedding
        query_vec = self.embed(query_text)
        if query_vec is None:
            return None

        sim = _cosine_similarity(query_vec, agent_vec)
        # Normalize from [-1, 1] to [0, 1]
        return max(0.0, min(1.0, (sim + 1.0) / 2.0))
