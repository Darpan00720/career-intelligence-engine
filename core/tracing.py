"""Distributed tracing primitives (v5).

Correlation/trace/span IDs on contextvars (async/thread-safe). `span()` times a
unit of work and records it as a histogram metric. This is an OpenTelemetry-
shaped facade: replace the contextvars + span() body with an OTel SDK tracer and
call sites stay identical.
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from core.event_log import log_event
from core.metrics import registry

_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")
_span_id: ContextVar[str] = ContextVar("span_id", default="-")
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def start_trace(correlation_id: str | None = None) -> dict:
    tid = _new_id()
    _trace_id.set(tid)
    _correlation_id.set(correlation_id or tid)
    _span_id.set(_new_id())
    return current_trace()


def current_trace() -> dict:
    return {
        "trace_id": _trace_id.get(),
        "span_id": _span_id.get(),
        "correlation_id": _correlation_id.get(),
    }


@contextmanager
def span(name: str):
    """Open a child span; records duration into the `span_duration` histogram."""
    if _trace_id.get() == "-":
        start_trace()
    parent = _span_id.get()
    sid = _new_id()
    token = _span_id.set(sid)
    start = time.perf_counter()
    try:
        yield {"trace_id": _trace_id.get(), "span_id": sid, "parent_span_id": parent}
    finally:
        duration = time.perf_counter() - start
        registry().observe("span_duration", duration, {"span": name})
        log_event("pipeline", f"span:{name}", duration=duration, status="ok")
        _span_id.reset(token)
