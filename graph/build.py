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
from graph.routing import route_from_supervisor
from graph.state import CareerState

# All workers are active in Sprint 3 (full path through prioritization).
_WORKERS = {
    "profile_strategy": profile_strategy_node,
    "job_ingestion": job_ingestion_node,
    "taxonomy": taxonomy_node,
    "scoring": scoring_node,
    "opportunity_intel": opportunity_intel_node,
    "prioritization": prioritization_node,
    "recommendations": recommendations_node,
    "output_experience": output_experience_node,
}

# No deferred workers remain in Sprint 3.
_DEFERRED: dict = {}


def build_graph(checkpointer=None, interrupt_before: list[str] | None = None):
    """Compile and return the runnable graph.

    Args:
        checkpointer: a langgraph checkpointer. Defaults to the persistent
            SqliteSaver from get_checkpointer().
        interrupt_before: optional list of node names to pause before
            (human-in-the-loop scaffolding). Resume with invoke(None, config).
    """
    g = StateGraph(CareerState)

    g.add_node("supervisor", supervisor_node)
    for name, fn in {**_WORKERS, **_DEFERRED}.items():
        g.add_node(name, fn)

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
