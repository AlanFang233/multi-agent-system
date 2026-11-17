"""HashingEmbeddingProvider — deterministic feature-hash embedding.

Adapted from AgentForge@7cee900 src/agentforge/memory/embeddings.py.

This is a deterministic, offline embedding provider.  It is NOT a learned
semantic model — texts with the same tokens produce the same vector, but
different wording of the same meaning will NOT be close.  Its purpose is to
provide a reproducible baseline for hybrid retrieval; a real semantic model
can be swapped in later via the EmbeddingProvider interface.

The hash is l2-normalised so cosine similarity is simply the dot product.
"""
from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")


class EmbeddingProvider(ABC):
    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @abstractmethod
    def embed(self, text: str) -> list[float]: ...

    @property
    def name(self) -> str:
        return self.__class__.__name__


class HashingEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dimension: int = 256) -> None:
        if dimension < 16:
            raise ValueError("dimension must be >= 16")
        self._dim = dimension

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return f"HashingEmbedding({self._dim}d)"

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        tokens = _TOKEN_RE.findall(text.lower()) if text else []
        for token in tokens:
            self._add_token(vec, token, weight=1.0)
            # character 3-grams carry sub-word similarity signal
            for i in range(len(token) - 2):
                self._add_token(vec, token[i:i + 3], weight=0.35)
        return _l2_normalize(vec)

    def _add_token(self, vec: list[float], token: str, weight: float) -> None:
        h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(h[:8], "big")
        idx = value % self._dim
        sign = 1.0 if (value >> 63) & 1 == 0 else -1.0
        vec[idx] += sign * weight


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a or not b:
        return 0.0
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)) / (na * nb)))


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return list(vec)
    return [x / norm for x in vec]