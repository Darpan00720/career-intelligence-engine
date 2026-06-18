"""Experiment framework (v4) — A/B testing with persisted assignments & metrics.

Generic and pure-ish: experiments and their events are persisted to SQLite, but
variant assignment is a deterministic hash (no DB needed, stable per unit) so
the same unit always lands in the same variant.

    exp = create_experiment("scoring_v2", ["control", "treatment"],
                            name="Scoring weight tweak")
    variant = assign_variant("scoring_v2", unit="job-42")
    record_metric("scoring_v2", variant, "interview_rate", 1.0)
    winner = decide_winner("scoring_v2", "interview_rate")  # higher is better
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from statistics import mean

from core import database


@dataclass
class Experiment:
    experiment_id: str
    name: str
    variants: list[str]
    status: str = "running"
    winner: str | None = None

    @classmethod
    def from_row(cls, row: dict) -> "Experiment":
        return cls(
            experiment_id=row["experiment_id"],
            name=row.get("name") or row["experiment_id"],
            variants=json.loads(row.get("variants") or "[]"),
            status=row.get("status") or "running",
            winner=row.get("winner"),
        )


def create_experiment(experiment_id: str, variants: list[str],
                      name: str | None = None) -> Experiment:
    if len(variants) < 2:
        raise ValueError("an experiment needs at least 2 variants")
    database.upsert_experiment(experiment_id, name or experiment_id, variants)
    return Experiment(experiment_id, name or experiment_id, list(variants))


def get_experiment(experiment_id: str) -> Experiment | None:
    row = database.get_experiment(experiment_id)
    return Experiment.from_row(row) if row else None


def assign_variant(experiment_id: str, unit: str) -> str:
    """Deterministically assign `unit` to a variant via stable hashing."""
    exp = get_experiment(experiment_id)
    if exp is None:
        raise KeyError(f"unknown experiment: {experiment_id}")
    digest = hashlib.sha256(f"{experiment_id}:{unit}".encode()).hexdigest()
    bucket = int(digest[:8], 16) % len(exp.variants)
    return exp.variants[bucket]


def record_metric(experiment_id: str, variant: str, metric: str, value: float) -> int:
    return database.record_experiment_event(experiment_id, variant, metric, float(value))


def results(experiment_id: str, metric: str | None = None) -> dict[str, dict]:
    """Aggregate recorded metrics per variant: {variant: {metric: {n, mean, sum}}}."""
    events = database.get_experiment_events(experiment_id)
    grouped: dict[str, dict[str, list[float]]] = {}
    for ev in events:
        if metric and ev["metric"] != metric:
            continue
        grouped.setdefault(ev["variant"], {}).setdefault(ev["metric"], []).append(ev["value"])

    out: dict[str, dict] = {}
    for variant, metrics in grouped.items():
        out[variant] = {
            m: {"n": len(vals), "mean": round(mean(vals), 4), "sum": round(sum(vals), 4)}
            for m, vals in metrics.items()
        }
    return out


def decide_winner(experiment_id: str, metric: str, higher_is_better: bool = True) -> str | None:
    """Pick the variant with the best mean for `metric`, persist it, return it."""
    agg = results(experiment_id, metric)
    candidates = {v: m[metric]["mean"] for v, m in agg.items() if metric in m}
    if not candidates:
        return None
    winner = (max if higher_is_better else min)(candidates, key=candidates.get)
    database.set_experiment_winner(experiment_id, winner)
    return winner
