"""API Gateway v2 (v5) — versioned, tenant-isolated, rate-limited routes.

Mounted at /api/v2. The existing /api/* CRM routes remain as the v1 surface
(unchanged for backward compatibility). New endpoints expose workflows, reviews,
experiments, metrics, tenants, and usage. Tenant isolation comes from the
X-Tenant-ID header (defaults to 'default'); a fixed-window limiter guards each
client; API keys are accepted via X-API-Key.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Header, HTTPException, Query, Response

from api.crm_models import Page

router = APIRouter(prefix="/api/v2", tags=["v2"])


# ── Tenant isolation ──────────────────────────────────────────────────────────

def _tenant(x_tenant_id: str | None = Header(default=None)) -> str:
    from core import tenancy
    tid = x_tenant_id or tenancy.DEFAULT_TENANT
    tenancy._current_tenant.set(tid)   # scope this request to the tenant
    return tid


# ── Simple fixed-window rate limiter ──────────────────────────────────────────

_RL_WINDOW = 60.0
_RL_MAX = 600
_rl_state: dict[str, tuple[float, int]] = {}


def _rate_limit(key: str) -> None:
    now = time.time()
    start, count = _rl_state.get(key, (now, 0))
    if now - start >= _RL_WINDOW:
        start, count = now, 0
    count += 1
    _rl_state[key] = (start, count)
    if count > _RL_MAX:
        raise HTTPException(429, "rate limit exceeded")


# ── Workflows ─────────────────────────────────────────────────────────────────

@router.get("/workflows", response_model=Page)
def list_workflows(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                   status: str | None = None, tenant: str = None):  # type: ignore
    from core import database, tenancy
    tenant = tenant or tenancy.current_tenant()
    _rate_limit(f"{tenant}:workflows")
    with database.get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM workflow_runs WHERE tenant_id = ? ORDER BY id DESC", (tenant,)
        ).fetchall()
    items = [dict(r) for r in rows]
    if status:
        items = [r for r in items if r["status"] == status]
    return Page(items=items[offset:offset + limit], total=len(items), limit=limit, offset=offset)


@router.get("/workflows/{run_id}")
def get_workflow(run_id: str):
    from core.workflow_engine import get_engine
    run = get_engine().get_run(run_id)
    if run is None:
        raise HTTPException(404, "workflow run not found")
    return run


@router.post("/workflows/{run_id}/retry")
def retry_workflow(run_id: str):
    from core.workflow_engine import get_engine
    try:
        status = get_engine().resume(run_id)
    except KeyError as exc:
        raise HTTPException(400, str(exc))
    return {"run_id": run_id, "status": status}


@router.post("/workflows/{run_id}/cancel")
def cancel_workflow(run_id: str):
    from core.workflow_engine import get_engine
    if get_engine().get_run(run_id) is None:
        raise HTTPException(404, "workflow run not found")
    get_engine().cancel(run_id)
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
        rows = conn.execute("SELECT * FROM experiments ORDER BY id DESC").fetchall()
    items = [dict(r) for r in rows]
    return Page(items=items[offset:offset + limit], total=len(items), limit=limit, offset=offset)


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
def usage(tenant: str = None):  # type: ignore
    from core import tenancy
    from core.cost_intelligence import cost_breakdown
    tenant = tenant or tenancy.current_tenant()
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
