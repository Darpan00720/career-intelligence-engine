"""Prioritization — authoritative composite / tiering / ranking / planning.

Implements docs/OPPORTUNITY_INTELLIGENCE_SPEC.md v1.0 §2 exactly. Weights come
from schemas.planning.COMPOSITE_WEIGHTS (ratified as-is). Tier cutoffs 65/50.

Ratified public interface: composite(), assign_tier(), prioritize() -> WeeklyPlan.
rank()/build_plan() are internal helpers so the graph node can obtain BOTH the
ranked CompositeScore list and the WeeklyPlan without duplicating the algorithm.
"""
from __future__ import annotations

from core.config import MAX_APPLICATIONS_PER_WEEK
from schemas.control import Tier
from schemas.intelligence import OpportunityIntelligence
from schemas.jobs import IngestedJob
from schemas.planning import COMPOSITE_WEIGHTS, MAX_THIS_WEEK, ApplyItem, CompositeScore, WeeklyPlan

# Ratified tier cutoffs (spec §2 Tiering).
TIER_1_MIN = 65.0
TIER_2_MIN = 50.0


def composite(intelligence: OpportunityIntelligence) -> float:
    """Weighted composite (0-100), rounded to 2 decimals. Spec §2 Composite."""
    oi = intelligence
    value = (
        COMPOSITE_WEIGHTS["interview_prob"] * oi.interview_prob
        + COMPOSITE_WEIGHTS["mba_fit"] * oi.mba_fit
        + COMPOSITE_WEIGHTS["ai_dt_fit"] * oi.ai_dt_fit
        + COMPOSITE_WEIGHTS["sponsorship_prob"] * oi.sponsorship_prob
        + COMPOSITE_WEIGHTS["brand"] * oi.brand
        + COMPOSITE_WEIGHTS["pivot_value"] * oi.pivot_value
    )
    return round(value, 2)


def assign_tier(composite_score: float) -> Tier:
    """Tier 1 >= 65; Tier 2 in [50,65); Tier 3 < 50. Spec §2 Tiering."""
    if composite_score >= TIER_1_MIN:
        return Tier.TIER_1
    if composite_score >= TIER_2_MIN:
        return Tier.TIER_2
    return Tier.TIER_3


def rank(
    intelligence: list[OpportunityIntelligence],
    total_scores: dict[int, int] | None = None,
) -> list[CompositeScore]:
    """Rank + tier. Spec §2 Ranking: composite desc, then interview_prob desc,
    total_score desc, job_id asc."""
    total_scores = total_scores or {}
    scored = [(composite(oi), oi) for oi in intelligence]
    scored.sort(key=lambda t: (
        -t[0],
        -t[1].interview_prob,
        -total_scores.get(t[1].job_id, 0),
        t[1].job_id,
    ))
    return [
        CompositeScore(job_id=oi.job_id, composite=comp, tier=assign_tier(comp), rank=i)
        for i, (comp, oi) in enumerate(scored, start=1)
    ]


def build_plan(
    prioritized: list[CompositeScore],
    intelligence: list[OpportunityIntelligence],
    jobs: dict[int, IngestedJob],
) -> WeeklyPlan:
    """Build the WeeklyPlan from an already-ranked list. Spec §2 Planning:
    this_week = top-5 Tier>=2; next_week = ranks 6-10 Tier>=2; capacity cap."""
    oi_by_id = {oi.job_id: oi for oi in intelligence}
    eligible = [cs for cs in prioritized if cs.tier in (Tier.TIER_1, Tier.TIER_2)]

    def _item(cs: CompositeScore) -> ApplyItem:
        oi = oi_by_id[cs.job_id]
        ij = jobs.get(cs.job_id)
        return ApplyItem(
            company=ij.company if ij else "Unknown",
            title=ij.title if ij else "Unknown",
            location=ij.location if ij else None,
            composite=cs.composite,
            interview_prob=oi.interview_prob,
            roi=oi.roi,
            rationale=f"composite {cs.composite}; interview {oi.interview_prob}%, ROI {oi.roi}%",
        )

    this_week = [_item(cs) for cs in eligible[:MAX_THIS_WEEK]]
    next_week = [_item(cs) for cs in eligible[MAX_THIS_WEEK:MAX_THIS_WEEK * 2]]

    # Capacity constraint: this_week + next_week <= MAX_APPLICATIONS_PER_WEEK.
    if len(this_week) + len(next_week) > MAX_APPLICATIONS_PER_WEEK:
        next_week = next_week[: max(0, MAX_APPLICATIONS_PER_WEEK - len(this_week))]

    return WeeklyPlan(this_week=this_week, next_week=next_week)


def prioritize(
    intelligence: list[OpportunityIntelligence],
    jobs: dict[int, IngestedJob],
    total_scores: dict[int, int] | None = None,
) -> WeeklyPlan:
    """Ratified entry point: rank then build the weekly plan."""
    return build_plan(rank(intelligence, total_scores), intelligence, jobs)
