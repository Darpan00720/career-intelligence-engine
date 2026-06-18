"""Job ranking — a computed export/view-layer concern (no schema change).

Every scored job is ordered by score (then recency) and assigned a 1-based
rank. No `rank` column is written to the database: ranks are recomputed on
demand so they always reflect the current scores. This keeps the schema
untouched and the strategic `priority_bucket` taxonomy unaffected.

Usage:
    from core.ranking import rank_jobs, top_jobs
    ranked = rank_jobs(database.get_all_scored_jobs_ranked())
    leaders = top_jobs(limit=20)
"""
from __future__ import annotations

from core import database
from core.scorer import application_priority


def rank_jobs(rows: list[dict]) -> list[dict]:
    """Sort rows by score DESC, then date-found DESC, and assign ranks from 1.

    Returns shallow copies with two added keys per row:
      rank                  1-based position
      application_priority  action band derived from the score

    The input list and its dicts are not mutated. Sorting is performed here
    (not trusted from the caller) so ranking is correct regardless of input
    order. Python's stable sort means equal scores keep date-DESC order.
    """
    # Secondary key first (date desc, id desc), then stable primary (score desc).
    by_recency = sorted(
        rows,
        key=lambda r: ((r.get("fetched_date") or ""), (r.get("id") or 0)),
        reverse=True,
    )
    ordered = sorted(by_recency, key=lambda r: (r.get("total_score") or 0), reverse=True)

    ranked: list[dict] = []
    for position, row in enumerate(ordered, start=1):
        item = dict(row)
        item["rank"] = position
        item["application_priority"] = application_priority(row.get("total_score"))
        ranked.append(item)
    return ranked


def top_jobs(limit: int = 20) -> list[dict]:
    """Return the top `limit` scored jobs, ranked. Empty list if none scored."""
    ranked = rank_jobs(database.get_all_scored_jobs_ranked())
    if limit is None or limit < 0:
        return ranked
    return ranked[:limit]
