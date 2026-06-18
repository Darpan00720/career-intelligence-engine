"""Continuous Feedback Engine (v4).

Pure, read-only outcome analytics: learns which companies, roles, score bands,
and recommendations convert. An "interview+" outcome means the application
reached Interview, Final Round, or Offer; a "win" means Offer.

    from core import feedback_engine
    feedback_engine.conversion_by_company()
    feedback_engine.conversion_by_score_band()
    feedback_engine.recommendation_effectiveness()
"""
from __future__ import annotations

from core import database

# Statuses that count as "reached interview or beyond".
_INTERVIEW_PLUS = {"Interview", "Final Round", "Offer"}
_APPLIED_PLUS = {"Applied", "Online Assessment", "Interview", "Final Round", "Offer"}


def _rate(num: int, den: int) -> float:
    return round(100.0 * num / den, 1) if den else 0.0


def _score_band(score) -> str:
    s = score or 0
    if s >= 90:
        return "90-100"
    if s >= 80:
        return "80-89"
    if s >= 70:
        return "70-79"
    return "<70"


def _aggregate(rows: list[dict], key_fn) -> dict[str, dict]:
    """Group rows by key_fn and compute applied / interview / offer rates."""
    buckets: dict[str, dict] = {}
    for row in rows:
        key = key_fn(row) or "Unknown"
        b = buckets.setdefault(key, {"applied": 0, "interviews": 0, "offers": 0})
        status = row.get("status")
        if status in _APPLIED_PLUS:
            b["applied"] += 1
        if status in _INTERVIEW_PLUS:
            b["interviews"] += 1
        if status == "Offer":
            b["offers"] += 1
    # Attach derived rates.
    for b in buckets.values():
        b["interview_rate"] = _rate(b["interviews"], b["applied"])
        b["offer_rate"] = _rate(b["offers"], b["applied"])
    return buckets


def conversion_by_company() -> dict[str, dict]:
    return _aggregate(database.get_outcomes_joined(), lambda r: r.get("company"))


def conversion_by_role() -> dict[str, dict]:
    return _aggregate(database.get_outcomes_joined(), lambda r: r.get("role_category"))


def conversion_by_score_band() -> dict[str, dict]:
    return _aggregate(database.get_outcomes_joined(), lambda r: _score_band(r.get("total_score")))


def recommendation_effectiveness() -> dict[str, dict]:
    """Group outcomes by the recommendation the engine would give each job today.

    Approximation in the absence of point-in-time recommendation history: it
    re-derives the recommendation from current job features, which is stable for
    score/sponsorship-driven actions.
    """
    from core.recommendation_engine import recommend

    rows = database.get_outcomes_joined()
    research_ids = database.get_researched_job_ids()
    for r in rows:
        r["application_status"] = "Not Started"  # neutralize status for the rec itself
        rec = recommend(r, has_research=r.get("job_id") in research_ids)
        r["_recommendation"] = rec["recommendation"]
    # Restore real status for outcome aggregation.
    for r, original in zip(rows, database.get_outcomes_joined()):
        r["status"] = original["status"]
    return _aggregate(rows, lambda r: r.get("_recommendation"))


def summary() -> dict:
    return {
        "conversion_by_company": conversion_by_company(),
        "conversion_by_role": conversion_by_role(),
        "conversion_by_score_band": conversion_by_score_band(),
        "recommendation_effectiveness": recommendation_effectiveness(),
    }
