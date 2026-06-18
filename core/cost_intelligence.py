"""Cost Intelligence (v5).

Reports and optimization recommendations built on the llm_costs table populated
by the LLM gateway. Tracks cost per user/tenant/workflow/agent, cache savings,
and token usage; generates daily/monthly reports and actionable suggestions.
"""
from __future__ import annotations

from core import database
from llm_gateway.cost import CostTracker, estimate_cost


def _by_period(period_expr: str) -> list[dict]:
    with database.get_connection() as conn:
        rows = conn.execute(
            f"""SELECT {period_expr} AS period,
                       SUM(cost_usd) AS cost,
                       SUM(prompt_tokens + completion_tokens) AS tokens,
                       COUNT(*) AS calls,
                       SUM(cached) AS cached_calls
                FROM llm_costs GROUP BY period ORDER BY period DESC"""
        ).fetchall()
    return [{"period": r["period"], "cost_usd": round(r["cost"] or 0.0, 6),
             "tokens": r["tokens"] or 0, "calls": r["calls"],
             "cached_calls": r["cached_calls"] or 0} for r in rows]


def daily_cost_report() -> list[dict]:
    return _by_period("DATE(created_at)")


def monthly_cost_report() -> list[dict]:
    return _by_period("STRFTIME('%Y-%m', created_at)")


def cache_savings() -> dict:
    """Estimate the cost avoided by cached LLM responses."""
    with database.get_connection() as conn:
        rows = conn.execute(
            "SELECT model, prompt_tokens, completion_tokens FROM llm_costs WHERE cached = 1"
        ).fetchall()
    saved = sum(estimate_cost(r["model"], r["prompt_tokens"], r["completion_tokens"]) for r in rows)
    return {"cached_calls": len(rows), "estimated_savings_usd": round(saved, 6)}


def cost_breakdown() -> dict:
    tracker = CostTracker()
    return {
        "total": tracker.total(),
        "by_tenant": tracker.by_tenant(),
        "by_workflow": tracker.by_workflow(),
        "by_agent": tracker.by_agent(),
        "by_model": tracker.by_model(),
        "cache_savings": cache_savings(),
    }


def optimization_recommendations() -> list[str]:
    """Heuristic suggestions to reduce LLM spend."""
    recs: list[str] = []
    tracker = CostTracker()
    total = tracker.total()
    by_model = tracker.by_model()
    savings = cache_savings()

    if total["calls"]:
        cache_rate = total["cached_calls"] / total["calls"]
        if cache_rate < 0.2:
            recs.append(
                f"Low cache hit rate ({cache_rate:.0%}). Increase LLM cache TTL or "
                "enable request dedup for repeated prompts.")
    # Flag the most expensive model if it dominates spend.
    if by_model:
        top_model, top = max(by_model.items(), key=lambda kv: kv[1]["cost_usd"])
        if total["cost_usd"] and top["cost_usd"] / total["cost_usd"] > 0.6:
            recs.append(
                f"{top_model} drives {top['cost_usd'] / total['cost_usd']:.0%} of spend; "
                "consider routing simpler prompts to a cheaper model.")
    if savings["estimated_savings_usd"] > 0:
        recs.append(
            f"Caching already saved ~${savings['estimated_savings_usd']:.4f}; "
            "warming the cache before peak workflows can save more.")
    if not recs:
        recs.append("No cost optimizations detected — spend looks efficient.")
    return recs


def full_report() -> dict:
    return {
        "daily": daily_cost_report(),
        "monthly": monthly_cost_report(),
        "breakdown": cost_breakdown(),
        "recommendations": optimization_recommendations(),
    }
