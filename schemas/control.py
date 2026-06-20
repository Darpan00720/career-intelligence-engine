"""Control-plane schemas and enums: phases, statuses, routing, errors.

These models drive the Supervisor/graph orchestration. They contain no
business logic — only the vocabulary the graph uses to route and report.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import Field

from schemas.base import CareerBaseModel


class Phase(str, Enum):
    """Ordered pipeline phases. Supervisor advances through these."""

    INIT = "init"
    PROFILE = "profile_strategy"
    ACQUISITION = "acquire_jobs"   # v6: opt-in write-capable acquisition stage
    INGESTION = "job_ingestion"
    TAXONOMY = "taxonomy"
    SCORING = "scoring"
    RESEARCH = "research"          # v6: opt-in cost-incurring research stage
    INTELLIGENCE = "opportunity_intel"
    PRIORITIZATION = "prioritization"
    RECOMMENDATIONS = "recommendations"
    DOCUMENTS = "documents"        # v6: documents stage (now run via the terminal runner)
    TERMINAL = "terminal"          # v6: ordered terminal runner (documents/export/tracker)
    OUTPUT = "output_experience"
    REPORT = "report"
    DONE = "done"


class RunStatus(str, Enum):
    RUNNING = "running"
    AWAITING_HUMAN = "awaiting_human"
    COMPLETED = "completed"
    FAILED = "failed"


class Track(str, Enum):
    """Career track. Mirrors search_agent._assign_track output."""

    A = "A"
    B = "B"
    UNCLASSIFIED = "unclassified"


class Tier(int, Enum):
    """Application tier from the prioritization framework."""

    TIER_1 = 1  # Apply immediately
    TIER_2 = 2  # Apply if capacity allows
    TIER_3 = 3  # Monitor only


class Importance(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class Horizon(str, Enum):
    SHORT = "short"  # closeable in weeks
    LONG = "long"    # requires months of experience


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentError(CareerBaseModel):
    """A recoverable or terminal failure raised by a node."""

    node: str
    message: str
    recoverable: bool = True
    timestamp: datetime = Field(default_factory=_utcnow)


class RoutingDecision(CareerBaseModel):
    """Supervisor's decision on which node to run next."""

    next_node: str
    reason: str


class RunMeta(CareerBaseModel):
    """Top-level run metadata."""

    run_id: str
    phase: Phase = Phase.INIT
    status: RunStatus = RunStatus.RUNNING
    started_at: datetime = Field(default_factory=_utcnow)
