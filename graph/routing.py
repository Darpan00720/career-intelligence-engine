"""Supervisor routing.

Two modes, selected automatically by what's in state:

* **Dependency-based (v6 Phase 3)** — when an ``execution_plan`` is present, the
  router dispatches every task whose dependencies are already satisfied
  (``completed_tasks``). Returning a *list* fans those tasks out in one parallel
  superstep; the supervisor re-evaluates after they return. END is reached once
  every task is complete (or no task can make progress — a deadlock guard).

* **Phase-based (legacy)** — when there is no plan, the original behaviour is
  preserved exactly: map the last-completed `phase` to the next node. This keeps
  every pre-v6 graph run (and its tests) byte-for-byte unchanged.

The supervisor node (graph/nodes.supervisor_node) is responsible for *marking*
tasks completed; this module only reads state and decides routing.
"""
from __future__ import annotations

from langgraph.graph import END

from graph.state import CareerState
from schemas.control import Phase
from schemas.execution import ExecutionPlan

# last-completed phase  ->  next node to run  (legacy phase-based routing)
_NEXT_NODE: dict = {
    None: "profile_strategy",
    Phase.INIT: "profile_strategy",
    Phase.PROFILE: "job_ingestion",
    Phase.INGESTION: "taxonomy",
    Phase.TAXONOMY: "scoring",
    Phase.SCORING: "opportunity_intel",
    Phase.INTELLIGENCE: "prioritization",
    Phase.PRIORITIZATION: "recommendations",
    Phase.RECOMMENDATIONS: "output_experience",
    Phase.OUTPUT: END,
    Phase.REPORT: END,
    Phase.DONE: END,
}


def as_plan(plan) -> ExecutionPlan | None:
    """Coerce a state ``execution_plan`` value to an ExecutionPlan.

    Accepts a live model (in-process), a dict (rehydrated from a checkpoint), or
    None. Used by both the router and the supervisor's completion marking.
    """
    if plan is None or isinstance(plan, ExecutionPlan):
        return plan
    if isinstance(plan, dict):
        return ExecutionPlan.model_validate(plan)
    return plan  # assume duck-typed ExecutionPlan-like


def ready_agents(state: CareerState) -> list[str]:
    """Node names of tasks whose dependencies are all satisfied and not yet done."""
    plan = as_plan(state.get("execution_plan"))
    if plan is None:
        return []
    completed = set(state.get("completed_tasks") or [])
    return [t.agent for t in plan.ready_tasks(completed)]


def route_from_supervisor(state: CareerState):
    """Return the next node, a list of parallel nodes, or END.

    Dependency-based when a plan is present; otherwise legacy phase-based.
    """
    plan = as_plan(state.get("execution_plan"))
    if plan is None:
        return _NEXT_NODE.get(state.get("phase"), END)

    completed = set(state.get("completed_tasks") or [])
    if plan.is_complete(completed):
        return END
    ready = [t.agent for t in plan.ready_tasks(completed)]
    # Empty ready set with the plan not complete => unsatisfiable (e.g. a failed
    # dependency). End cleanly rather than spin.
    return ready or END
