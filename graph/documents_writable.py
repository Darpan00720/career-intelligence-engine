"""Writable documents node (v6 convergence) — first artifact-generation stage.

Generates a tailored resume + cover letter per high-scoring job. Implements the
ADR's authoritative-artifacts principle (Model D2):

  * The paid Claude generation is gated by a content-hash triple
    (source/prompt/research) + version and persisted to the DB
    (``documents.content``) — the **DB is content-authoritative**.
  * The ``.md`` file is a **projection** of the DB content, materialized
    **write-only-if-missing**: a deleted file is restored for free (no Claude
    call), and a user's manual edits are never clobbered.
  * Versioning is **append-only**: a genuine content change creates a new
    version + new file; unchanged content is reused.

Graph-native (operates on ``state["scored_jobs"]``). Cost-gated: re-materializing
a missing file needs no key; only *new generation* requires ANTHROPIC_API_KEY.
Per-job partial-failure isolation; rate-limited.
"""
from __future__ import annotations

import os
from pathlib import Path

from core.logging_config import get_logger
from graph.providers import get_provider
from schemas.control import Phase

logger = get_logger(__name__)


def _threshold() -> int:
    return int(os.getenv("DOCUMENTS_MIN_SCORE", "80"))


def _materialize_if_missing(file_path: str | None, content: str | None) -> bool:
    """Project DB content to the file ONLY IF the file is missing. Never clobbers
    an existing file (preserves user edits). Writes atomically so a crash mid-
    write can never leave a truncated file at the canonical path. Returns True if
    it wrote."""
    from agents.document_agent import atomic_write_text

    if not file_path or content is None:
        return False
    path = Path(file_path)
    if path.exists():
        return False
    atomic_write_text(path, content)
    return True


def _process_doc(job, doc_type, prompt_name, counter, profile, research_ctx,
                 source_hash, research_hash, has_key, stats) -> None:
    from agents.document_agent import _build_message, _call_claude, _write_doc
    from core import database
    from core.prompt_loader import version

    prompt_hash = version(prompt_name)
    latest = database.get_latest_document(job["id"], doc_type)
    unchanged = bool(latest
                     and latest.get("source_hash") == source_hash
                     and latest.get("prompt_hash") == prompt_hash
                     and latest.get("research_hash") == research_hash)

    if unchanged:
        # Content is already authoritative in the DB; never re-pay Claude. The
        # file is a projection — restore it if missing, otherwise leave it be.
        content = latest.get("content")
        if content is None:
            stats["reused"] += 1          # legacy row, content not in DB → leave file
        elif _materialize_if_missing(latest.get("file_path"), content):
            stats["rematerialized"] += 1
        else:
            stats["reused"] += 1
        return

    # New or content changed → regenerate. This is the only path that pays Claude.
    if not has_key:
        stats["skipped_no_key"] += 1
        return
    new_version = (latest.get("version") or 0) + 1 if latest else 1
    content = _call_claude(_build_message(prompt_name, profile, job, research_ctx))
    path = _write_doc(job, doc_type, content, new_version)
    database.insert_document(
        job_id=job["id"], doc_type=doc_type, file_path=str(path), file_name=path.name,
        word_count=len(content.split()), version=new_version, langgraph_run_id="",
        prompt_version=prompt_hash, source_hash=source_hash, prompt_hash=prompt_hash,
        research_hash=research_hash, content=content,
    )
    stats[counter] += 1


def run_documents_stage(state: dict) -> dict:
    """Core documents logic (terminal stage). Returns documents_stats.

    Performs the DB-authoritative generation + write-only-if-missing file
    projection. No graph-node bookkeeping (the terminal runner owns
    phase/audit/EXECUTION_LOG); call ``documents_node`` for the standalone form.
    """
    from agents.document_agent import _CLAUDE_DELAY_SECONDS, _hash, _research_context
    from core.profile_loader import load as load_profile

    run_id = state.get("run_id")
    provider = get_provider(run_id)
    threshold = _threshold()

    candidates = [sj for sj in (state.get("scored_jobs") or [])
                  if (sj.total_score or 0) >= threshold]
    stats = {"eligible": len(candidates), "resumes": 0, "cover_letters": 0,
             "reused": 0, "rematerialized": 0, "skipped_no_key": 0,
             "errors": 0, "reason": ""}

    if not candidates:
        stats["reason"] = "no jobs above threshold"
        return stats

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    try:
        profile = load_profile()
    except Exception:
        profile = {}

    jobs = [j for j in (provider.get(sj.job_id) for sj in candidates) if j]

    def _one(job: dict) -> bool:
        research_ctx = _research_context(job["id"])
        source_hash = _hash(job.get("description"))
        research_hash = _hash(research_ctx)
        for doc_type, prompt_name, counter in (
            ("resume", "resume_prompt", "resumes"),
            ("cover_letter", "cover_letter_prompt", "cover_letters"),
        ):
            _process_doc(job, doc_type, prompt_name, counter, profile, research_ctx,
                         source_hash, research_hash, has_key, stats)
        return True

    from core.concurrency import parallel_map
    results = parallel_map(_one, jobs, max_workers=1, rate_limit=_CLAUDE_DELAY_SECONDS)
    for job, res in zip(jobs, results):
        if not res.ok:
            stats["errors"] += 1
            logger.warning("documents: job %s failed: %s", job.get("id"), res.error)

    if has_key is False and stats["skipped_no_key"]:
        stats["reason"] = "ANTHROPIC_API_KEY not set — new generation skipped"
    logger.info("documents: resumes=%d cover_letters=%d rematerialized=%d reused=%d",
                stats["resumes"], stats["cover_letters"], stats["rematerialized"],
                stats["reused"])
    return stats


def documents_node(state: dict) -> dict:
    """Standalone graph-node form of the documents stage (direct/unit use)."""
    from graph.nodes import _enter

    _enter("documents")
    return {"phase": Phase.DOCUMENTS, "documents_stats": run_documents_stage(state),
            "audit_log": ["documents"]}
