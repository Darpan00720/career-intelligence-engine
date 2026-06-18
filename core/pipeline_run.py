"""Pipeline run tracking (v3).

A small dataclass capturing one autonomous-pipeline execution, persisted to the
pipeline_runs table. Idempotent on run_id (re-saving updates the same row).

    run = PipelineRun.start()
    ...
    run.jobs_found = 200
    run.finalize(PipelineRun.SUCCESS)
    run.save()
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from core import database

# Terminal statuses
SUCCESS = "SUCCESS"
PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
FAILED = "FAILED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PipelineRun:
    run_id: str
    started_at: str
    completed_at: str | None = None
    jobs_found: int = 0
    jobs_scored: int = 0
    companies_researched: int = 0
    documents_generated: int = 0
    applications_updated: int = 0
    status: str | None = None
    duration_seconds: float | None = None
    detail: dict = field(default_factory=dict)

    # Expose status constants on the class for callers.
    SUCCESS = SUCCESS
    PARTIAL_SUCCESS = PARTIAL_SUCCESS
    FAILED = FAILED

    @classmethod
    def start(cls) -> "PipelineRun":
        return cls(run_id=uuid.uuid4().hex, started_at=_now().isoformat())

    def finalize(self, status: str) -> "PipelineRun":
        end = _now()
        self.completed_at = end.isoformat()
        self.status = status
        try:
            start = datetime.fromisoformat(self.started_at)
            self.duration_seconds = round((end - start).total_seconds(), 2)
        except ValueError:
            self.duration_seconds = None
        return self

    def to_row(self) -> dict:
        row = asdict(self)
        row["detail"] = json.dumps(self.detail, ensure_ascii=False)
        return row

    def save(self) -> int:
        return database.insert_pipeline_run(self.to_row())


def recent(limit: int = 20) -> list[dict]:
    """Return recent persisted runs (newest first)."""
    return database.get_pipeline_runs(limit)
