"""Experiment framework package (v4).

A/B testing for scoring weights, resume/cover-letter prompts, and recommendation
logic. See experiments.framework for the public API.
"""
from experiments.framework import (
    Experiment,
    assign_variant,
    create_experiment,
    decide_winner,
    record_metric,
    results,
)

__all__ = [
    "Experiment",
    "create_experiment",
    "assign_variant",
    "record_metric",
    "results",
    "decide_winner",
]
