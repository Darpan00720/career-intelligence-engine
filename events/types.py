"""Event types and the versioned Event envelope (v5)."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

# Core event-type names.
JOB_DISCOVERED = "JobDiscovered"
JOB_SCORED = "JobScored"
RESEARCH_COMPLETED = "ResearchCompleted"
DOCUMENTS_GENERATED = "DocumentsGenerated"
RECOMMENDATION_GENERATED = "RecommendationGenerated"
APPLICATION_UPDATED = "ApplicationUpdated"
WORKFLOW_COMPLETED = "WorkflowCompleted"
WORKFLOW_FAILED = "WorkflowFailed"
NOTIFICATION_REQUESTED = "NotificationRequested"

CORE_EVENTS = [
    JOB_DISCOVERED, JOB_SCORED, RESEARCH_COMPLETED, DOCUMENTS_GENERATED,
    RECOMMENDATION_GENERATED, APPLICATION_UPDATED, WORKFLOW_COMPLETED,
    WORKFLOW_FAILED, NOTIFICATION_REQUESTED,
]


@dataclass
class Event:
    """Versioned event envelope (v5.1).

    Carries full distributed-tracing context — trace_id / correlation_id /
    causation_id / workflow_id / tenant_id — in addition to an idempotency key.
    trace_id/correlation_id auto-populate from the active tracing span when not
    supplied. Core persistence stays on the v5 columns for backward
    compatibility; the richer envelope travels in-memory and via streams.
    """
    type: str
    payload: dict = field(default_factory=dict)
    version: int = 1
    tenant_id: str = "default"
    idempotency_key: str | None = None
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)
    # v5.1 envelope context
    trace_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    workflow_id: str | None = None

    def __post_init__(self):
        if self.trace_id is None or self.correlation_id is None:
            try:
                from core.tracing import current_trace
                ctx = current_trace()
            except Exception:
                ctx = {"trace_id": "-", "correlation_id": "-"}
            self.trace_id = self.trace_id or ctx.get("trace_id", "-")
            self.correlation_id = self.correlation_id or ctx.get("correlation_id", "-")

    def envelope(self) -> dict:
        """Full metadata dict (for stream messages, logs, and consumers)."""
        return {
            "event_id": self.event_id, "type": self.type, "version": self.version,
            "tenant_id": self.tenant_id, "trace_id": self.trace_id,
            "correlation_id": self.correlation_id, "causation_id": self.causation_id,
            "workflow_id": self.workflow_id, "idempotency_key": self.idempotency_key,
        }

    def to_row(self) -> dict:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "version": self.version,
            "tenant_id": self.tenant_id,
            "idempotency_key": self.idempotency_key,
            "payload": json.dumps(self.payload, ensure_ascii=False),
        }

    @classmethod
    def from_row(cls, row: dict) -> "Event":
        return cls(
            type=row["type"],
            payload=json.loads(row["payload"]) if row.get("payload") else {},
            version=row.get("version", 1),
            tenant_id=row.get("tenant_id", "default"),
            idempotency_key=row.get("idempotency_key"),
            event_id=row["event_id"],
        )
