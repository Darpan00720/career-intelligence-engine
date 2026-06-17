"""Output-experience schemas (Phase 7 — presentation layer).

Presentation-only views over existing ratified state. No business calculations,
no scoring, no recommendation logic. All models are deterministic and
serialization-safe.
"""
from __future__ import annotations

from typing import Any

from pydantic import Field

from schemas.base import CareerBaseModel
from schemas.planning import ApplyItem, CompositeScore
from schemas.profile import CareerProfile
from schemas.recommendations import Recommendation, RecommendationType

OUTPUT_VERSION = "1.0"


class CareerSummary(CareerBaseModel):
    """Deterministic narrative summary derived from profile + plan + recs."""

    career_direction: str
    application_status: str
    recommended_focus: str
    immediate_actions: list[str] = Field(default_factory=list)


class TopOpportunity(CareerBaseModel):
    """A presentation row for a ranked opportunity."""

    rank: int
    job_id: int
    company: str
    title: str
    recommendation_type: RecommendationType
    interview_prob: int = Field(ge=0, le=100)
    roi: int = Field(ge=0, le=100)
    sponsorship_prob: int = Field(ge=0, le=100)
    preparation_actions: list[str] = Field(default_factory=list)


class DashboardSection(CareerBaseModel):
    """A titled key/value section (JSON-safe values only)."""

    title: str
    data: dict[str, Any] = Field(default_factory=dict)


class Dashboard(CareerBaseModel):
    profile_summary: DashboardSection
    pipeline_summary: DashboardSection
    application_summary: DashboardSection
    weekly_plan: DashboardSection
    top_opportunities: list[TopOpportunity] = Field(default_factory=list)


class ExportPayload(CareerBaseModel):
    """Self-contained, JSON-serializable bundle for export / future CSV/PDF/UI."""

    profile: CareerProfile | None = None
    prioritized: list[CompositeScore] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    this_week: list[ApplyItem] = Field(default_factory=list)
    next_week: list[ApplyItem] = Field(default_factory=list)
    summary: CareerSummary
    dashboard_metadata: dict[str, Any] = Field(default_factory=dict)

    def to_json(self) -> str:
        """Stable JSON serialization (future CSV/PDF/UI render off this)."""
        return self.model_dump_json()


class ApiResponse(CareerBaseModel):
    success: bool
    version: str
    generated_at: str  # injected at the transport edge; "" in the deterministic core
    summary: CareerSummary
    dashboard: Dashboard
    export_payload: ExportPayload
