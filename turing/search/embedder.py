from __future__ import annotations

"""
Local deterministic text → vector embedder (no LLM dependency).

Produces L2-normalized hashing-trick embeddings suitable for cosine ranking
and for storage in Embedding.vector (pgvector-compatible float arrays).
"""

import hashlib
import math
import re
from typing import Sequence

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def embed_text(text: str, *, dimensions: int = 256) -> list[float]:
    """
    Hashing-trick bag-of-tokens embedding.

    Deterministic and dependency-free — used by PgVectorSearchProvider until a
    host swaps in a model-backed embedder.

    Uses unsigned increments (no random signs) so token-hash collisions reinforce
    rather than cancel — important for short queries matching longer segments.
    """
    dims = max(8, int(dimensions))
    vec = [0.0] * dims
    tokens = tokenize(text)
    if not tokens:
        return vec

    for i, tok in enumerate(tokens):
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dims
        vec[idx] += 1.0
        # Soft positional / neighbor bucket for short phrases.
        vec[(idx + (i % 7) + 1) % dims] += 0.25
        # Character trigrams help short query ↔ longer document overlap.
        padded = f"#{tok}#"
        for j in range(len(padded) - 2):
            gram = padded[j : j + 3]
            gdigest = hashlib.sha256(gram.encode("utf-8")).digest()
            gidx = int.from_bytes(gdigest[:4], "big") % dims
            vec[gidx] += 0.15

    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0:
        return vec
    return [v / norm for v in vec]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        va = float(a[i])
        vb = float(b[i])
        dot += va * vb
        na += va * va
        nb += vb * vb
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / math.sqrt(na * nb)
