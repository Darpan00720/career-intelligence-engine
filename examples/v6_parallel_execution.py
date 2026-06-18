"""v6 Phase 4 — execution examples: planner levelization + parallel routing.

Run:  python3 examples/v6_parallel_execution.py

Demonstrates, without touching the database or external services:

  1. Deterministic planning from a user query, and the ExecutionPlanner-derived
     parallel levels for the (linear) canonical pipeline.
  2. A *branching* plan whose independent tasks share a level — i.e. would be
     dispatched in parallel by the supervisor router.
  3. The dependency-based router selecting a whole ready level at once.

This is illustrative; the authoritative behaviour is covered by
tests/test_parallel_execution.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from graph.planner import build_execution_plan, plan_levels
from graph.routing import route_from_supervisor
from schemas.execution import ExecutionPlan, Task


def example_1_canonical_levels() -> None:
    plan = build_execution_plan("generate full report")
    print("\n[1] full pipeline plan:", plan.intent_kind)
    print("    tasks :", plan.task_ids())
    print("    levels:", plan_levels(plan))  # one task per level (linear chain)


def example_2_branching_parallel() -> None:
    # Two independent roots (a, b) feed a join (c). a and b share level 0.
    plan = ExecutionPlan(plan_id="branch", intent_kind="custom", tasks=[
        Task(id="a", agent="a"),
        Task(id="b", agent="b"),
        Task(id="c", agent="c", depends_on=["a", "b"]),
    ])
    print("\n[2] branching plan levels:", plan_levels(plan))   # [['a', 'b'], ['c']]
    # Router dispatches the whole ready level in parallel:
    print("    router @ start          :", route_from_supervisor({"execution_plan": plan}))
    print("    router @ a,b complete   :",
          route_from_supervisor({"execution_plan": plan, "completed_tasks": ["a", "b"]}))


def example_3_scoped_plan() -> None:
    plan = build_execution_plan("just ingest jobs")
    print("\n[3] scoped plan ('ingest'):", plan.task_ids())
    print("    router @ start         :", route_from_supervisor({"execution_plan": plan}))


if __name__ == "__main__":  # pragma: no cover - manual example
    example_1_canonical_levels()
    example_2_branching_parallel()
    example_3_scoped_plan()
    print("\nok")
