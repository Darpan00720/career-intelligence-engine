"""Recommendation schemas (Phase 6 — application intelligence layer).

These are a PRESENTATION/derivation layer on top of the ratified
OpportunityIntelligence + CompositeScore outputs. They introduce no scoring;
recommendation-category thresholds are documented constants in
core/recommendations.py.
"""
from __future__ import annotations

from enum import Enum

from pydantic import Field

from schemas.base import CareerBaseModel


class RecommendationType(str, Enum):
    APPLY_IMMEDIATELY = "APPLY_IMMEDIATELY"
    APPLY_THIS_WEEK = "APPLY_THIS_WEEK"
    STRETCH_ROLE = "STRETCH_ROLE"
    MONITOR = "MONITOR"


class PreparationAction(CareerBaseModel):
    """A single actionable preparation step with its justification."""

    action: str
    rationale: str


class Recommendation(CareerBaseModel):
    """Explainable, actionable recommendation for one prioritized job."""

    job_id: int
    recommendation_type: RecommendationType
    recommendation_reason: str
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    application_strategy: str
    preparation_actions: list[PreparationAction] = Field(min_length=1)
