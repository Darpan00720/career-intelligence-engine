"""Semantic cache (v5.1).

Returns a cached LLM response for prompts that are *semantically* near a prior
prompt (not just byte-identical), cutting duplicate LLM spend. The embedder is
injectable: the default is a dependency-free bag-of-words cosine ("semantic-
lite"); inject a real embedding function (e.g. core.semantic_matcher) for
production-grade matching. The interface is unchanged either way.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _bow(text: str) -> dict[str, float]:
    counts: dict[str, float] = {}
    for tok in _TOKEN_RE.findall((text or "").lower()):
        counts[tok] = counts.get(tok, 0.0) + 1.0
    return counts


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class SemanticCache:
    threshold: float = 0.9
    embed: Callable[[str], dict[str, float]] = _bow
    _entries: list[tuple[dict, Any, str]] = field(default_factory=list)  # (vec, value, prompt)
    hits: int = 0
    misses: int = 0

    def put(self, prompt: str, value: Any) -> None:
        self._entries.append((self.embed(prompt), value, prompt))

    def get(self, prompt: str) -> Any | None:
        vec = self.embed(prompt)
        best_value, best_sim = None, 0.0
        for evec, value, _ in self._entries:
            sim = _cosine(vec, evec)
            if sim > best_sim:
                best_sim, best_value = sim, value
        if best_sim >= self.threshold:
            self.hits += 1
            return best_value
        self.misses += 1
        return None

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total else 0.0

    def stats(self) -> dict:
        return {"entries": len(self._entries), "hits": self.hits,
                "misses": self.misses, "hit_ratio": self.hit_ratio}
