"""LLM Platform v3 (v5.2) — token budgeting, adaptive routing, embedding cache.

Adds per-tenant token/cost budgets with quota enforcement, an adaptive router
that downgrades to a cheaper model as a tenant approaches its budget, an
embedding cache (single-flight), and provider health stats (error rate /
throughput) feeding automatic failover decisions. Builds on the v5.1
ModelRouter and CostTracker.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from llm_gateway.router import DEFAULT_MODEL, ModelRouter


@dataclass
class TenantBudget:
    tenant_id: str
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    used_tokens: int = 0
    used_cost: float = 0.0

    def would_exceed(self, tokens: int, cost: float) -> bool:
        if self.max_tokens is not None and self.used_tokens + tokens > self.max_tokens:
            return True
        if self.max_cost_usd is not None and self.used_cost + cost > self.max_cost_usd:
            return True
        return False

    def utilization(self) -> float:
        ratios = []
        if self.max_tokens:
            ratios.append(self.used_tokens / self.max_tokens)
        if self.max_cost_usd:
            ratios.append(self.used_cost / self.max_cost_usd)
        return round(max(ratios), 4) if ratios else 0.0


class BudgetExceeded(Exception):
    pass


class BudgetManager:
    def __init__(self):
        self._budgets: dict[str, TenantBudget] = {}
        self._lock = threading.Lock()

    def set_budget(self, tenant_id: str, *, max_tokens: int | None = None,
                   max_cost_usd: float | None = None) -> None:
        with self._lock:
            self._budgets[tenant_id] = TenantBudget(tenant_id, max_tokens, max_cost_usd)

    def _get(self, tenant_id: str) -> TenantBudget:
        return self._budgets.setdefault(tenant_id, TenantBudget(tenant_id))

    def check(self, tenant_id: str, tokens: int, cost: float) -> bool:
        with self._lock:
            return not self._get(tenant_id).would_exceed(tokens, cost)

    def consume(self, tenant_id: str, tokens: int, cost: float, *, enforce: bool = True) -> None:
        with self._lock:
            b = self._get(tenant_id)
            if enforce and b.would_exceed(tokens, cost):
                raise BudgetExceeded(
                    f"tenant {tenant_id} budget exceeded "
                    f"(util={b.utilization():.0%})")
            b.used_tokens += tokens
            b.used_cost += cost

    def utilization(self, tenant_id: str) -> float:
        with self._lock:
            return self._get(tenant_id).utilization()

    def status(self, tenant_id: str) -> dict:
        with self._lock:
            b = self._get(tenant_id)
            return {"tenant_id": tenant_id, "used_tokens": b.used_tokens,
                    "used_cost": round(b.used_cost, 6), "utilization": b.utilization(),
                    "max_tokens": b.max_tokens, "max_cost_usd": b.max_cost_usd}


class AdaptiveRouter:
    """Routes by task, but downgrades to a cheaper model as a tenant nears budget."""

    def __init__(self, router: ModelRouter | None = None, budgets: BudgetManager | None = None,
                 cheap_model: str = "claude-haiku-4-5-20251001", downgrade_at: float = 0.8):
        self.router = router or ModelRouter()
        self.budgets = budgets or BudgetManager()
        self.cheap_model = cheap_model
        self.downgrade_at = downgrade_at

    def route(self, task: str, tenant_id: str = "default", *, prefer: str | None = None) -> str:
        if self.budgets.utilization(tenant_id) >= self.downgrade_at:
            return self.cheap_model
        return self.router.route(task, prefer=prefer)


class EmbeddingCache:
    """Single-flight cache of text→embedding to avoid recomputation."""

    def __init__(self, embed_fn=None):
        self._store: dict[str, object] = {}
        self._embed = embed_fn or (lambda t: [float(len(w)) for w in t.split()])
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, text: str):
        key = text.strip().lower()
        with self._lock:
            if key in self._store:
                self.hits += 1
                return self._store[key]
            self.misses += 1
            value = self._embed(text)
            self._store[key] = value
            return value

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total else 0.0


@dataclass
class ProviderStats:
    """Per-provider error rate + throughput for failover/benchmarking decisions."""
    _calls: dict[str, list] = field(default_factory=dict)

    def record(self, provider: str, *, ok: bool, latency_ms: float) -> None:
        self._calls.setdefault(provider, []).append((ok, latency_ms, time.time()))

    def error_rate(self, provider: str) -> float:
        calls = self._calls.get(provider, [])
        if not calls:
            return 0.0
        errors = sum(1 for ok, _, _ in calls if not ok)
        return round(errors / len(calls), 4)

    def throughput(self, provider: str, window_s: float = 60.0) -> int:
        now = time.time()
        return sum(1 for _, _, ts in self._calls.get(provider, []) if now - ts <= window_s)

    def should_failover(self, provider: str, threshold: float = 0.5) -> bool:
        return self.error_rate(provider) >= threshold
