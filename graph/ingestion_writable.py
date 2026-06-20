"""Writable job-acquisition node (v6 convergence — first write-capable node).

This is the first concrete step toward letting the LangGraph graph subsume the
legacy ``run_autonomous_pipeline`` acquisition step. Unlike the existing
``job_ingestion_node`` (which reads jobs already in the DB via the *read-only*
``DbJobProvider``), this node **acquires new jobs from external sources and
persists them**.

Idempotency — the whole reason this can live safely inside a checkpointed graph
— is layered:

1. **Row-level (correctness).** Persistence delegates to
   ``agents.search_agent._process_job``, which dedups by content fingerprint
   (``dedup_hash``) and by the UNIQUE ``url`` column, returning the existing row
   id instead of inserting. A retried/replayed node therefore *cannot* create
   duplicate rows, even across different runs.
2. **Run-level (efficiency / replay guard).** Before doing any work the node
   records a marker keyed by ``(run_id, source_signature)`` in ``ingestion_runs``.
   On a LangGraph checkpoint replay or node retry the marker is found and the
   network fetch + persistence are skipped entirely — the node just re-registers
   the read-only provider so downstream nodes see the same data.

Source acquisition is injected (mirroring ``graph.providers``): production uses
``WatchlistSource`` (ATS connectors); tests register a ``SeedRawSource`` for a
deterministic, offline batch. Persistence side effects are deliberate and live
*outside* state; the node writes only schema objects (``IngestionStats``) +
``audit_log`` into state, honouring the node-boundary rules in ``graph/nodes.py``.

NOTE: intentionally NOT wired into ``graph/build.py`` yet. Wiring it (and the
design decision to allow side effects in the graph) is the follow-up step; see
the module docstring of ``graph/nodes.py`` for the existing boundary rules.
"""
from __future__ import annotations

import hashlib
import json
from typing import Protocol, runtime_checkable

from core import config, database
from core.logging_config import get_logger
from core.settings import settings
from schemas.control import Phase
from schemas.jobs import IngestionStats

logger = get_logger(__name__)


# ── Raw job sources (injectable, mirrors graph.providers) ───────────────────────

@runtime_checkable
class RawJobSource(Protocol):
    """Yields raw, un-persisted job dicts (the shape produced by the ATS
    normalisers in agents.search_agent)."""
    def fetch(self) -> list[dict]: ...


class WatchlistSource:
    """Production default: fetch raw jobs from the ATS company watchlist.

    Reuses the existing greenhouse/lever connectors. Performs NO persistence —
    persistence is the node's job (so dedup/gating run in one place)."""

    def fetch(self) -> list[dict]:
        from agents import apify_search
        from agents.search_agent import fetch_company

        out: list[dict] = []
        # ATS company boards (greenhouse/lever/ashby/smartrecruiters) from the watchlist.
        path = config.BASE_DIR / "data" / "company_watchlist.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for company in data.get("companies", []):
                out += fetch_company(company)
        else:
            logger.warning("watchlist not found at %s", path)
        # Public boards (LinkedIn/Indeed/WTTJ via Apify) — no-op without apify-client/key.
        try:
            from core.profile_loader import load as load_profile
            out += apify_search.search_public_boards(load_profile())
        except Exception as exc:  # public-board search is best-effort
            logger.warning("public-board search failed: %s", exc)
        return out


class SeedRawSource:
    """Deterministic, offline source for tests / fixtures."""

    def __init__(self, raw_jobs: list[dict]):
        self._jobs = [dict(j) for j in raw_jobs]

    def fetch(self) -> list[dict]:
        return [dict(j) for j in self._jobs]


_RAW_SOURCES: dict[str, RawJobSource] = {}


def set_raw_source(run_id: str, source: RawJobSource) -> None:
    _RAW_SOURCES[run_id] = source


def get_raw_source(run_id: str | None) -> RawJobSource:
    if run_id and run_id in _RAW_SOURCES:
        return _RAW_SOURCES[run_id]
    return WatchlistSource()


def clear_raw_source(run_id: str) -> None:
    _RAW_SOURCES.pop(run_id, None)


# ── Run-level idempotency marker ────────────────────────────────────────────────

def _ensure_marker_table(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ingestion_runs (
               run_id           TEXT NOT NULL,
               source_signature TEXT NOT NULL,
               fetched          INTEGER,
               accepted         INTEGER,
               duplicates       INTEGER,
               created_at       DATETIME DEFAULT (DATETIME('now')),
               PRIMARY KEY (run_id, source_signature)
           )"""
    )


def _source_signature(raw_jobs: list[dict]) -> str:
    """Stable fingerprint of the candidate batch. Identical re-fetches (e.g. on
    replay) produce the same signature so the run-level guard trips."""
    parts = sorted(
        f"{(j.get('company') or '').strip().lower()}|"
        f"{(j.get('title') or '').strip().lower()}|"
        f"{(j.get('url') or '').strip().lower()}"
        for j in raw_jobs
    )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def _already_ingested(run_id: str, signature: str) -> dict | None:
    with database.get_connection() as conn:
        _ensure_marker_table(conn)
        row = conn.execute(
            "SELECT fetched, accepted, duplicates FROM ingestion_runs "
            "WHERE run_id = ? AND source_signature = ?",
            (run_id, signature),
        ).fetchone()
    return dict(row) if row else None


def _mark_ingested(run_id: str, signature: str, stats: dict) -> None:
    with database.get_connection() as conn:
        _ensure_marker_table(conn)
        conn.execute(
            "INSERT OR REPLACE INTO ingestion_runs "
            "(run_id, source_signature, fetched, accepted, duplicates) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, signature, stats.get("fetched", 0),
             stats.get("accepted", 0), stats.get("duplicates", 0)),
        )


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _register_readonly_provider(run_id: str) -> None:
    """Point the (read-only) DbJobProvider at the freshly-written rows so the
    existing ``job_ingestion_node`` projects exactly what we acquired."""
    from graph.providers import DbJobProvider, set_provider

    set_provider(run_id, DbJobProvider(limit=settings.db_job_limit,
                                       db_path=str(config.DB_PATH)))


def _exclude_keywords() -> list[str]:
    try:
        from core.profile_loader import load as load_profile
        profile = load_profile() or {}
        return profile.get("application_preferences", {}).get("exclude_title_keywords", [])
    except Exception:  # profile is optional for acquisition
        return []


def _stats_to_schema(stats: dict) -> IngestionStats:
    accepted = stats.get("accepted", 0)
    duplicates = stats.get("duplicates", 0)
    fetched = stats.get("fetched", 0)
    reasons = {
        "excluded":  stats.get("rejected_excluded", 0),
        "seniority": stats.get("rejected_seniority", 0),
        "geography": stats.get("rejected_geography", 0),
        "language":  stats.get("rejected_language", 0),
        "other":     stats.get("rejected_other", 0),
    }
    reasons = {k: v for k, v in reasons.items() if v}
    return IngestionStats(
        fetched=fetched,
        passed=accepted + duplicates,        # rows now available downstream
        rejected=max(fetched - accepted - duplicates, 0),
        rejected_by_reason=reasons,
    )


# ── The node ─────────────────────────────────────────────────────────────────────

def acquire_jobs_node(state: dict) -> dict:
    """Acquire + persist jobs idempotently, then register the read-only provider.

    Returns a partial state update (``ingestion_stats`` + ``audit_log``). The DB
    write and provider registration are deliberate side effects performed once
    per (run_id, source) and safe to replay.
    """
    from agents.search_agent import _process_job
    from graph.nodes import _enter

    _enter("acquire_jobs")  # register in EXECUTION_LOG like every peer node
    run_id = state.get("run_id") or "default"
    source = get_raw_source(run_id)
    raw_jobs = source.fetch()
    signature = _source_signature(raw_jobs)
    logger.info("acquire_jobs: run=%s fetched=%d sig=%s", run_id, len(raw_jobs), signature)

    # Run-level guard: this run already ingested this exact batch → skip work.
    prior = _already_ingested(run_id, signature)
    if prior is not None:
        logger.info("acquire_jobs: idempotent skip (already ingested) run=%s", run_id)
        _register_readonly_provider(run_id)
        return {
            "phase": Phase.ACQUISITION,
            "ingestion_stats": _stats_to_schema(prior),
            "audit_log": ["acquire_jobs(skipped:already-ingested)"],
        }

    # First time for this (run, batch): persist via the row-idempotent processor.
    exclude_keywords = _exclude_keywords()
    stats: dict = {}
    for raw in raw_jobs:
        try:
            _process_job(raw, exclude_keywords, stats)  # dedups + inserts; safe to repeat
        except Exception as exc:  # one bad row must not abort acquisition
            logger.warning("acquire_jobs: row failed (%s): %s", raw.get("url"), exc)
            stats["errors"] = stats.get("errors", 0) + 1

    _mark_ingested(run_id, signature, stats)
    _register_readonly_provider(run_id)
    logger.info("acquire_jobs: accepted=%d duplicates=%d", stats.get("accepted", 0),
                stats.get("duplicates", 0))
    return {
        "phase": Phase.ACQUISITION,
        "ingestion_stats": _stats_to_schema(stats),
        "audit_log": ["acquire_jobs"],
    }
