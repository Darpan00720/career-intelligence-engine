"""Persistent Workflow Engine (v5).

Replaces transient orchestration state with durable runs/steps/events so a
workflow can survive a crash and be resumed, replayed, cancelled, retried, and
audited. Each step's status is persisted as it executes; resume() skips steps
already COMPLETED.

States: PENDING → RUNNING → (COMPLETED | PARTIAL_SUCCESS | FAILED | CANCELLED),
with RETRYING during step retries.

Definitions are code (a name + ordered StepDefs); persisted runs reference the
definition by name. Steps run sequentially (use the Orchestrator for parallel
fan-out within a step). Emits WorkflowCompleted / WorkflowFailed on the bus.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable

from core import database
from core.event_log import log_event

PENDING = "PENDING"
RUNNING = "RUNNING"
PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
RETRYING = "RETRYING"
CANCELLED = "CANCELLED"


@dataclass
class StepDef:
    name: str
    func: Callable[[dict], dict]
    max_retries: int = 1
    timeout: float | None = None
    critical: bool = False   # if a critical step fails, the run is FAILED


@dataclass
class WorkflowDefinition:
    name: str
    steps: list[StepDef]

    def __post_init__(self):
        _REGISTRY[self.name] = self


_REGISTRY: dict[str, WorkflowDefinition] = {}


def get_definition(name: str) -> WorkflowDefinition | None:
    return _REGISTRY.get(name)


class WorkflowCancelled(Exception):
    pass


def _run_with_timeout(func, arg, timeout: float | None):
    if timeout is None:
        return func(arg)
    result, error = {}, {}

    def _target():
        try:
            result["v"] = func(arg)
        except Exception as exc:  # noqa: BLE001
            error["e"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"step exceeded timeout of {timeout}s")
    if "e" in error:
        raise error["e"]
    return result.get("v")


class WorkflowEngine:
    # ── Persistence helpers ────────────────────────────────────────────────────
    def _set_run_status(self, run_id: str, status: str, detail: dict | None = None) -> None:
        with database.get_connection() as conn:
            conn.execute(
                "UPDATE workflow_runs SET status = ?, detail = COALESCE(?, detail), "
                "updated_at = DATETIME('now') WHERE run_id = ?",
                (status, json.dumps(detail) if detail is not None else None, run_id),
            )

    def _emit(self, run_id: str, event: str, detail: str = "") -> None:
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO workflow_events (run_id, event, detail) VALUES (?, ?, ?)",
                (run_id, event, detail),
            )

    def _upsert_step(self, run_id: str, name: str, status: str,
                     attempts: int = 0, output: dict | None = None,
                     error: str | None = None) -> None:
        with database.get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM workflow_steps WHERE run_id = ? AND name = ?",
                (run_id, name),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE workflow_steps SET status=?, attempts=?, output=?, error=?, "
                    "updated_at=DATETIME('now') WHERE id=?",
                    (status, attempts, json.dumps(output) if output else None, error, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO workflow_steps (run_id, name, status, attempts, output, error) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, name, status, attempts,
                     json.dumps(output) if output else None, error),
                )

    def _step_status(self, run_id: str, name: str) -> str | None:
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT status FROM workflow_steps WHERE run_id = ? AND name = ?",
                (run_id, name),
            ).fetchone()
        return row["status"] if row else None

    def _run_status(self, run_id: str) -> str | None:
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT status FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return row["status"] if row else None

    # ── Public API ─────────────────────────────────────────────────────────────
    def start(self, defn: WorkflowDefinition, tenant_id: str | None = None,
              context: dict | None = None) -> str:
        from core.tenancy import current_tenant
        run_id = uuid.uuid4().hex
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO workflow_runs (run_id, tenant_id, definition, status) "
                "VALUES (?, ?, ?, ?)",
                (run_id, tenant_id or current_tenant(), defn.name, PENDING),
            )
        return self._execute(run_id, defn, dict(context or {}))

    def resume(self, run_id: str, defn: WorkflowDefinition | None = None,
               context: dict | None = None) -> str:
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT definition FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if not row:
            raise KeyError(f"no workflow run {run_id}")
        defn = defn or get_definition(row["definition"])
        if defn is None:
            raise KeyError(f"definition {row['definition']!r} not registered")
        return self._execute(run_id, defn, dict(context or {}), resuming=True)

    def cancel(self, run_id: str) -> None:
        self._set_run_status(run_id, CANCELLED)
        self._emit(run_id, "cancelled")

    def get_run(self, run_id: str) -> dict | None:
        with database.get_connection() as conn:
            run = conn.execute(
                "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if not run:
                return None
            steps = conn.execute(
                "SELECT * FROM workflow_steps WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        out = dict(run)
        out["steps"] = [dict(s) for s in steps]
        return out

    # ── Execution ──────────────────────────────────────────────────────────────
    def _execute(self, run_id: str, defn: WorkflowDefinition, context: dict,
                 resuming: bool = False) -> str:
        self._set_run_status(run_id, RUNNING)
        self._emit(run_id, "resumed" if resuming else "started")
        any_failed = False

        for step in defn.steps:
            # Honor cancellation requested between steps.
            if self._run_status(run_id) == CANCELLED:
                self._emit(run_id, "cancelled_midrun")
                return CANCELLED
            # Resume: skip steps already completed.
            if resuming and self._step_status(run_id, step.name) == COMPLETED:
                context.setdefault("_resumed_skips", []).append(step.name)
                continue

            attempts, ok, last_error, output = 0, False, None, None
            while attempts <= step.max_retries:
                attempts += 1
                self._upsert_step(run_id, step.name,
                                  RETRYING if attempts > 1 else RUNNING, attempts)
                try:
                    output = _run_with_timeout(step.func, context, step.timeout) or {}
                    if isinstance(output, dict):
                        context.update(output)
                    ok = True
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)

            if ok:
                self._upsert_step(run_id, step.name, COMPLETED, attempts, output)
                self._emit(run_id, "step_completed", step.name)
            else:
                any_failed = True
                self._upsert_step(run_id, step.name, FAILED, attempts, error=last_error)
                self._emit(run_id, "step_failed", f"{step.name}: {last_error}")
                log_event("pipeline", "workflow_step_failed", status="error",
                          error=f"{step.name}: {last_error}", level="ERROR")
                if step.critical:
                    self._finish(run_id, FAILED)
                    return FAILED

        final = PARTIAL_SUCCESS if any_failed else COMPLETED
        self._finish(run_id, final)
        return final

    def _finish(self, run_id: str, status: str) -> None:
        self._set_run_status(run_id, status)
        self._emit(run_id, f"finished:{status}")
        # Publish a domain event (best-effort).
        try:
            from events import get_bus, WORKFLOW_COMPLETED, WORKFLOW_FAILED
            event_type = WORKFLOW_FAILED if status == FAILED else WORKFLOW_COMPLETED
            get_bus().emit(event_type, {"run_id": run_id, "status": status},
                           idempotency_key=f"{run_id}:{status}")
        except Exception:  # pragma: no cover - event bus is best-effort here
            pass


_engine: WorkflowEngine | None = None


def get_engine() -> WorkflowEngine:
    global _engine
    if _engine is None:
        _engine = WorkflowEngine()
    return _engine
