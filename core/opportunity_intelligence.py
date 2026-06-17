"""Opportunity Intelligence — authoritative 8-metric assessment.

Implements docs/OPPORTUNITY_INTELLIGENCE_SPEC.md v1.0 exactly. All formulas,
weights, and thresholds are ratified there; this module must not change them.

Six metrics are deterministic (derived from existing score_job components +
the eligibility engine). Two metrics — interview probability and application
effort — are LLM-generated via a mockable hook so tests stay offline.
"""
from __future__ import annotations

from core.logging_config import get_logger
from schemas.intelligence import IntelLLMAssessment, OpportunityIntelligence
from schemas.jobs import ClassifiedJob, IngestedJob
from schemas.profile import CareerProfile
from schemas.scoring import ScoredJob

logger = get_logger(__name__)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> int:
    return int(max(lo, min(hi, v)))


def assess(
    profile: CareerProfile,
    job: IngestedJob,
    classified: ClassifiedJob,
    score: ScoredJob,
    company_profile=None,
    claude_hook=None,
    eligibility: dict | None = None,
) -> OpportunityIntelligence:
    """Produce the full 8-metric OpportunityIntelligence for one role.

    Args:
        eligibility: optional dict carrying the eligibility-engine fields for
            this job (`visa_accessibility` 0-40). Falls back to a neutral value
            when absent.
        company_profile: CompanyEligibilityProfile (visa_friendliness_score 0-10).
            Resolved via company_eligibility.lookup_company when not provided.
        claude_hook: callable(profile, job, score) -> IntelLLMAssessment.
            When None, interview_prob/effort fall back to deterministic values
            and status="degraded" (offline-safe).
    """
    c = score.components

    # --- deterministic metrics (spec §1.1, §1.2, §1.3, §1.6) ---------------
    mba_fit = round(c.mba_relevance / 15 * 100)
    ai_dt_fit = round(c.track_alignment / 30 * 100)
    brand = round(c.company_quality / 15 * 100)
    # pivot_value intentionally reuses track_alignment (same as ai_dt_fit) and
    # carries an additional 5% composite weight: strategic pivot potential is
    # considered independently valuable from track-fit alone. (Spec §1.6.)
    pivot_value = round(c.track_alignment / 30 * 100)

    # --- Sponsorship Probability (deterministic, eligibility engine §1.5) --
    visa_accessibility = float((eligibility or {}).get("visa_accessibility", 0) or 0)
    if company_profile is None:
        try:
            from core.company_eligibility import lookup_company
            company_profile = lookup_company(job.company)
        except Exception:
            company_profile = None
    vf_raw = getattr(company_profile, "visa_friendliness_score", None)
    visa_friendliness = float(vf_raw if vf_raw is not None else 5)  # preserve a real 0
    sponsorship_prob = round(
        0.50 * (visa_accessibility / 40 * 100)
        + 0.30 * (visa_friendliness / 10 * 100)
        + 0.20 * (c.intl_friendliness / 10 * 100)
    )

    # --- LLM metrics: interview_prob + effort (spec §1.4, §1.7) ------------
    status = "ok"
    if claude_hook is not None:
        try:
            llm: IntelLLMAssessment = claude_hook(profile, job, score)
            interview_prob = _clamp(llm.interview_probability)
            effort = _clamp(llm.application_effort)
        except Exception as exc:
            logger.warning("intel LLM hook failed for job %s: %s; degraded", job.job_id, exc)
            interview_prob = _clamp(score.total_score)          # spec fallback
            effort = _clamp(100 - interview_prob)
            status = "degraded"
    else:
        interview_prob = _clamp(score.total_score)              # offline fallback
        effort = _clamp(100 - interview_prob)
        status = "degraded"

    # --- Expected ROI (deterministic composed, spec §1.8) ------------------
    base = 0.50 * interview_prob + 0.30 * pivot_value + 0.20 * sponsorship_prob
    roi = _clamp(round(base * (1 - 0.30 * effort / 100)))

    return OpportunityIntelligence(
        job_id=score.job_id,
        mba_fit=_clamp(mba_fit),
        ai_dt_fit=_clamp(ai_dt_fit),
        brand=_clamp(brand),
        interview_prob=interview_prob,
        sponsorship_prob=_clamp(sponsorship_prob),
        pivot_value=_clamp(pivot_value),
        effort=effort,
        roi=roi,
        gaps=[],
        status=status,
    )
