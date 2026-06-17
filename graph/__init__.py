"""Career Agent LangGraph layer: state, reducers, config, checkpointing.

Agent nodes and graph wiring (build.py, routing.py) arrive in Sprint 2.
This package imports cleanly without langgraph installed.
"""
from graph.state import (
    CareerState,
    CareerStateModel,
    new_career_state,
    validate_state,
)

__all__ = [
    "CareerState",
    "CareerStateModel",
    "new_career_state",
    "validate_state",
]
