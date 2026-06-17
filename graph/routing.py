"""Supervisor routing: phase-based next-node selection (no LLM).

Phase-based (not output-presence-based) progression makes routing robust to
empty results: a node that legitimately produces an empty list still advances
the phase, so the graph never loops. Each worker sets `phase` to its own phase
on completion; the supervisor maps the last-completed phase to the next node.

Full Sprint-3 path:
  profile -> ingestion -> taxonomy -> scoring -> intelligence -> prioritization -> END
"""
from __future__ import annotations

from langgraph.graph import END

from graph.state import CareerState
from schemas.control import Phase

# last-completed phase  ->  next node to run
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


def route_from_supervisor(state: CareerState) -> str:
    """Return the next node (or END) based on the last completed phase."""
    return _NEXT_NODE.get(state.get("phase"), END)
