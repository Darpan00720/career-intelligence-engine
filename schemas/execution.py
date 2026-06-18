"""Execution-planning schemas (v6 Phase 1): Task and ExecutionPlan.

These models are the *contract* a Planner Agent will emit and a plan-aware
supervisor will consume. Phase 1 introduces the data shapes ONLY — nothing in
this module routes, executes, or mutates graph state. The existing phase-based
routing is untouched.

Conventions follow schemas/base.CareerBaseModel (extra="forbid",
validate_assignment, enums kept as enums). The helper methods are pure reads
over a caller-supplied ``completed`` set — they make the models testable now and
ready for the routing phase later, without coupling to LangGraph.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import Field

from schemas.base import CareerBaseModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    """Lifecycle of a single planned task."""

    PENDING = "pending"        # not yet runnable / not yet started
    READY = "ready"            # dependencies satisfied, eligible to dispatch
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"        # optional task whose dependency failed


class Task(CareerBaseModel):
    """One unit of planned work, mapped to a graph node (``agent``).

    ``id`` is the stable handle used in other tasks' ``depends_on`` and in the
    state's pending/completed task lists. ``agent`` is the node name that will
    execute it (e.g. "scoring"). ``depends_on`` references other task ids and
    must form a DAG (validated by the planner, not here).
    """

    id: str
    agent: str
    capability: str = ""
    depends_on: list[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)
    optional: bool = False
    status: TaskStatus = TaskStatus.PENDING

    def is_ready(self, completed: set[str]) -> bool:
        """True when every dependency id is in ``completed``."""
        return all(dep in completed for dep in self.depends_on)


class ExecutionPlan(CareerBaseModel):
    """An ordered set of tasks derived from a user query.

    Pure data: the levelization/route logic lives elsewhere (the planner uses
    core.workflow_dag.DependencyGraph/ExecutionPlanner to validate + level).
    The read-only helpers below operate on a caller-supplied ``completed`` set.
    """

    plan_id: str
    user_query: str = ""
    intent_kind: str = "full_pipeline"
    tasks: list[Task] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    planner_version: str = "v1"

    # ── pure read helpers (no side effects, no graph coupling) ──────────────────
    def task_ids(self) -> list[str]:
        return [t.id for t in self.tasks]

    def get(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def ready_tasks(self, completed: set[str]) -> list[Task]:
        """Tasks not yet completed whose dependencies are all satisfied."""
        done = set(completed)
        return [t for t in self.tasks if t.id not in done and t.is_ready(done)]

    def is_complete(self, completed: set[str]) -> bool:
        done = set(completed)
        return all(t.id in done for t in self.tasks)
