"""FastAPI application factory + exception-handling middleware.

Stateless HTTP layer over the compiled graph. JSON-only. OpenAPI auto-generated.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes import router
from core.logging_config import get_logger
from core.observability import current_ids, log_startup, set_ids
from core.settings import settings
from core.version import NAME, VERSION

logger = get_logger("api")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # ── Startup ──
    log_startup(settings)
    from api.auth import auth_enabled
    if settings.environment == "production" and not auth_enabled():
        logger.warning(
            "API auth is DISABLED (API_KEYS not set) while ENVIRONMENT=production; "
            "all endpoints are open and tenant isolation is not enforced")
    # Warm the compiled graph so /ready and first request are fast.
    try:
        from services.career_service import warmup
        warmup()
    except Exception:  # warmup is best-effort; /ready reports true status
        logger.exception("graph warmup failed at startup")
    yield
    # ── Shutdown ──
    # Close the singleton checkpointer connection (R3: no leaked sqlite conn).
    try:
        from services.career_service import shutdown
        shutdown()
    except Exception:
        logger.exception("graph shutdown cleanup failed")


def create_app() -> FastAPI:
    app = FastAPI(
        title=NAME,
        version=VERSION,
        description="Deterministic career-intelligence pipeline API.",
        lifespan=_lifespan,
    )

    # CORS is opt-in: set CORS_ORIGINS (comma-separated) to allow browser clients.
    # Default is no cross-origin access (same-origin only).
    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def _request_context(request: Request, call_next):
        set_ids(correlation_id=request.headers.get("x-correlation-id"))
        try:
            response = await call_next(request)
        except Exception as exc:  # exception-handling middleware
            logger.exception("unhandled error | %s | %s", current_ids(), exc)
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error", **current_ids()},
            )
        response.headers["x-correlation-id"] = current_ids()["correlation_id"]
        return response

    app.include_router(router)

    # v4 CRM REST API (jobs, recommendations, analytics, applications, pipeline).
    # This /api/* surface is the v1 API and stays stable for backward compat.
    from api.crm_routes import router as crm_router
    app.include_router(crm_router)

    # v5 API Gateway v2 — workflows, reviews, experiments, metrics, tenants, usage.
    from api.v2_routes import router as v2_router
    app.include_router(v2_router)
    return app


app = create_app()
