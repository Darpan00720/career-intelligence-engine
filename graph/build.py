"""Graph assembly: build the Sprint-2 executable StateGraph.

Topology (hub-and-spoke supervisor):

    START -> supervisor --(conditional)--> profile_strategy -> supervisor
                                       --> job_ingestion    -> supervisor
                                       --> taxonomy         -> supervisor
                                       --> scoring          -> supervisor
                                       --> END

opportunity_intel and prioritization are registered and declared as valid
conditional targets (so they are wired, not orphaned) but the router never
selects them in Sprint 2 — they return to supervisor if ever reached.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph.checkpoint import get_checkpointer
from graph.nodes import (
    job_ingestion_node,
    opportunity_intel_node,
    prioritization_node,
    output_experience_node,
    profile_strategy_node,
    recommendations_node,
    scoring_node,
    supervisor_node,
    taxonomy_node,
)
from graph.ingestion_writable import acquire_jobs_node
from graph.research_writable import research_node
from graph.routing import route_from_supervisor
from graph.terminal_stages import terminal_node
from graph.state import CareerState

# All workers are active in Sprint 3 (full path through prioritization).
# `acquire_jobs` is a write-capable node that the supervisor only routes to when
# ENABLE_ACQUISITION is set (graph.persistence.acquisition_enabled); otherwise it
# is a registered-but-never-selected target (orphan-safe), so the default graph
# is unchanged.
_WORKERS = {
    "profile_strategy": profile_strategy_node,
    "acquire_jobs": acquire_jobs_node,
    "job_ingestion": job_ingestion_node,
    "taxonomy": taxonomy_node,
    "scoring": scoring_node,
    "research": research_node,
    "opportunity_intel": opportunity_intel_node,
    "prioritization": prioritization_node,
    "recommendations": recommendations_node,
    "terminal": terminal_node,
    "output_experience": output_experience_node,
}

# No deferred workers remain in Sprint 3.
_DEFERRED: dict = {}


def build_graph(checkpointer=None, interrupt_before: list[str] | None = None,
                planner_mode: bool = False):
    """Compile and return the runnable graph.

    Args:
        checkpointer: a langgraph checkpointer. Defaults to the persistent
            SqliteSaver from get_checkpointer().
        interrupt_before: optional list of node names to pause before
            (human-in-the-loop scaffolding). Resume with invoke(None, config).
        planner_mode: when True, insert a planner stage (START -> planner ->
            supervisor) that builds an ExecutionPlan from ``user_query``; the
            supervisor then routes by task dependencies. When False (default)
            the graph is identical to before: START -> supervisor with
            phase-based routing. Routing auto-selects its mode by whether an
            ``execution_plan`` is present in state, so either entry point works.
    """
    g = StateGraph(CareerState)

    g.add_node("supervisor", supervisor_node)
    for name, fn in {**_WORKERS, **_DEFERRED}.items():
        g.add_node(name, fn)

    if planner_mode:
        from graph.planner import planner_node
        g.add_node("planner", planner_node)
        g.add_edge(START, "planner")
        g.add_edge("planner", "supervisor")
    else:
        g.add_edge(START, "supervisor")

    # Conditional fan-out from the supervisor. All worker nodes (active +
    # deferred) are declared targets so none are orphaned; END terminates.
    path_map = {name: name for name in {**_WORKERS, **_DEFERRED}}
    path_map[END] = END
    g.add_conditional_edges("supervisor", route_from_supervisor, path_map)

    # Every worker returns to the supervisor (hub-and-spoke).
    for name in {**_WORKERS, **_DEFERRED}:
        g.add_edge(name, "supervisor")

    if checkpointer is None:
        checkpointer = get_checkpointer()

    return g.compile(checkpointer=checkpointer, interrupt_before=interrupt_before or [])
