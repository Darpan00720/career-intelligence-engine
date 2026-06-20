"""Writable research node (v6 convergence) — first cost-incurring external node.

Graph-native: operates on ``state["scored_jobs"]`` (NOT a DB threshold query), so
it does not depend on the PERSIST_SCORES flag. For each job above the score
threshold it performs company research (a paid Claude call) and persists the
result via the existing ``company_research`` store.

Idempotency mechanism for this side-effect class = **TTL staleness**
(``research_is_stale``): a replay/retry only researches rows whose research is
missing or past the TTL, so an interrupted/re-run graph never repeats a fresh
(expensive) Claude call or writes a duplicate research row. Cost-gating
(no ANTHROPIC_API_KEY -> skip), rate limiting, and per-job partial-failure
isolation are reused from ``agents.research_agent``.
"""
from __future__ import annotations

import os

from core.logging_config import get_logger
from graph.providers import get_provider
from schemas.control import Phase

logger = get_logger(__name__)


def _threshold() -> int:
    return int(os.getenv("RESEARCH_MIN_SCORE", "80"))


def research_node(state: dict) -> dict:
    """Research companies for high-scoring jobs; persist idempotently (TTL-guarded).

    Returns a partial state update (``phase`` + ``research_stats`` + ``audit_log``).
    The Claude calls + persistence are deliberate side effects, skipped for rows
    whose research is still fresh.
    """
    from agents.research_agent import (
        _CLAUDE_DELAY_SECONDS,
        _build_message,
        _call_claude,
        _persist,
        research_is_stale,
    )
    from graph.nodes import _enter

    _enter("research")
    run_id = state.get("run_id")
    provider = get_provider(run_id)
    threshold = _threshold()

    candidates = [sj for sj in (state.get("scored_jobs") or [])
                  if (sj.total_score or 0) >= threshold]
    stats = {"eligible": len(candidates), "researched": 0,
             "skipped_fresh": 0, "errors": 0, "reason": ""}

    if not candidates:
        stats["reason"] = "no jobs above threshold"
        return {"phase": Phase.RESEARCH, "research_stats": stats, "audit_log": ["research"]}

    # Cost gate: research is a paid call. No key -> skip cleanly (legacy parity).
    if not os.environ.get("ANTHROPIC_API_KEY"):
        stats["reason"] = "ANTHROPIC_API_KEY not set — research skipped"
        return {"phase": Phase.RESEARCH, "research_stats": stats,
                "audit_log": ["research(skipped:no-key)"]}

    # Replay guard: only research stale/missing rows; count fresh hits as skips.
    stale: list[dict] = []
    for sj in candidates:
        if research_is_stale(sj.job_id):
            job = provider.get(sj.job_id)
            if job:
                stale.append(job)
        else:
            stats["skipped_fresh"] += 1

    def _one(job: dict) -> bool:
        _persist(job, _call_claude(_build_message(job)))
        return True

    from core.concurrency import parallel_map
    results = parallel_map(_one, stale, max_workers=1, rate_limit=_CLAUDE_DELAY_SECONDS)
    for job, res in zip(stale, results):
        if res.ok:
            stats["researched"] += 1
        else:
            stats["errors"] += 1
            logger.warning("research: job %s failed: %s", job.get("id"), res.error)

    logger.info("research: researched=%d skipped_fresh=%d errors=%d",
                stats["researched"], stats["skipped_fresh"], stats["errors"])
    return {"phase": Phase.RESEARCH, "research_stats": stats, "audit_log": ["research"]}
