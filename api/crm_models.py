"""Pydantic schemas for the v4 CRM REST API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Page(BaseModel):
    """A paginated envelope around a list of items."""
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class JobOut(BaseModel):
    id: int
    title: str | None = None
    company: str | None = None
    location: str | None = None
    total_score: int | None = None
    application_priority: str | None = None
    application_status: str | None = None
    url: str | None = None


class StatusUpdate(BaseModel):
    job_id: int = Field(..., ge=1)
    status: str
    notes: str | None = None


class PipelineRunRequest(BaseModel):
    top_n: int = Field(20, ge=1, le=200)
    research_min_score: int = Field(80, ge=0, le=100)


class MessageResponse(BaseModel):
    ok: bool
    message: str
    data: dict[str, Any] | None = None
