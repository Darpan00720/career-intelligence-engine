"""Scoring schemas (Scoring Agent I/O).

Mirrors the finalised 6-dimension scoring engine in core/scorer.py.
Bounds match the engine; this module performs NO scoring itself.
"""
from __future__ import annotations

from enum import Enum

from pydantic import ConfigDict, Field, computed_field

from schemas.base import CareerBaseModel


class PriorityBucket(str, Enum):
    APPLY_IMMEDIATELY = "Apply Immediately"
    HIGH = "High Priority"
    MEDIUM = "Medium Priority"
    LOW = "Low Priority"
    IGNORE = "Ignore"
    REJECTED = "Rejected"


class ScoreComponents(CareerBaseModel):
    """The six deterministic scoring dimensions with their engine bounds.

    extra="ignore" (overriding the base "forbid") so the serialized computed
    field `total` is dropped on round-trip reconstruction instead of raising —
    R1 serialization remediation (Phase 7C). Behavior/output unchanged: `total`
    is still emitted by model_dump_json(); it is merely ignored on re-validation.
    """

    model_config = ConfigDict(extra="ignore")

    track_alignment: int = Field(ge=0, le=30)
    skill_match: int = Field(ge=0, le=25)
    mba_relevance: int = Field(ge=0, le=15)
    company_quality: int = Field(ge=0, le=15)
    intl_friendliness: int = Field(ge=0, le=10)
    pivot_bonus: int = Field(ge=0, le=5)

    @computed_field  # type: ignore[misc]
    @property
    def total(self) -> int:
        return (
            self.track_alignment
            + self.skill_match
            + self.mba_relevance
            + self.company_quality
            + self.intl_friendliness
            + self.pivot_bonus
        )


class ClaudeScoreAdjustment(CareerBaseModel):
    """Existing Claude qualitative-layer contract: bounded +/-10 adjustment."""

    score_adjustment: int = Field(ge=-10, le=10)
    explanation: str


class ScoredJob(CareerBaseModel):
    job_id: int
    total_score: int = Field(ge=0, le=100)
    components: ScoreComponents
    priority_bucket: PriorityBucket
    semantic_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    claude_explanation: str | None = None


class ScoringStats(CareerBaseModel):
    scored: int = 0
    claude_calls: int = 0
    claude_errors: int = 0
    by_bucket: dict[str, int] = Field(default_factory=dict)
