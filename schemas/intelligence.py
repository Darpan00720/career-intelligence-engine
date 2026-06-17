"""Opportunity Intelligence schemas (Intelligence Agent I/O).

The eight candid 0-100 metrics plus categorised skill gaps from the
finalised Opportunity Intelligence framework.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field

from schemas.base import CareerBaseModel
from schemas.control import Horizon, Importance


class GapCategory(str, Enum):
    TECHNICAL = "Technical"
    PRODUCT = "Product"
    STRATEGY = "Strategy"
    ANALYTICS = "Analytics"
    LEADERSHIP = "Leadership"
    DOMAIN = "Domain"


class SkillGap(CareerBaseModel):
    category: GapCategory
    importance: Importance
    horizon: Horizon
    note: str | None = None


_Score = Field(ge=0, le=100)


class OpportunityIntelligence(CareerBaseModel):
    """Per-role assessment. Scores are candid and must not be inflated."""

    job_id: int
    mba_fit: int = _Score
    ai_dt_fit: int = _Score
    brand: int = _Score
    interview_prob: int = _Score
    sponsorship_prob: int = _Score
    pivot_value: int = _Score
    effort: int = _Score          # higher = more effort required
    roi: int = _Score
    gaps: list[SkillGap] = Field(default_factory=list)
    status: Literal["ok", "degraded"] = "ok"


class IntelLLMAssessment(CareerBaseModel):
    """Structured output from the LLM layer: the two candor-sensitive metrics
    (interview probability + application effort) plus a short rationale.
    Mockable; see prompts/opportunity_intel_prompt.txt."""

    interview_probability: int = _Score
    application_effort: int = _Score   # higher = more effort required
    rationale: str
