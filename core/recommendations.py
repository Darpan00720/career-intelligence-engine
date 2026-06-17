"""Application Recommendations — Phase 6 actionable intelligence layer.

Transforms ratified OpportunityIntelligence + CompositeScore (tier) outputs
into explainable, actionable Recommendations. This module performs NO scoring
and changes no ratified formula/weight/threshold/ranking. The category and
strength/risk cutoffs below are PRESENTATION thresholds for the recommendation
layer only (documented, not hidden).
"""
from __future__ import annotations

from schemas.control import Tier
from schemas.intelligence import OpportunityIntelligence
from schemas.jobs import ClassifiedJob, IngestedJob
from schemas.planning import CompositeScore
from schemas.recommendations import PreparationAction, Recommendation, RecommendationType

# --- recommendation-layer presentation thresholds (NOT scoring) ----------- #
HIGH_ROI = 60               # APPLY_THIS_WEEK gate for Tier 2
LOW_ROI = 40                # MONITOR signal
LOW_INTERVIEW_PROB = 40     # STRETCH_ROLE / risk signal
HIGH_STRATEGIC_FIT = 70     # STRETCH_ROLE strategic-value gate (ai_dt_fit)
STRONG = 70                 # strength threshold (mba/ai_dt/brand)
WEAK = 40                   # risk threshold (sponsorship)
COMPETITIVE_INTERVIEW = 60  # "Competitive interview probability" strength gate
FAVORABLE_SPONSORSHIP = 60  # "Favorable sponsorship likelihood" strength gate
HIGH_EFFORT = 60            # "High application effort" risk + extra-prep action

_STRATEGY = {
    RecommendationType.APPLY_IMMEDIATELY: "Apply immediately — top-tier match; lead your weekly applications with this.",
    RecommendationType.APPLY_THIS_WEEK: "Apply this week — strong expected ROI with solid fit.",
    RecommendationType.STRETCH_ROLE: "Apply selectively as a stretch — invest in networking and targeted prep to offset lower interview odds.",
    RecommendationType.MONITOR: "Monitor — revisit if a stronger signal emerges or capacity allows.",
}

_TRACK_A_PRODUCT = {"product_management"}
_TRACK_A_AIDT = {"ai_strategy", "product_management", "digital_transformation"}
_TRACK_A_STRATEGY = {"business_strategy"}
_TRACK_B = {"people_analytics", "hr_analytics", "workforce_planning", "talent_acquisition"}


def _categorize(tier: Tier, roi: int, ai_dt_fit: int, interview_prob: int) -> RecommendationType:
    """Deterministic category from ratified tier + intelligence metrics."""
    if tier == Tier.TIER_1:
        return RecommendationType.APPLY_IMMEDIATELY
    if tier == Tier.TIER_3:
        return RecommendationType.MONITOR
    # Tier 2:
    if interview_prob <= LOW_INTERVIEW_PROB and ai_dt_fit >= HIGH_STRATEGIC_FIT:
        return RecommendationType.STRETCH_ROLE
    if roi >= HIGH_ROI:
        return RecommendationType.APPLY_THIS_WEEK
    return RecommendationType.MONITOR


def _strengths(oi: OpportunityIntelligence) -> list[str]:
    out = []
    if oi.mba_fit >= STRONG:
        out.append("Strong MBA-level fit")
    if oi.ai_dt_fit >= STRONG:
        out.append("Strong AI / Digital-Transformation alignment")
    if oi.brand >= STRONG:
        out.append("High employer-brand value")
    if oi.interview_prob >= COMPETITIVE_INTERVIEW:
        out.append("Competitive interview probability")
    if oi.sponsorship_prob >= FAVORABLE_SPONSORSHIP:
        out.append("Favorable sponsorship likelihood")
    return out


def _risks(oi: OpportunityIntelligence) -> list[str]:
    out = []
    if oi.interview_prob <= LOW_INTERVIEW_PROB:
        out.append("Low interview probability (stretch)")
    if oi.sponsorship_prob <= WEAK:
        out.append("Sponsorship uncertain")
    if oi.effort >= HIGH_EFFORT:
        out.append("High application effort")
    if oi.roi <= LOW_ROI:
        out.append("Low expected ROI")
    return out


def _preparation(oi: OpportunityIntelligence, role_category: str, company: str) -> list[PreparationAction]:
    """Actionable prep derived only from intelligence metrics, role category,
    and job metadata. Always includes at least the CV-tailoring action."""
    actions = [PreparationAction(
        action="Tailor CV and cover letter to the role",
        rationale=f"Aligns the application to {role_category} expectations",
    )]
    if role_category in _TRACK_A_PRODUCT:
        actions.append(PreparationAction(
            action="Practice product-sense and product-case interviews",
            rationale="Product Management role"))
    if role_category in _TRACK_A_AIDT:
        actions.append(PreparationAction(
            action="Prepare 2-3 AI / digital-transformation strategy examples",
            rationale="AI/Product/Digital-Transformation track"))
    if role_category in _TRACK_A_STRATEGY:
        actions.append(PreparationAction(
            action="Prepare a structured business/strategy case",
            rationale="Business Strategy role"))
    if role_category in _TRACK_B:
        actions.append(PreparationAction(
            action="Revise people-analytics / workforce metrics and tooling",
            rationale="People/Workforce track"))
    if oi.interview_prob <= LOW_INTERVIEW_PROB:
        actions.append(PreparationAction(
            action=f"Network with alumni/employees at {company} to secure a referral",
            rationale="Low interview probability — a referral materially raises odds"))
    if oi.sponsorship_prob <= WEAK:
        actions.append(PreparationAction(
            action="Confirm visa-sponsorship availability before investing time",
            rationale="Sponsorship likelihood is uncertain"))
    if oi.effort >= HIGH_EFFORT:
        actions.append(PreparationAction(
            action="Block extra preparation time; expect strong competition",
            rationale="High estimated application effort"))
    return actions


def _reason(tier: Tier, comp: float, oi: OpportunityIntelligence) -> str:
    tier_label = {Tier.TIER_1: "Tier 1", Tier.TIER_2: "Tier 2", Tier.TIER_3: "Tier 3"}[tier]
    return (f"{tier_label} (composite {comp}): interview {oi.interview_prob}%, "
            f"ROI {oi.roi}, AI/DT fit {oi.ai_dt_fit}, sponsorship {oi.sponsorship_prob}%.")


def generate_recommendations(
    intelligence: list[OpportunityIntelligence],
    prioritized: list[CompositeScore],
    jobs: dict[int, IngestedJob],
    classified: dict[int, ClassifiedJob] | None = None,
) -> list[Recommendation]:
    """Generate one explainable Recommendation per prioritized job.

    Deterministic: output order follows the prioritized ranking. `classified`
    supplies role_category for preparation guidance (falls back to 'unknown')."""
    classified = classified or {}
    oi_by_id = {oi.job_id: oi for oi in intelligence}
    # iterate in ranked order for deterministic output
    ordered = sorted(prioritized, key=lambda cs: cs.rank)

    recs: list[Recommendation] = []
    for cs in ordered:
        oi = oi_by_id.get(cs.job_id)
        if oi is None:
            continue
        rec_type = _categorize(cs.tier, oi.roi, oi.ai_dt_fit, oi.interview_prob)
        role_category = classified[cs.job_id].role_category if cs.job_id in classified else "unknown"
        company = jobs[cs.job_id].company if cs.job_id in jobs else "the company"
        recs.append(Recommendation(
            job_id=cs.job_id,
            recommendation_type=rec_type,
            recommendation_reason=_reason(cs.tier, cs.composite, oi),
            strengths=_strengths(oi),
            risks=_risks(oi),
            application_strategy=_STRATEGY[rec_type],
            preparation_actions=_preparation(oi, role_category, company),
        ))
    return recs
