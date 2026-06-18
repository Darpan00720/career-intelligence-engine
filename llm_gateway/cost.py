"""LLM cost tracking (v5).

Per-request cost is computed from a model pricing table ($ per 1M tokens) and
persisted to the llm_costs table with tenant / workflow / agent attribution.
Aggregations power the cost-intelligence reports.
"""
from __future__ import annotations

from core import database

# USD per 1M tokens (input, output). Approximate list prices; override via config.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8":      (15.0, 75.0),
    "claude-sonnet-4-6":    (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    "echo":                 (0.0, 0.0),
}
_DEFAULT_PRICE = (3.0, 15.0)


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    in_price, out_price = PRICING.get(model, _DEFAULT_PRICE)
    cost = (prompt_tokens / 1_000_000) * in_price + (completion_tokens / 1_000_000) * out_price
    return round(cost, 6)


class CostTracker:
    """Persists per-request LLM cost and exposes attribution aggregates."""

    def record(self, *, request_id: str, tenant_id: str, workflow_id: str | None,
               agent: str | None, provider: str, model: str,
               prompt_tokens: int, completion_tokens: int,
               latency_ms: float, cached: bool) -> float:
        cost = 0.0 if cached else estimate_cost(model, prompt_tokens, completion_tokens)
        with database.get_connection() as conn:
            conn.execute(
                """INSERT INTO llm_costs
                   (request_id, tenant_id, workflow_id, agent, provider, model,
                    prompt_tokens, completion_tokens, cost_usd, latency_ms, cached)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (request_id, tenant_id, workflow_id, agent, provider, model,
                 prompt_tokens, completion_tokens, cost, latency_ms, int(cached)),
            )
        return cost

    # ── Aggregations ────────────────────────────────────────────────────────────
    @staticmethod
    def _sum_by(column: str) -> dict[str, dict]:
        with database.get_connection() as conn:
            rows = conn.execute(
                f"""SELECT {column} AS k,
                           SUM(cost_usd) AS cost,
                           SUM(prompt_tokens + completion_tokens) AS tokens,
                           SUM(cached) AS cached_calls,
                           COUNT(*) AS calls
                    FROM llm_costs GROUP BY {column}"""
            ).fetchall()
        return {(r["k"] or "unknown"): {
            "cost_usd": round(r["cost"] or 0.0, 6),
            "tokens": r["tokens"] or 0,
            "calls": r["calls"],
            "cached_calls": r["cached_calls"] or 0,
        } for r in rows}

    def by_tenant(self) -> dict[str, dict]:
        return self._sum_by("tenant_id")

    def by_workflow(self) -> dict[str, dict]:
        return self._sum_by("workflow_id")

    def by_agent(self) -> dict[str, dict]:
        return self._sum_by("agent")

    def by_model(self) -> dict[str, dict]:
        return self._sum_by("model")

    def total(self) -> dict:
        with database.get_connection() as conn:
            row = conn.execute(
                """SELECT SUM(cost_usd) AS cost,
                          SUM(prompt_tokens + completion_tokens) AS tokens,
                          SUM(cached) AS cached_calls, COUNT(*) AS calls
                   FROM llm_costs"""
            ).fetchone()
        return {
            "cost_usd": round(row["cost"] or 0.0, 6),
            "tokens": row["tokens"] or 0,
            "calls": row["calls"] or 0,
            "cached_calls": row["cached_calls"] or 0,
        }
