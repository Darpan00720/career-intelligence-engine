"""Human Review Queue (v4).

Before an application is submitted, the AI-generated resume, cover letter, and
recommendation are queued for human review. A reviewer can approve, reject, or
request regeneration. Backed by the review_queue table (no in-memory state).

States: Pending Review → Approved | Rejected | Regenerate Requested.
"""
from __future__ import annotations

from core import database

PENDING = "Pending Review"
APPROVED = "Approved"
REJECTED = "Rejected"
REGENERATE = "Regenerate Requested"

STATES = [PENDING, APPROVED, REJECTED, REGENERATE]


class InvalidReviewState(ValueError):
    pass


def enqueue(job_id: int, recommendation: str | None = None,
            resume_doc_id: int | None = None,
            cover_letter_doc_id: int | None = None) -> int:
    """Add a job's generated materials to the review queue (Pending Review)."""
    return database.enqueue_review(job_id, recommendation, resume_doc_id, cover_letter_doc_id)


def _transition(review_id: int, state: str, notes: str | None = None) -> dict:
    if state not in STATES:
        raise InvalidReviewState(f"Unknown state: {state!r}. Valid: {STATES}")
    if database.get_review(review_id) is None:
        raise KeyError(f"No review with id {review_id}")
    database.update_review_state(review_id, state, notes)
    return database.get_review(review_id)


def approve(review_id: int, notes: str | None = None) -> dict:
    return _transition(review_id, APPROVED, notes)


def reject(review_id: int, notes: str | None = None) -> dict:
    return _transition(review_id, REJECTED, notes)


def request_regeneration(review_id: int, notes: str | None = None) -> dict:
    return _transition(review_id, REGENERATE, notes)


def get(review_id: int) -> dict | None:
    return database.get_review(review_id)


def pending() -> list[dict]:
    return database.list_reviews(PENDING)


def all_reviews(state: str | None = None) -> list[dict]:
    return database.list_reviews(state)
