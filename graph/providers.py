"""Offline-capable job sources + per-run registry (seed/fixture/test-mode).

Nodes never touch the network directly. They resolve a JobProvider by run_id
from this registry. Tests register a SeedJobProvider before invoking; the
default is a bounded, READ-ONLY DbJobProvider over data/career_agent.db.

A registry (keyed by run_id) is used instead of config["configurable"] so we
never put non-serializable objects into checkpointed state/config.
"""
from __future__ import annotations

import sqlite3
from typing import Protocol

from core.config import DB_PATH
from core.logging_config import get_logger

logger = get_logger(__name__)

# Columns score_job + gating need. role_category is re-derived by the taxonomy
# node, so the value read here is informational only.
_COLS = (
    "id, title, company, location, description, url, role_category, "
    "language_gate, visa_gate, language_rejection_reason, visa_rejection_reason, "
    "eligibility_status, visa_accessibility"
)


class JobProvider(Protocol):
    def fetch(self) -> list[dict]: ...
    def get(self, job_id: int) -> dict | None: ...


class DbJobProvider:
    """Bounded, read-only provider over career_agent.db. No writes, ever."""

    def __init__(self, limit: int = 10, db_path: str | None = None):
        self.limit = limit
        self.db_path = str(db_path or DB_PATH)

    def _ro_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def fetch(self) -> list[dict]:
        conn = self._ro_conn()
        try:
            rows = conn.execute(
                f"SELECT {_COLS} FROM jobs "
                f"WHERE is_expired = 0 ORDER BY id LIMIT ?",
                (self.limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get(self, job_id: int) -> dict | None:
        conn = self._ro_conn()
        try:
            row = conn.execute(
                f"SELECT {_COLS} FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


class SeedJobProvider:
    """In-memory provider for deterministic, offline tests. Holds full dicts."""

    def __init__(self, seed_jobs: list[dict]):
        self._jobs = [dict(j) for j in seed_jobs]
        self._by_id = {j["id"]: j for j in self._jobs}

    def fetch(self) -> list[dict]:
        return [dict(j) for j in self._jobs]

    def get(self, job_id: int) -> dict | None:
        j = self._by_id.get(job_id)
        return dict(j) if j else None


# --- per-run registry ------------------------------------------------------ #
_PROVIDERS: dict[str, JobProvider] = {}


def set_provider(run_id: str, provider: JobProvider) -> None:
    _PROVIDERS[run_id] = provider


def get_provider(run_id: str | None) -> JobProvider:
    if run_id and run_id in _PROVIDERS:
        return _PROVIDERS[run_id]
    return DbJobProvider()


def clear_provider(run_id: str) -> None:
    _PROVIDERS.pop(run_id, None)


# --- optional Claude hook (default OFF; mockable in tests) ------------------ #
_CLAUDE_HOOKS: dict[str, object] = {}


def set_claude_hook(run_id: str, fn) -> None:
    """fn(job: dict, det) -> ClaudeScoreAdjustment. None/absent = deterministic only."""
    _CLAUDE_HOOKS[run_id] = fn


def get_claude_hook(run_id: str | None):
    return _CLAUDE_HOOKS.get(run_id) if run_id else None


def clear_claude_hook(run_id: str) -> None:
    _CLAUDE_HOOKS.pop(run_id, None)


# --- optional Opportunity-Intelligence LLM hook (separate signature) -------- #
# fn(profile, job, score) -> IntelLLMAssessment. None = deterministic fallback.
_INTEL_HOOKS: dict[str, object] = {}


def set_intel_hook(run_id: str, fn) -> None:
    _INTEL_HOOKS[run_id] = fn


def get_intel_hook(run_id: str | None):
    return _INTEL_HOOKS.get(run_id) if run_id else None


def clear_intel_hook(run_id: str) -> None:
    _INTEL_HOOKS.pop(run_id, None)
