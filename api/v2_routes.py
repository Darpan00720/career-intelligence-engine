"""API Gateway v2 (v5) — versioned, tenant-isolated, rate-limited routes.

Mounted at /api/v2. The existing /api/* CRM routes remain as the v1 surface
(unchanged for backward compatibility). New endpoints expose workflows, reviews,
experiments, metrics, tenants, and usage.

Security: every route depends on :func:`api.auth.authenticate`. When ``API_KEYS``
is configured, requests must present a valid ``X-API-Key`` and the tenant is
bound to that key (the ``X-Tenant-ID`` header / ``?tenant=`` query param are
ignored, so a client cannot read another tenant's data). When no keys are
configured the API runs in open/dev mode and the tenant falls back to the
``X-Tenant-ID`` header. A fixed-window limiter (``api.auth.rate_limit``) guards
each tenant. Workflow lookups are tenant-scoped to prevent cross-tenant access.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api.auth import authenticate, tenant_ctx
from api.crm_models import Page

router = APIRouter(prefix="/api/v2", tags=["v2"], dependencies=[Depends(authenticate)])


def _owns_run(run: dict | None, tenant: str) -> bool:
    """True when the run exists and belongs to the requesting tenant."""
    return bool(run) and run.get("tenant_id") == tenant


# ── Workflows ─────────────────────────────────────────────────────────────────

@router.get("/workflows", response_model=Page)
def list_workflows(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                   status: str | None = None, tenant: str = Depends(tenant_ctx)):
    from core import database
    params: list = [tenant]
    sql = "SELECT * FROM workflow_runs WHERE tenant_id = ?"
    if status:
        sql += " AND status = ?"
        params.append(status)
    count_sql = sql.replace("SELECT *", "SELECT COUNT(*) AS n", 1)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params_page = params + [limit, offset]
    with database.get_connection() as conn:
        total = conn.execute(count_sql, params).fetchone()["n"]
        rows = conn.execute(sql, params_page).fetchall()
    items = [dict(r) for r in rows]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/workflows/{run_id}")
def get_workflow(run_id: str, tenant: str = Depends(tenant_ctx)):
    from core.workflow_engine import get_engine
    run = get_engine().get_run(run_id)
    if not _owns_run(run, tenant):
        raise HTTPException(404, "workflow run not found")
    return run


@router.post("/workflows/{run_id}/retry")
def retry_workflow(run_id: str, tenant: str = Depends(tenant_ctx)):
    from core.workflow_engine import get_engine
    engine = get_engine()
    if not _owns_run(engine.get_run(run_id), tenant):
        raise HTTPException(404, "workflow run not found")
    try:
        status = engine.resume(run_id)
    except KeyError as exc:
        raise HTTPException(400, str(exc))
    return {"run_id": run_id, "status": status}


@router.post("/workflows/{run_id}/cancel")
def cancel_workflow(run_id: str, tenant: str = Depends(tenant_ctx)):
    from core.workflow_engine import get_engine
    engine = get_engine()
    if not _owns_run(engine.get_run(run_id), tenant):
        raise HTTPException(404, "workflow run not found")
    engine.cancel(run_id)
    return {"run_id": run_id, "status": "CANCELLED"}


# ── Reviews / experiments ─────────────────────────────────────────────────────

@router.get("/reviews", response_model=Page)
def list_reviews(state: str | None = None, limit: int = Query(100, ge=1, le=1000),
                 offset: int = Query(0, ge=0)):
    from core import review_queue
    rows = review_queue.all_reviews(state)
    return Page(items=rows[offset:offset + limit], total=len(rows), limit=limit, offset=offset)


@router.get("/experiments", response_model=Page)
def list_experiments(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    from core import database
    with database.get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM experiments").fetchone()["n"]
        rows = conn.execute(
            "SELECT * FROM experiments ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
    return Page(items=[dict(r) for r in rows], total=total, limit=limit, offset=offset)


# ── Metrics / tenants / usage ─────────────────────────────────────────────────

@router.get("/metrics")
def metrics_snapshot():
    from core.metrics import registry
    return registry().snapshot()


@router.get("/metrics/prometheus")
def metrics_prometheus():
    from core.metrics import registry
    return Response(content=registry().render_prometheus(), media_type="text/plain")


@router.get("/tenants", response_model=Page)
def list_tenants(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    from core import tenancy
    rows = tenancy.list_tenants()
    return Page(items=rows[offset:offset + limit], total=len(rows), limit=limit, offset=offset)


@router.get("/usage")
def usage(tenant: str = Depends(tenant_ctx)):
    from core import tenancy
    from core.cost_intelligence import cost_breakdown
    return {"tenant_id": tenant, "usage": tenancy.get_usage(tenant_id=tenant),
            "cost": cost_breakdown()}


# ── Versioned jobs alias (demonstrates deprecation headers) ───────────────────

@router.get("/jobs", response_model=Page)
def jobs_v2(response: Response, limit: int = Query(50, ge=1, le=500),
            offset: int = Query(0, ge=0), min_score: int = Query(0, ge=0, le=100)):
    from core.ranking import rank_jobs
    from core import database
    rows = rank_jobs(database.get_all_scored_jobs_ranked())
    if min_score:
        rows = [r for r in rows if (r.get("total_score") or 0) >= min_score]
    return Page(items=rows[offset:offset + limit], total=len(rows), limit=limit, offset=offset)
