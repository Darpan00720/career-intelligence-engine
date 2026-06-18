"""Event-driven architecture (v5).

Public API: Event, EventBus, the default get_bus(), and core event-type names.
"""
from events.types import (
    APPLICATION_UPDATED,
    DOCUMENTS_GENERATED,
    JOB_DISCOVERED,
    JOB_SCORED,
    NOTIFICATION_REQUESTED,
    RECOMMENDATION_GENERATED,
    RESEARCH_COMPLETED,
    WORKFLOW_COMPLETED,
    WORKFLOW_FAILED,
    CORE_EVENTS,
    Event,
)
from events.bus import DeadLetterQueue, EventBus, get_bus, reset_bus

__all__ = [
    "Event", "EventBus", "DeadLetterQueue", "get_bus", "reset_bus",
    "CORE_EVENTS", "JOB_DISCOVERED", "JOB_SCORED", "RESEARCH_COMPLETED",
    "DOCUMENTS_GENERATED", "RECOMMENDATION_GENERATED", "APPLICATION_UPDATED",
    "WORKFLOW_COMPLETED", "WORKFLOW_FAILED", "NOTIFICATION_REQUESTED",
]
