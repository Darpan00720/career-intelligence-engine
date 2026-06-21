"""Pipeline observability metrics — read-only aggregation over the DB + the last
run's in-memory stats. No writes, no business logic; reuses core.database and
core.analytics. Powers /api/v2/metrics/* and the dashboard.
"""
from __future__ import annotations

from core import database

# ── Last-run stats (per-run, ephemeral) ─────────────────────────────────────────
# Captured by services.career_service after a graph run returns. Process-global,
# reflects the most recent run — enough for a "current run" dashboard.
_LAST_RUN: dict = {}


def record_run(state: dict) -> None:
    """Snapshot the per-run *_stats from a finished graph state. Additive,
    read-only w.r.t. the graph; called from the service layer post-invoke."""
    terminal = state.get("terminal_stats") or {}
    _LAST_RUN.clear()
    _LAST_RUN.update({
        "run_id": state.get("run_id"),
        "ingestion": _as_dict(state.get("ingestion_stats")),
        "prefilter": dict(state.get("prefilter_stats") or {}),
        "scoring": _as_dict(state.get("scoring_stats")),
        "research": dict(state.get("research_stats") or {}),
        "documents": dict(terminal.get("documents") or {}),
        "export": dict(terminal.get("export") or {}),
        "tracker": dict(terminal.get("tracker") or {}),
        "recommendations": len(state.get("recommendations") or []),
    })


def last_run() -> dict:
    return dict(_LAST_RUN)


def _as_dict(obj) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return {}


# ── Score distribution (DB) ─────────────────────────────────────────────────────

def _scores() -> list[int]:
    with database.get_connection() as conn:
        return [int(r[0]) for r in conn.execute(
            "SELECT total_score FROM scores WHERE total_score IS NOT NULL").fetchall()]


def _percentile(sorted_vals: list[int], q: float) -> int:
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def _top_companies(limit: int = 10) -> list[dict]:
    with database.get_connection() as conn:
        rows = conn.execute(
            """SELECT j.company, MAX(s.total_score) AS sc
               FROM scores s JOIN jobs j ON j.id = s.job_id
               WHERE j.company IS NOT NULL AND j.company != ''
               GROUP BY j.company ORDER BY sc DESC LIMIT ?""", (limit,)).fetchall()
    return [{"company": r[0], "score": int(r[1])} for r in rows]


def scoring_metrics() -> dict:
    s = sorted(_scores())
    n = len(s)
    buckets = {"0-20": 0, "20-40": 0, "40-60": 0, "60-70": 0, "70-80": 0, "80+": 0}
    for v in s:
        if v < 20:   buckets["0-20"] += 1
        elif v < 40: buckets["20-40"] += 1
        elif v < 60: buckets["40-60"] += 1
        elif v < 70: buckets["60-70"] += 1
        elif v < 80: buckets["70-80"] += 1
        else:        buckets["80+"] += 1
    return {
        "jobs_scored": n,
        "average_score": round(sum(s) / n, 1) if n else 0,
        "p50": _percentile(s, 0.50), "p75": _percentile(s, 0.75),
        "p90": _percentile(s, 0.90), "p95": _percentile(s, 0.95),
        "jobs_score_ge_70": sum(1 for v in s if v >= 70),
        "jobs_score_ge_80": sum(1 for v in s if v >= 80),
        "distribution": buckets,
        "top_companies_by_score": _top_companies(),
    }


# ── Taxonomy + source performance (DB) ──────────────────────────────────────────

def taxonomy_metrics() -> dict:
    with database.get_connection() as conn:
        cats = {(r[0] or "unknown"): r[1] for r in conn.execute(
            "SELECT role_category, COUNT(*) FROM jobs GROUP BY role_category").fetchall()}
        tracks = {(r[0] or "?"): r[1] for r in conn.execute(
            "SELECT track, COUNT(*) FROM jobs GROUP BY track").fetchall()}
    return {"category_distribution": cats, "unknown_classifications": cats.get("unknown", 0),
            "track_a": tracks.get("A", 0), "track_b": tracks.get("B", 0)}


def source_performance() -> dict:
    with database.get_connection() as conn:
        rows = conn.execute(
            "SELECT COALESCE(job_board,'unknown'), COUNT(*) FROM jobs "
            "GROUP BY job_board ORDER BY 2 DESC").fetchall()
    return {r[0]: r[1] for r in rows}


# ── Acquisition + funnel ────────────────────────────────────────────────────────

def acquisition_metrics() -> dict:
    """Source performance (all-time, DB) + this run's acquisition/prefilter stats."""
    lr = last_run()
    return {
        "jobs_per_source": source_performance(),
        "last_run_acquisition": lr.get("ingestion", {}),
        "last_run_prefilter": lr.get("prefilter", {}),
    }


def _count(table: str, distinct_col: str | None = None) -> int:
    col = f"DISTINCT {distinct_col}" if distinct_col else "*"
    with database.get_connection() as conn:
        return conn.execute(f"SELECT COUNT({col}) FROM {table}").fetchone()[0]


def pipeline_funnel() -> dict:
    """Acquisition→application funnel with counts + conversion %. Fetched/Kept come
    from the last run (state-only); the rest from the DB."""
    lr = last_run()
    fetched = (lr.get("ingestion", {}) or {}).get("fetched")
    kept = (lr.get("prefilter", {}) or {}).get("kept")
    scored = _count("scores")
    researched = _count("company_research", "job_id")
    documents = _count("documents", "job_id")
    applications = _count("applications")
    # Fall back to scored when no run has populated the state-only stages yet.
    fetched = fetched if fetched is not None else scored
    kept = kept if kept is not None else scored

    stages = [("Fetched", fetched), ("Prefilter Kept", kept), ("Scored", scored),
              ("Research Eligible", researched), ("Documents", documents),
              ("Applications Added", applications)]
    base = stages[0][1] or 1
    funnel = []
    prev = None
    for name, count in stages:
        funnel.append({
            "stage": name, "count": count,
            "pct_of_fetched": round(100.0 * count / base, 1) if base else 0.0,
            "pct_of_prev": round(100.0 * count / prev, 1) if prev else 100.0,
        })
        prev = count or prev
    return {"funnel": funnel}


# ── Bundle for the dashboard ────────────────────────────────────────────────────

def dashboard_data() -> dict:
    from core import analytics
    return {
        "last_run": last_run(),
        "funnel": pipeline_funnel()["funnel"],
        "scoring": scoring_metrics(),
        "taxonomy": taxonomy_metrics(),
        "source_performance": source_performance(),
        "documents": document_metrics(),
        "tracker": analytics.status_breakdown(),
    }


def document_metrics() -> dict:
    with database.get_connection() as conn:
        by_type = {r[0]: r[1] for r in conn.execute(
            "SELECT type, COUNT(*) FROM documents GROUP BY type").fetchall()}
    return {"resumes_generated": by_type.get("resume", 0),
            "cover_letters_generated": by_type.get("cover_letter", 0)}
