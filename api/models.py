"""API request/response models (transport DTOs).

Reuses the ratified domain schemas for response bodies; adds only thin request
wrappers. No business fields.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.output import ApiResponse


class AnalyzeRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    profile_path: str = "candidate_profile.json"
    limit: int | None = Field(default=None, ge=1, le=1000)


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    database: bool
    checkpoint_storage: bool
    graph_compiled: bool
    embeddings_loaded: bool


class VersionResponse(BaseModel):
    name: str
    version: str
    system_certified: bool


class RunResponse(BaseModel):
    """Wraps the deterministic ApiResponse plus the thread id."""

    thread_id: str
    found: bool = True
    result: ApiResponse | None = None
