"""Dynamic model routing + provider benchmarking (v5.1).

Routes a task to a model by policy (e.g. cheap vs. high-quality), with an
ordered provider failover list, and records per-model benchmarks (latency, cost,
quality) to recommend the best model over time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

# Task → preferred model. Override per tenant/experiment.
DEFAULT_POLICY = {
    "research":      "claude-sonnet-4-6",
    "documents":     "claude-sonnet-4-6",
    "summarize":     "claude-haiku-4-5-20251001",
    "classify":      "claude-haiku-4-5-20251001",
    "strategy":      "claude-opus-4-8",
}
DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass
class ModelRouter:
    policy: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_POLICY))
    provider_failover: list[str] = field(default_factory=lambda: ["anthropic", "openai", "echo"])
    _benchmarks: dict[str, dict[str, list[float]]] = field(default_factory=dict)

    def route(self, task: str, *, prefer: str | None = None) -> str:
        if prefer:
            return prefer
        return self.policy.get(task, DEFAULT_MODEL)

    def record_benchmark(self, model: str, *, latency_ms: float, cost_usd: float,
                         quality: float | None = None) -> None:
        b = self._benchmarks.setdefault(model, {"latency": [], "cost": [], "quality": []})
        b["latency"].append(latency_ms)
        b["cost"].append(cost_usd)
        if quality is not None:
            b["quality"].append(quality)

    def benchmarks(self) -> dict[str, dict]:
        out = {}
        for model, b in self._benchmarks.items():
            out[model] = {
                "avg_latency_ms": round(mean(b["latency"]), 2) if b["latency"] else 0.0,
                "avg_cost_usd": round(mean(b["cost"]), 6) if b["cost"] else 0.0,
                "avg_quality": round(mean(b["quality"]), 3) if b["quality"] else None,
                "samples": len(b["latency"]),
            }
        return out

    def best_model(self, metric: str = "quality") -> str | None:
        bench = self.benchmarks()
        if not bench:
            return None
        if metric == "cost":
            return min(bench, key=lambda m: bench[m]["avg_cost_usd"])
        if metric == "latency":
            return min(bench, key=lambda m: bench[m]["avg_latency_ms"])
        scored = {m: v["avg_quality"] for m, v in bench.items() if v["avg_quality"] is not None}
        return max(scored, key=scored.get) if scored else None


_router: ModelRouter | None = None


def get_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router


def reset_router() -> None:
    global _router
    _router = None
