"""Job ingestion and taxonomy schemas (Ingestion + Taxonomy Agent I/O).

No fetching or classification logic lives here — these are the data contracts
that wrap the existing search_agent and role_loader outputs.
"""
from __future__ import annotations

from pydantic import Field

from schemas.base import CareerBaseModel
from schemas.control import Track


class RawJob(CareerBaseModel):
    """A job as fetched from an ATS board, pre-gating."""

    title: str
    company: str
    location: str | None = None
    url: str
    description: str
    board: str  # e.g. "greenhouse", "lever"


class GateResult(CareerBaseModel):
    """Outcome of a single eligibility gate (geography/language/visa)."""

    passed: bool
    reason: str


class IngestedJob(CareerBaseModel):
    """A job after eligibility gating. eligible = all gates passed."""

    job_id: int
    title: str
    company: str
    location: str | None = None
    url: str
    geography_gate: GateResult
    language_gate: GateResult
    visa_gate: GateResult
    eligible: bool


class ClassifiedJob(CareerBaseModel):
    """A job after role-category classification (taxonomy v1.2)."""

    job_id: int
    role_category: str = "unknown"
    track: Track = Track.UNCLASSIFIED


class IngestionStats(CareerBaseModel):
    fetched: int = 0
    passed: int = 0
    rejected: int = 0
    rejected_by_reason: dict[str, int] = Field(default_factory=dict)


class TaxonomyStats(CareerBaseModel):
    by_category: dict[str, int] = Field(default_factory=dict)
    unknown_count: int = 0
