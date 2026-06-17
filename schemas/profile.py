"""Candidate profile and derived career-strategy schemas (Profile Agent I/O)."""
from __future__ import annotations

from pydantic import Field

from schemas.base import CareerBaseModel
from schemas.control import Track


class CareerProfile(CareerBaseModel):
    """Normalised candidate profile consumed by all downstream agents."""

    name: str
    years_experience: int = Field(ge=0, le=60)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    target_primary: list[str] = Field(min_length=1)
    target_secondary: list[str] = Field(default_factory=list)
    locations: list[str] = Field(min_length=1)
    needs_sponsorship: bool = True


class TrackStrategy(CareerBaseModel):
    """Derived primary-track recommendation and its rationale."""

    primary_track: Track
    rationale: str
