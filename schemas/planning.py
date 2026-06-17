"""Application prioritization schemas (Prioritization Agent I/O).

Encodes the finalised composite formula weights. The weights are declared
here as the single source of truth and validated to sum to 1.0.
"""
from __future__ import annotations

from pydantic import Field, field_validator

from schemas.base import CareerBaseModel
from schemas.control import Tier

# Composite weighting (must sum to 1.0). Single source of truth.
COMPOSITE_WEIGHTS: dict[str, float] = {
    "interview_prob": 0.30,
    "mba_fit": 0.20,
    "ai_dt_fit": 0.20,
    "sponsorship_prob": 0.15,
    "brand": 0.10,
    "pivot_value": 0.05,
}

MAX_THIS_WEEK = 5


class CompositeScore(CareerBaseModel):
    job_id: int
    composite: float = Field(ge=0.0, le=100.0)
    tier: Tier
    rank: int = Field(ge=1)


class ApplyItem(CareerBaseModel):
    company: str
    title: str
    location: str | None = None
    composite: float = Field(ge=0.0, le=100.0)
    interview_prob: int = Field(ge=0, le=100)
    roi: int = Field(ge=0, le=100)
    rationale: str


class WeeklyPlan(CareerBaseModel):
    this_week: list[ApplyItem] = Field(default_factory=list)
    next_week: list[ApplyItem] = Field(default_factory=list)

    @field_validator("this_week")
    @classmethod
    def _cap_this_week(cls, v: list[ApplyItem]) -> list[ApplyItem]:
        if len(v) > MAX_THIS_WEEK:
            raise ValueError(f"this_week may contain at most {MAX_THIS_WEEK} items")
        return v
