"""Career Analytics Engine (v3) — pure read-only metrics over the CRM tables.

No writes, no side effects. Each function returns a plain dict/list so callers
(CLI, Excel dashboard, API) can render however they like.

    from core import analytics
    analytics.application_metrics()
    analytics.funnel_metrics()
    analytics.conversion_metrics()
    analytics.status_breakdown()
"""
from __future__ import annotations

from core import database, tracker

# Forward funnel (Rejected is terminal and counted separately, not in the order).
_FUNNEL = ["Applied", "Online Assessment", "Interview", "Final Round", "Offer"]


def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def _reached(counts: dict[str, int], stage: str) -> int:
    """Count applications currently at `stage` or any later forward stage."""
    idx = _FUNNEL.index(stage)
    return sum(counts.get(s, 0) for s in _FUNNEL[idx:])


def status_breakdown() -> dict[str, int]:
    """Return {status: count}, zero-filled over the canonical lifecycle and
    including any legacy/extra statuses present in the table."""
    breakdown = {s: 0 for s in tracker.STATUSES}
    for status, n in database.get_status_counts().items():
        breakdown[status] = breakdown.get(status, 0) + n
    return breakdown


def application_metrics() -> dict:
    """Headline counts across the full lifecycle."""
    agg = database.get_aggregate_counts()
    counts = status_breakdown()
    return {
        "jobs_found":   agg["jobs_found"],
        "jobs_scored":  agg["jobs_scored"],
        "researched":   agg["researched"],
        "documents":    agg["documents"],
        "applied":      _reached(counts, "Applied"),
        "assessments":  _reached(counts, "Online Assessment"),
        "interviews":   _reached(counts, "Interview"),
        "final_rounds": _reached(counts, "Final Round"),
        "offers":       counts.get("Offer", 0),
        "rejections":   counts.get("Rejected", 0),
    }


def funnel_metrics() -> list[dict]:
    """Ordered funnel stages with cumulative counts (top → bottom)."""
    m = application_metrics()
    stages = [
        ("Jobs Found",   m["jobs_found"]),
        ("Jobs Scored",  m["jobs_scored"]),
        ("Applied",      m["applied"]),
        ("Assessments",  m["assessments"]),
        ("Interviews",   m["interviews"]),
        ("Final Rounds", m["final_rounds"]),
        ("Offers",       m["offers"]),
    ]
    return [{"stage": s, "count": c} for s, c in stages]


def conversion_metrics() -> dict:
    """Conversion / interview / offer rates as percentages."""
    m = application_metrics()
    return {
        "conversion_rate": _pct(m["applied"], m["jobs_scored"]),   # scored → applied
        "interview_rate":  _pct(m["interviews"], m["applied"]),    # applied → interview
        "offer_rate":      _pct(m["offers"], m["applied"]),        # applied → offer
    }


def summary() -> dict:
    """Convenience bundle of all metric groups (used by the Excel dashboard)."""
    return {
        "application_metrics": application_metrics(),
        "funnel_metrics":      funnel_metrics(),
        "conversion_metrics":  conversion_metrics(),
        "status_breakdown":    status_breakdown(),
    }
