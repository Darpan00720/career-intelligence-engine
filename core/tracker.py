"""Application tracker — a lightweight job-application CRM over the existing
`applications` table. No schema change: it reuses the table's `status`/`notes`
columns and adds a validated status lifecycle plus convenience operations used
by the CLI (main.py option 6) and the autonomous pipeline.
"""
from __future__ import annotations

from core import database

# Canonical application lifecycle. Order reflects funnel progression; the first
# entry is the default for newly tracked jobs.
STATUSES: list[str] = [
    "Not Started",
    "Saved",
    "Applied",
    "Online Assessment",
    "Interview",
    "Final Round",
    "Offer",
    "Rejected",
]

DEFAULT_STATUS = STATUSES[0]


class InvalidStatusError(ValueError):
    """Raised when a status outside STATUSES is supplied."""


def is_valid_status(status: str) -> bool:
    return status in STATUSES


def ensure_tracked(job_id: int, status: str = DEFAULT_STATUS) -> bool:
    """Ensure a job has an application row. Returns True if newly created."""
    if not is_valid_status(status):
        raise InvalidStatusError(f"Unknown status: {status!r}. Valid: {STATUSES}")
    return database.ensure_application_row(job_id, status)


def set_status(job_id: int, status: str, notes: str | None = None) -> None:
    """Update a job's application status (creating the row first if needed)."""
    if not is_valid_status(status):
        raise InvalidStatusError(f"Unknown status: {status!r}. Valid: {STATUSES}")
    database.ensure_application_row(job_id, DEFAULT_STATUS)
    database.update_application(job_id, status=status, notes=notes)


def ensure_rows_for_scored() -> int:
    """Create 'Not Started' application rows for every scored job that lacks one.

    Used by the autonomous pipeline so the tracker always covers the full
    opportunity set. Returns the number of rows created.
    """
    created = 0
    for row in database.get_all_scored_jobs_ranked():
        if database.ensure_application_row(row["id"], DEFAULT_STATUS):
            created += 1
    return created


def list_applications() -> list[dict]:
    """Return all tracked applications with job/score context (score DESC)."""
    return database.get_applications_overview()


def status_counts() -> dict[str, int]:
    """Return a {status: count} map across all tracked applications."""
    counts = {s: 0 for s in STATUSES}
    for row in list_applications():
        st = row.get("status") or DEFAULT_STATUS
        counts[st] = counts.get(st, 0) + 1
    return counts
