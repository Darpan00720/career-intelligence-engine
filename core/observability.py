"""Observability: structured logging, request/correlation IDs, timing.

No business logic; never mutates state. Pure cross-cutting instrumentation.
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from core.logging_config import get_logger

logger = get_logger("observability")

# Per-request context (async/thread-safe via contextvars).
_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex


def set_ids(request_id: str | None = None, correlation_id: str | None = None) -> tuple[str, str]:
    rid = request_id or new_request_id()
    cid = correlation_id or rid
    _request_id.set(rid)
    _correlation_id.set(cid)
    return rid, cid


def current_ids() -> dict[str, str]:
    return {"request_id": _request_id.get(), "correlation_id": _correlation_id.get()}


@contextmanager
def timed(operation: str, **fields):
    """Log start/duration of an operation with current request/correlation IDs."""
    ids = current_ids()
    start = time.perf_counter()
    logger.info("start %s | %s | %s", operation, ids, fields or "")
    try:
        yield
    except Exception as exc:
        dur = (time.perf_counter() - start) * 1000
        logger.exception("error %s | %.1fms | %s | %s", operation, dur, ids, exc)
        raise
    else:
        dur = (time.perf_counter() - start) * 1000
        logger.info("done %s | %.1fms | %s", operation, dur, ids)


def log_startup(settings) -> None:
    logger.info(
        "startup | env=%s version=%s provider=%s embed=%s db=%s checkpoint=%s",
        settings.environment, settings.api_version, settings.model_provider,
        settings.embedding_model, settings.database_url, settings.checkpoint_path,
    )
