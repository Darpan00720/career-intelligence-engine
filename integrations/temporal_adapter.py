"""Temporal evaluation adapter (v5.2).

Defines hexagonal ports for durable workflow orchestration —
TemporalWorkflowPort / TemporalActivityPort / TemporalPersistencePort — plus a
``LocalTemporalRuntime`` that satisfies them in-process (no server) so the
programming model is testable now. A real ``temporalio``-backed adapter
implements the same ports for production.

This lets us A/B the custom DAG engine against the Temporal model behind one
interface. See docs/V51_ARCHITECTURE.md (v5.2 section) for the comparison and
recommendation.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from core.concurrency import backoff_retry
from core.workflow_engine import _run_with_timeout


# ── Ports ─────────────────────────────────────────────────────────────────────

class TemporalActivityPort(ABC):
    @abstractmethod
    def execute(self, fn: Callable, *args, retries: int = 2,
                timeout: float | None = None, **kwargs) -> Any: ...


class TemporalPersistencePort(ABC):
    @abstractmethod
    def save(self, run_id: str, state: dict) -> None: ...
    @abstractmethod
    def load(self, run_id: str) -> dict | None: ...


@dataclass
class WorkflowExecution:
    run_id: str
    status: str
    result: Any = None
    history: list[dict] = field(default_factory=list)
    error: str | None = None


class TemporalWorkflowPort(ABC):
    @abstractmethod
    def start(self, workflow_fn: Callable, *args, **kwargs) -> WorkflowExecution: ...


# ── Local (in-process) implementation ───────────────────────────────────────────

class InMemoryPersistence(TemporalPersistencePort):
    def __init__(self):
        self._store: dict[str, dict] = {}

    def save(self, run_id: str, state: dict) -> None:
        self._store[run_id] = dict(state)

    def load(self, run_id: str) -> dict | None:
        return self._store.get(run_id)


class LocalActivityRuntime(TemporalActivityPort):
    """Runs activities synchronously with retry + timeout (Temporal-like semantics)."""

    def execute(self, fn: Callable, *args, retries: int = 2,
                timeout: float | None = None, **kwargs) -> Any:
        def _call():
            return _run_with_timeout(lambda _: fn(*args, **kwargs), None, timeout)
        return backoff_retry(_call, retries=retries, base_delay=0.0)


class WorkflowContext:
    """Passed to workflow functions; records an activity history for replay/audit."""

    def __init__(self, run_id: str, activities: TemporalActivityPort):
        self.run_id = run_id
        self._activities = activities
        self.history: list[dict] = []

    def execute_activity(self, fn: Callable, *args, retries: int = 2,
                         timeout: float | None = None, **kwargs) -> Any:
        name = getattr(fn, "__name__", "activity")
        try:
            result = self._activities.execute(fn, *args, retries=retries,
                                              timeout=timeout, **kwargs)
            self.history.append({"activity": name, "status": "completed"})
            return result
        except Exception as exc:  # noqa: BLE001
            self.history.append({"activity": name, "status": "failed", "error": str(exc)})
            raise


class LocalTemporalRuntime(TemporalWorkflowPort):
    def __init__(self, activities: TemporalActivityPort | None = None,
                 persistence: TemporalPersistencePort | None = None):
        self.activities = activities or LocalActivityRuntime()
        self.persistence = persistence or InMemoryPersistence()

    def start(self, workflow_fn: Callable, *args, **kwargs) -> WorkflowExecution:
        run_id = uuid.uuid4().hex
        ctx = WorkflowContext(run_id, self.activities)
        self.persistence.save(run_id, {"status": "RUNNING"})
        try:
            result = workflow_fn(ctx, *args, **kwargs)
            execution = WorkflowExecution(run_id, "COMPLETED", result, ctx.history)
        except Exception as exc:  # noqa: BLE001
            execution = WorkflowExecution(run_id, "FAILED", None, ctx.history, str(exc))
        self.persistence.save(run_id, {"status": execution.status,
                                       "history": execution.history})
        return execution


class TemporalRuntime(TemporalWorkflowPort):  # pragma: no cover - requires temporal server
    """Production adapter backed by the temporalio SDK (same port)."""

    def __init__(self, target: str = "localhost:7233", namespace: str = "default"):
        self.target = target
        self.namespace = namespace

    def start(self, workflow_fn: Callable, *args, **kwargs) -> WorkflowExecution:
        import asyncio

        from temporalio.client import Client

        async def _run():
            client = await Client.connect(self.target, namespace=self.namespace)
            handle = await client.start_workflow(
                workflow_fn, args, id=uuid.uuid4().hex, task_queue="career-tq")
            result = await handle.result()
            return WorkflowExecution(handle.id, "COMPLETED", result)

        return asyncio.run(_run())
