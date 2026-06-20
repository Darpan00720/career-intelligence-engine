"""v4 CRM REST API routes — jobs, recommendations, analytics, applications,
and pipeline control. Pagination / filtering / sorting on list endpoints.

Thin handlers: validate → call core/service → return Pydantic schema. Mounted
under /api so the existing LangGraph routes are untouched.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import authenticate
from api.crm_models import MessageResponse, Page, PipelineRunRequest, StatusUpdate

# Auth is enforced on the whole CRM surface when API_KEYS is configured; in open
# (dev) mode the dependency is a no-op so existing clients are unaffected.
router = APIRouter(prefix="/api", tags=["crm"], dependencies=[Depends(authenticate)])

_SORT_FIELDS = {"total_score", "fetched_date", "company", "rank"}


def _paginate(rows: list[dict], limit: int, offset: int) -> Page:
    total = len(rows)
    return Page(items=rows[offset:offset + limit], total=total, limit=limit, offset=offset)


def _ranked_rows() -> list[dict]:
    from core.ranking import rank_jobs
    from core import database
    return rank_jobs(database.get_all_scored_jobs_ranked())


@router.get("/jobs", response_model=Page)
def list_jobs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    min_score: int = Query(0, ge=0, le=100),
    company: str | None = None,
    priority: str | None = None,
    sort: str = Query("total_score"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
) -> Page:
    rows = _ranked_rows()
    if min_score:
        rows = [r for r in rows if (r.get("total_score") or 0) >= min_score]
    if company:
        rows = [r for r in rows if company.lower() in (r.get("company") or "").lower()]
    if priority:
        rows = [r for r in rows if r.get("application_priority") == priority]
    if sort not in _SORT_FIELDS:
        raise HTTPException(400, f"sort must be one of {sorted(_SORT_FIELDS)}")
    rows.sort(key=lambda r: (r.get(sort) is None, r.get(sort)), reverse=(order == "desc"))
    return _paginate(rows, limit, offset)


@router.get("/jobs/top", response_model=Page)
def top_jobs(limit: int = Query(20, ge=1, le=200), min_score: int = Query(70, ge=0, le=100)) -> Page:
    rows = [r for r in _ranked_rows() if (r.get("total_score") or 0) >= min_score][:limit]
    return _paginate(rows, limit, 0)


@router.get("/recommendations", response_model=Page)
def recommendations(limit: int = Query(20, ge=1, le=200), min_score: int = Query(70, ge=0, le=100)) -> Page:
    from core import database
    from core.recommendation_engine import recommend_all, sort_for_dashboard

    try:
        from core.profile_loader import load as _load
        profile = _load()
    except Exception:
        profile = None
    rows = recommend_all(_ranked_rows(), profile=profile,
                         research_job_ids=database.get_researched_job_ids())
    rows = [r for r in rows if (r.get("total_score") or 0) >= min_score]
    rows = sort_for_dashboard(rows)
    return _paginate(rows, limit, 0)


@router.get("/analytics")
def analytics_endpoint() -> dict:
    from core import analytics
    return analytics.summary()


@router.get("/applications", response_model=Page)
def applications(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                 status: str | None = None) -> Page:
    from core import tracker
    rows = tracker.list_applications()
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return _paginate(rows, limit, offset)


@router.post("/applications/status", response_model=MessageResponse)
def update_status(payload: StatusUpdate) -> MessageResponse:
    from core import tracker
    if not tracker.is_valid_status(payload.status):
        raise HTTPException(400, f"invalid status; valid: {tracker.STATUSES}")
    tracker.set_status(payload.job_id, payload.status, notes=payload.notes)
    return MessageResponse(ok=True, message=f"job {payload.job_id} → {payload.status}")


@router.post("/pipeline/run", response_model=MessageResponse)
def run_pipeline(payload: PipelineRunRequest) -> MessageResponse:
    from core.pipeline import run_autonomous_pipeline
    summary = run_autonomous_pipeline(top_n=payload.top_n,
                                      research_min_score=payload.research_min_score)
    return MessageResponse(ok=True, message="pipeline complete", data={
        "run_id": summary.get("run_id"), "status": summary.get("status"),
        "duration_seconds": summary.get("duration_seconds"),
    })


@router.get("/pipeline/runs", response_model=Page)
def pipeline_runs(limit: int = Query(20, ge=1, le=200)) -> Page:
    from core import database
    rows = database.get_pipeline_runs(limit=limit)
    return _paginate(rows, limit, 0)
