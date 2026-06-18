"""Autonomous Career Pipeline (main.py option 9).

Orchestrates the end-to-end flow and prints a progress line for every step:

    1. Search for new jobs
    2. Score all unscored jobs
    3. Rank all jobs
    4. Research approved companies
    5. Generate resume and cover letter
    6. Export jobs_master.xlsx
    7. Display top opportunities

The orchestrator owns no business logic — it calls the existing agents,
exporter, and ranking layer. Steps whose underlying agents are not yet
implemented (research, documents) are skipped with an explicit notice so the
pipeline still completes and produces jobs_master.xlsx, rather than printing a
misleading success.
"""
from __future__ import annotations

import time

from core import database
from core.event_log import log_event
from core.pipeline_run import PipelineRun


# ── Small DB count helpers ─────────────────────────────────────────────────────

def _count_jobs() -> int:
    with database.get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def _count_scored() -> int:
    with database.get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]


# ── Top Jobs view ───────────────────────────────────────────────────────────────

_TOP_COLUMNS: list[tuple[str, str, int]] = [
    ("rank",                 "Rank",     5),
    ("total_score",          "Score",    6),
    ("application_priority", "Priority", 13),
    ("company",              "Company",  22),
    ("title",                "Job Title", 40),
    ("location",             "Location", 20),
    ("application_status",   "Status",   16),
]


def _truncate(value, width: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def display_top_jobs(limit: int = 20, min_score: int = 70) -> list[dict]:
    """Print the top `limit` ranked opportunities as a console table.

    Columns: Rank, Score, Priority, Company, Job Title, Location, Status.
    By default only jobs scoring >= 70 are shown. Returns the rows displayed.
    """
    from core.ranking import top_jobs

    rows = [r for r in top_jobs(limit=None) if (r.get("total_score") or 0) >= min_score]
    rows = rows[:limit]
    for row in rows:
        row["application_status"] = row.get("application_status") or "Not Started"

    if not rows:
        print(f"No opportunities scoring >= {min_score} yet — run search and scoring first.")
        return rows

    header = "  ".join(label.ljust(width) for _, label, width in _TOP_COLUMNS)
    print(f"\nTop {len(rows)} opportunities (score >= {min_score})")
    print(header)
    print("─" * len(header))
    for row in rows:
        line = "  ".join(
            _truncate(row.get(key), width).ljust(width)
            for key, _, width in _TOP_COLUMNS
        )
        print(line)
    print()
    return rows


# ── Recommendation dashboard (v3) ────────────────────────────────────────────────

_REC_COLUMNS: list[tuple[str, str, int]] = [
    ("rank",                 "Rank",           5),
    ("total_score",          "Score",          6),
    ("application_priority", "Priority",       12),
    ("recommendation",       "Recommendation", 18),
    ("company",              "Company",        20),
    ("title",                "Role",           36),
    ("location",             "Location",       18),
    ("application_status",   "Status",         14),
]


def _recommendation_rows(limit: int, min_score: int) -> list[dict]:
    from core.ranking import rank_jobs
    from core.recommendation_engine import recommend_all, sort_for_dashboard

    try:
        from core.profile_loader import load as _load_profile
        profile = _load_profile()
    except Exception:
        profile = None

    rows = rank_jobs(database.get_all_scored_jobs_ranked())
    rows = recommend_all(rows, profile=profile,
                         research_job_ids=database.get_researched_job_ids())
    rows = [r for r in rows if (r.get("total_score") or 0) >= min_score]
    rows = sort_for_dashboard(rows)[:limit]
    for r in rows:
        r["application_status"] = r.get("application_status") or "Not Started"
    return rows


def display_recommendations(limit: int = 20, min_score: int = 70) -> list[dict]:
    """Print the recommendation dashboard: Rank, Score, Priority, Recommendation,
    Company, Role, Location, Status. Filtered to score >= min_score, sorted by
    recommendation priority then score DESC then date DESC. Returns the rows."""
    rows = _recommendation_rows(limit, min_score)
    if not rows:
        print(f"No recommendations (no jobs scoring >= {min_score} yet).")
        return rows

    header = "  ".join(label.ljust(width) for _, label, width in _REC_COLUMNS)
    print(f"\nRecommendation dashboard (score >= {min_score})")
    print(header)
    print("─" * len(header))
    for row in rows:
        print("  ".join(
            _truncate(row.get(key), width).ljust(width)
            for key, _, width in _REC_COLUMNS
        ))
    print()
    return rows


# ── Autonomous pipeline ─────────────────────────────────────────────────────────

def run_autonomous_pipeline(top_n: int = 20, research_min_score: int = 80) -> dict:
    """Run the full autonomous career-intelligence pipeline:

    search → deduplicate → score → rank → research → documents → export →
    update tracker → display top opportunities.

    Each step prints a progress line and the run never aborts on a single step's
    failure. Returns a summary dict of what happened in each step.
    """
    run = PipelineRun.start()
    summary: dict = {}
    errors: list[str] = []
    t0 = time.perf_counter()
    print("\nAutonomous Career Intelligence Pipeline")
    print("═" * 48)

    def _timed(step: str, fn):
        """Run a step, time it, log it, and record failures without aborting."""
        start = time.perf_counter()
        status = "ok"
        try:
            return fn()
        except Exception as exc:   # noqa: BLE001 - pipeline must not abort
            status = "error"
            errors.append(step)
            print(f"  [warn] {step} step failed: {exc}")
            log_event("pipeline", step, duration=time.perf_counter() - start,
                      status=status, error=str(exc), level="ERROR")
            return None
        finally:
            if status == "ok":
                log_event("pipeline", step, duration=time.perf_counter() - start,
                          status=status)

    # ── Step 1: Search (deduplication happens inside the search agent) ──────────
    print("\nSearching for jobs…")
    s = time.perf_counter()
    _timed("search", lambda: __import__("agents.search_agent", fromlist=["run"]).run())
    run.jobs_found = _count_jobs()
    summary["jobs_found"] = run.jobs_found
    print(f"✓ {run.jobs_found} jobs found ({time.perf_counter() - s:.1f}s)")
    print("✓ Duplicates filtered (URL + content fingerprint)")

    # ── Step 2: Score ───────────────────────────────────────────────────────────
    print("\nScoring jobs…")
    s = time.perf_counter()
    _timed("score", lambda: __import__("agents.scoring_agent", fromlist=["run"]).run())
    run.jobs_scored = _count_scored()
    summary["jobs_scored"] = run.jobs_scored
    print(f"✓ {run.jobs_scored} jobs scored ({time.perf_counter() - s:.1f}s)")

    # ── Step 3: Rank ────────────────────────────────────────────────────────────
    print("\nRanking jobs…")
    s = time.perf_counter()
    from core.ranking import rank_jobs
    ranked = rank_jobs(database.get_all_scored_jobs_ranked())
    summary["jobs_ranked"] = len(ranked)
    print(f"✓ Ranking complete ({time.perf_counter() - s:.1f}s)")

    # ── Step 4: Research approved companies (cached; score >= threshold) ────────
    print("\nResearching companies…")
    s = time.perf_counter()
    research = _timed(
        "research",
        lambda: __import__("agents.research_agent", fromlist=["run"]).run(min_score=research_min_score),
    ) or {}
    summary["research"] = research
    run.companies_researched = research.get("researched", 0)
    print(f"✓ {run.companies_researched} companies researched "
          f"({research.get('skipped_fresh', 0)} cached) ({time.perf_counter() - s:.1f}s)")

    # ── Step 5: Generate resume and cover letter (versioned; score >= threshold) ─
    print("\nGenerating documents…")
    s = time.perf_counter()
    docs = _timed(
        "documents",
        lambda: __import__("agents.document_agent", fromlist=["run"]).run(min_score=research_min_score),
    ) or {}
    summary["documents"] = docs
    run.documents_generated = docs.get("resumes", 0) + docs.get("cover_letters", 0)
    print(f"✓ {run.documents_generated} documents generated "
          f"({docs.get('reused', 0)} reused) ({time.perf_counter() - s:.1f}s)")

    # ── Step 6: Export multi-sheet dashboard ────────────────────────────────────
    print("\nExporting dashboard…")
    s = time.perf_counter()
    from core.exporter import export_jobs_master_xlsx
    path = _timed("export", export_jobs_master_xlsx)
    summary["export_path"] = path
    print(f"✓ {path} created ({time.perf_counter() - s:.1f}s)")

    # ── Step 7: Update application tracker ──────────────────────────────────────
    print("\nUpdating application tracker…")
    s = time.perf_counter()
    from core.tracker import ensure_rows_for_scored
    created = _timed("tracker", ensure_rows_for_scored) or 0
    run.applications_updated = created
    summary["tracker_rows_created"] = created
    print(f"✓ Application tracker updated ({created} new) ({time.perf_counter() - s:.1f}s)")

    # ── Step 8: Display top opportunities + recommendation dashboard ────────────
    print("\nDisplaying top opportunities…")
    display_top_jobs(limit=top_n)
    display_recommendations(limit=top_n)
    print("✓ Top opportunities displayed")

    # ── Persist the run ─────────────────────────────────────────────────────────
    if "export" in errors:
        run_status = PipelineRun.FAILED
    elif errors:
        run_status = PipelineRun.PARTIAL_SUCCESS
    else:
        run_status = PipelineRun.SUCCESS
    run.detail = {
        "errors": errors,
        "research_researched": research.get("researched", 0),
        "research_cached": research.get("skipped_fresh", 0),
        "documents_generated": docs.get("resumes", 0) + docs.get("cover_letters", 0),
        "documents_reused": docs.get("reused", 0),
    }
    run.finalize(run_status)
    try:
        run.save()
    except Exception as exc:   # pragma: no cover - persistence is best-effort
        print(f"  [warn] could not persist pipeline run: {exc}")
    summary["run_id"] = run.run_id
    summary["status"] = run.status

    total = time.perf_counter() - t0
    summary["duration_seconds"] = round(total, 1)
    log_event("pipeline", "pipeline_complete", status=run.status, duration=total)
    print(f"\nPipeline completed ({run.status}).")
    print(f"Total runtime: {total:.1f}s")
    return summary
