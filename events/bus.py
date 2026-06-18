"""EventBus (v5) — in-process publish/subscribe with persistence.

Properties:
  * at-least-once delivery: every subscriber is invoked; failures are retried
    a bounded number of times, then routed to the dead-letter queue.
  * idempotency: publishing an event whose (type, idempotency_key) already
    exists is a no-op (deduplicated via a unique index).
  * replay: persisted events can be re-dispatched to current subscribers.
  * event versioning: carried on the Event envelope.

The transport is in-process (synchronous). The same interface backs a future
distributed transport (Redis Streams / Kafka) — swap the dispatch method.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Callable

from core import database
from core.event_log import log_event as _evlog
from events.types import Event

Handler = Callable[[Event], None]


class DeadLetterQueue:
    def add(self, event: Event, error: str) -> None:
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO dead_letter_queue (event_id, type, tenant_id, payload, error) "
                "VALUES (?, ?, ?, ?, ?)",
                (event.event_id, event.type, event.tenant_id,
                 json.dumps(event.payload, ensure_ascii=False), error),
            )

    def list(self, limit: int = 100) -> list[dict]:
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM dead_letter_queue ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


class EventBus:
    def __init__(self, max_delivery_attempts: int = 2):
        self._subscribers: dict[str, list[Handler]] = {}
        self.dlq = DeadLetterQueue()
        self.max_delivery_attempts = max_delivery_attempts

    # ── Subscription ───────────────────────────────────────────────────────────
    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe_all(self, event_type: str | None = None) -> None:
        if event_type is None:
            self._subscribers.clear()
        else:
            self._subscribers.pop(event_type, None)

    # ── Persistence / idempotency ──────────────────────────────────────────────
    def _persist(self, event: Event) -> bool:
        """Persist the event. Returns False if it was a duplicate (idempotent)."""
        row = event.to_row()
        try:
            with database.get_connection() as conn:
                conn.execute(
                    """INSERT INTO events_log
                       (event_id, type, version, tenant_id, idempotency_key, payload)
                       VALUES (:event_id, :type, :version, :tenant_id,
                               :idempotency_key, :payload)""",
                    row,
                )
            return True
        except sqlite3.IntegrityError:
            # Duplicate (type, idempotency_key) or event_id → idempotent no-op.
            return False

    # ── Delivery ───────────────────────────────────────────────────────────────
    def _deliver(self, event: Event) -> int:
        delivered = 0
        for handler in self._subscribers.get(event.type, []):
            last_error = None
            for _ in range(self.max_delivery_attempts):
                try:
                    handler(event)
                    delivered += 1
                    last_error = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
            if last_error is not None:
                self.dlq.add(event, last_error)
                _evlog("pipeline", "event_dead_lettered", status="error",
                       error=f"{event.type}: {last_error}", level="ERROR")
        return delivered

    def publish(self, event: Event) -> dict:
        """Publish an event. Deduplicated by idempotency key. Returns a receipt."""
        if not self._persist(event):
            return {"event_id": event.event_id, "duplicate": True, "delivered": 0}
        delivered = self._deliver(event)
        _evlog("pipeline", "event_published", status="ok",
               error=f"{event.type} → {delivered} handlers")
        return {"event_id": event.event_id, "duplicate": False, "delivered": delivered}

    def emit(self, event_type: str, payload: dict | None = None, *,
             tenant_id: str | None = None, idempotency_key: str | None = None,
             version: int = 1) -> dict:
        """Convenience: build + publish an Event (tenant defaults to context)."""
        from core.tenancy import current_tenant
        return self.publish(Event(
            type=event_type, payload=payload or {}, version=version,
            tenant_id=tenant_id or current_tenant(), idempotency_key=idempotency_key,
        ))

    # ── Replay ─────────────────────────────────────────────────────────────────
    def replay(self, event_type: str | None = None, since_id: int = 0) -> int:
        """Re-dispatch persisted events to current subscribers. Returns count replayed."""
        with database.get_connection() as conn:
            if event_type:
                rows = conn.execute(
                    "SELECT * FROM events_log WHERE type = ? AND id > ? ORDER BY id",
                    (event_type, since_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events_log WHERE id > ? ORDER BY id", (since_id,)
                ).fetchall()
        for row in rows:
            self._deliver(Event.from_row(dict(row)))
        return len(rows)


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_bus() -> None:
    global _bus
    _bus = None
