"""Side-effect feature flags + score persistence (v6 convergence).

The graph is read-only by default. Write-capable stages (job acquisition, score
persistence) are **opt-in** via environment flags so the default topology — and
the existing test suite — are byte-for-byte unchanged, and a normal `analyze`
run never performs unexpected network or write I/O.

  ENABLE_ACQUISITION=1   -> the graph runs `acquire_jobs` before `job_ingestion`
  PERSIST_SCORES=1       -> `scoring` upserts each ScoredJob into the scores table

Both writes are idempotent: acquisition dedups via dedup_hash/unique-url
(agents.search_agent._process_job), and score persistence uses the existing
``database.insert_score`` upsert (``ON CONFLICT(job_id) DO UPDATE``), so a
checkpoint replay or node retry overwrites rather than duplicates.
"""
from __future__ import annotations

import json
import os

from core.logging_config import get_logger

logger = get_logger(__name__)


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def acquisition_enabled() -> bool:
    return _flag("ENABLE_ACQUISITION")


def score_persistence_enabled() -> bool:
    return _flag("PERSIST_SCORES")


def research_enabled() -> bool:
    return _flag("ENABLE_RESEARCH")


def documents_enabled() -> bool:
    return _flag("ENABLE_DOCUMENTS")


def export_enabled() -> bool:
    return _flag("ENABLE_EXPORT")


def tracker_enabled() -> bool:
    return _flag("ENABLE_TRACKER")


# ── Score persistence ───────────────────────────────────────────────────────────

def persist_scored_job(sj, role_category: str) -> bool:
    """Upsert one graph ``ScoredJob`` into the scores table. Idempotent by job_id.

    The graph produces the Phase-2 component scores (ScoreComponents); the legacy
    v1 dimensions (role_fit/location_fit/seniority_fit) are not computed here and
    default to 0. Returns True on success, False if the row was skipped/failed.
    """
    from core import database

    comp = sj.components
    try:
        database.insert_score(
            job_id=sj.job_id,
            role_category=role_category or "unknown",
            total_score=int(sj.total_score),
            # v1 dimensions are not produced by the graph scorer.
            role_fit=0,
            skills_match=int(comp.skill_match),
            location_fit=0,
            seniority_fit=0,
            explanation=sj.claude_explanation or "",
            matched_categories={},
            prompt_version="graph",
            dictionary_version="graph",
            role_dictionary_version="graph",
            # Phase-2 dimensions map directly.
            track_alignment_score=int(comp.track_alignment),
            mba_relevance_score=int(comp.mba_relevance),
            company_quality_score=int(comp.company_quality),
            intl_friendliness_score=int(comp.intl_friendliness),
            pivot_bonus_score=int(comp.pivot_bonus),
            priority_bucket=sj.priority_bucket.value,
            scoring_metadata=json.dumps({"source": "graph"}),
            semantic_similarity=sj.semantic_similarity,
        )
        return True
    except Exception as exc:  # one bad row must not abort the batch
        logger.warning("persist_scored_job: job %s failed: %s", sj.job_id, exc)
        return False


def persist_scores(scored_jobs, role_by_id: dict[int, str]) -> int:
    """Upsert a batch of ScoredJobs. Returns the count persisted."""
    n = 0
    for sj in scored_jobs or []:
        if persist_scored_job(sj, role_by_id.get(sj.job_id, "unknown")):
            n += 1
    return n
