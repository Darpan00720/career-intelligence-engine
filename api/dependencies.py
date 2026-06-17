"""Shared API dependencies: request-id binding."""
from __future__ import annotations

from fastapi import Request

from core.observability import set_ids


async def bind_request_ids(request: Request) -> dict[str, str]:
    """Bind request/correlation IDs from headers (or generate) for this request."""
    incoming = request.headers.get("x-correlation-id")
    rid, cid = set_ids(correlation_id=incoming)
    return {"request_id": rid, "correlation_id": cid}
