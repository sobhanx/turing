from __future__ import annotations

"""
Local neural-style embedding provider (Phase 4.5.5).

No external API or heavy ML dependency. Implements a real embedding *model
interface* (named model + fixed dims + ``embed``) using a deterministic
feature → dense projection conditioned on ``model_name``.
"""

import hashlib
import math
import re
from typing import ClassVar

from django.conf import settings

from turing.search.embeddings.base import EmbeddingProvider

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

# Named local models (unknown names still embed using settings dims).
_MODEL_PROFILES: dict[str, dict[str, int]] = {
    "turing-local-v1": {"dimensions": 256, "hashes": 4},
    "turing-local-small": {"dimensions": 64, "hashes": 3},
    "turing-local-large": {"dimensions": 384, "hashes": 5},
}

DEFAULT_MODEL = "turing-local-v1"


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _features(text: str) -> dict[str, float]:
    """Sparse bag of tokens + character trigrams + bigrams."""
    feats: dict[str, float] = {}
    tokens = _tokenize(text)
    for i, tok in enumerate(tokens):
        feats[f"t:{tok}"] = feats.get(f"t:{tok}", 0.0) + 1.0
        feats[f"p:{i % 7}:{tok}"] = feats.get(f"p:{i % 7}:{tok}", 0.0) + 0.25
        padded = f"#{tok}#"
        for j in range(max(0, len(padded) - 2)):
            gram = padded[j : j + 3]
            key = f"g:{gram}"
            feats[key] = feats.get(key, 0.0) + 0.15
    for a, b in zip(tokens, tokens[1:]):
        key = f"b:{a}_{b}"
        feats[key] = feats.get(key, 0.0) + 0.5
    return feats


def _bucket(model_name: str, feature: str, hash_i: int, dims: int) -> int:
    digest = hashlib.sha256(
        f"{model_name}|{hash_i}|{feature}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:4], "big") % dims


class LocalNeuralEmbeddingProvider(EmbeddingProvider):
    """
    Deterministic local neural embedding interface.

    Projection is conditioned on ``model_name`` so different models occupy
    different vector spaces. No network calls; safe for tests and air-gapped
    hosts.
    """

    code: ClassVar[str] = "local"
    display_name: ClassVar[str] = "Local neural"

    def __init__(
        self,
        *,
        model_name: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        configured = (
            model_name
            if model_name is not None
            else getattr(settings, "TURING_EMBEDDING_MODEL", "") or DEFAULT_MODEL
        )
        self._model_name = (configured or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        profile = _MODEL_PROFILES.get(self._model_name, {})
        if dimensions is not None:
            self._dimensions = max(8, int(dimensions))
        elif self._model_name in _MODEL_PROFILES:
            self._dimensions = int(profile["dimensions"])
        else:
            self._dimensions = max(
                8,
                int(getattr(settings, "TURING_SEARCH_EMBEDDING_DIMS", 256) or 256),
            )
        self._num_hashes = int(profile.get("hashes") or 4)

    def model_name(self) -> str:
        return self._model_name

    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        dims = self._dimensions
        vec = [0.0] * dims
        feats = _features(text)
        if not feats:
            return vec

        # Unsigned multi-hash projection (avoids same-bucket cancellation).
        for feature, weight in feats.items():
            for h in range(self._num_hashes):
                idx = _bucket(self._model_name, feature, h, dims)
                vec[idx] += weight / self._num_hashes

        norm = math.sqrt(sum(v * v for v in vec))
        if norm <= 0:
            return vec
        return [v / norm for v in vec]
