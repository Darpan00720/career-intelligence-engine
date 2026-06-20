"""Tracker terminal stage (v6) — persistence only, idempotent.

No new replay model: ``ensure_rows_for_scored`` inserts a 'Not Started'
application row for each scored job that lacks one (insert-if-missing), so a
re-run creates zero new rows. Never overwrites existing application status.
"""
from __future__ import annotations

from core.logging_config import get_logger

logger = get_logger(__name__)


def run_tracker_stage(state: dict | None = None) -> dict:
    """Ensure a tracker row exists for every scored job. Returns {created}."""
    from core import tracker

    created = tracker.ensure_rows_for_scored()
    logger.info("tracker: %d new application row(s)", created)
    return {"created": created}
