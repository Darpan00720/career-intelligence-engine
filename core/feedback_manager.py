"""Feedback & Experimentation Platform (v5.5).

Closes the loop on recommendations: capture feedback, evaluate quality online
and offline, run A/B tests over ranking strategies, and gate features.

  FeedbackManager        — record recommendation feedback + acceptance metrics
  RecommendationEvaluator— offline (precision@k, NDCG) + online (acceptance) eval
  ExperimentManager      — experiments, deterministic variant assignment, events
  ABTestingFramework     — per-variant conversion + winner selection (with lift)
  FeatureFlagManager     — tenant feature flags with deterministic % rollout

Reuses the existing ``experiments`` / ``experiment_events`` tables + DB helpers
and the v5.5 ``recommendation_feedback`` / ``feature_flags`` tables. Tenant-scoped.
"""
from __future__ import annotations

import hashlib
import json
import math

from core import database, tenancy

POSITIVE = {"accepted", "applied", "saved", "clicked"}
NEGATIVE = {"rejected", "dismissed", "ignored"}


def _bucket(key: str, salt: str = "") -> float:
    """Deterministic uniform value in [0, 1) for stable assignment/rollout."""
    h = hashlib.sha256(f"{salt}:{key}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


# ── Feedback ─────────────────────────────────────────────────────────────────────

class FeedbackManager:
    def record(self, recommendation_id: str, signal: str, *, profile_id: str = "",
               weight: float = 1.0, tenant_id: str | None = None) -> None:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO recommendation_feedback "
                "(tenant_id, recommendation_id, profile_id, signal, weight) "
                "VALUES (?, ?, ?, ?, ?)",
                (tenant_id, recommendation_id, profile_id, signal, weight))

    def signals_for(self, recommendation_id: str, *,
                    tenant_id: str | None = None) -> list[dict]:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT signal, weight, created_at FROM recommendation_feedback "
                "WHERE tenant_id = ? AND recommendation_id = ? ORDER BY id",
                (tenant_id, recommendation_id)).fetchall()
        return [dict(r) for r in rows]

    def acceptance_rate(self, *, tenant_id: str | None = None) -> float:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT signal FROM recommendation_feedback WHERE tenant_id = ?",
                (tenant_id,)).fetchall()
        decided = [r["signal"] for r in rows if r["signal"] in POSITIVE | NEGATIVE]
        if not decided:
            return 0.0
        positives = sum(1 for s in decided if s in POSITIVE)
        return round(positives / len(decided), 4)


# ── Evaluation ─────────────────────────────────────────────────────────────────

class RecommendationEvaluator:
    """Offline ranking metrics + online acceptance, for continuous improvement."""

    def precision_at_k(self, ranked_ids: list[str], relevant_ids: set[str], k: int = 5) -> float:
        if k <= 0 or not ranked_ids:
            return 0.0
        top = ranked_ids[:k]
        return round(sum(1 for r in top if r in relevant_ids) / len(top), 4)

    def ndcg_at_k(self, ranked_ids: list[str], relevant_ids: set[str], k: int = 5) -> float:
        top = ranked_ids[:k]
        dcg = sum((1.0 / math.log2(i + 2)) for i, r in enumerate(top) if r in relevant_ids)
        ideal_hits = min(len(relevant_ids), k)
        idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
        return round(dcg / idcg, 4) if idcg else 0.0

    def online_acceptance(self, feedback: FeedbackManager | None = None, *,
                          tenant_id: str | None = None) -> float:
        return (feedback or FeedbackManager()).acceptance_rate(tenant_id=tenant_id)


# ── Experiments ────────────────────────────────────────────────────────────────

class ExperimentManager:
    def create(self, experiment_id: str, name: str, variants: list[str]) -> None:
        database.upsert_experiment(experiment_id, name, variants)

    def assign(self, experiment_id: str, unit_id: str) -> str:
        """Deterministically assign a unit (user/session) to a variant."""
        exp = database.get_experiment(experiment_id)
        if not exp or not exp.get("variants"):
            raise KeyError(f"unknown experiment {experiment_id!r}")
        variants = exp["variants"]
        if isinstance(variants, str):           # DB stores variants as JSON
            variants = json.loads(variants)
        idx = int(_bucket(unit_id, experiment_id) * len(variants))
        return variants[min(idx, len(variants) - 1)]

    def record_event(self, experiment_id: str, variant: str, metric: str,
                     value: float = 1.0) -> None:
        database.record_experiment_event(experiment_id, variant, metric, value)

    def results(self, experiment_id: str, metric: str = "conversion") -> dict[str, dict]:
        events = database.get_experiment_events(experiment_id)
        agg: dict[str, dict] = {}
        for e in events:
            if e["metric"] != metric:
                continue
            v = agg.setdefault(e["variant"], {"n": 0, "sum": 0.0})
            v["n"] += 1
            v["sum"] += e["value"]
        for v in agg.values():
            v["rate"] = round(v["sum"] / v["n"], 4) if v["n"] else 0.0
        return agg


class ABTestingFramework:
    """Thin layer over :class:`ExperimentManager` adding winner selection + lift."""

    def __init__(self, experiments: ExperimentManager | None = None):
        self.experiments = experiments or ExperimentManager()

    def conversion(self, experiment_id: str, metric: str = "conversion") -> dict[str, dict]:
        return self.experiments.results(experiment_id, metric)

    def winner(self, experiment_id: str, metric: str = "conversion", *,
               min_samples: int = 1) -> dict | None:
        results = self.conversion(experiment_id, metric)
        eligible = {k: v for k, v in results.items() if v["n"] >= min_samples}
        if not eligible:
            return None
        best = max(eligible, key=lambda k: eligible[k]["rate"])
        baseline = min(eligible, key=lambda k: eligible[k]["rate"])
        base_rate = eligible[baseline]["rate"]
        lift = (eligible[best]["rate"] - base_rate) / base_rate if base_rate else float("inf")
        return {"variant": best, "rate": eligible[best]["rate"],
                "lift_over_worst": round(lift, 4) if lift != float("inf") else None,
                "samples": eligible[best]["n"]}

    def conclude(self, experiment_id: str, metric: str = "conversion") -> str | None:
        win = self.winner(experiment_id, metric)
        if win:
            database.set_experiment_winner(experiment_id, win["variant"])
            return win["variant"]
        return None


# ── Feature flags ────────────────────────────────────────────────────────────────

class FeatureFlagManager:
    """Tenant-scoped flags with deterministic percentage rollout."""

    def set_flag(self, flag: str, *, enabled: bool = True, rollout: float = 1.0,
                 tenant_id: str | None = None) -> None:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO feature_flags (tenant_id, flag, enabled, rollout) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, flag) DO UPDATE SET "
                "enabled=excluded.enabled, rollout=excluded.rollout, "
                "updated_at=DATETIME('now')",
                (tenant_id, flag, 1 if enabled else 0, rollout))

    def _get(self, flag: str, tenant_id: str) -> dict | None:
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT enabled, rollout FROM feature_flags WHERE tenant_id = ? AND flag = ?",
                (tenant_id, flag)).fetchone()
        return dict(row) if row else None

    def is_enabled(self, flag: str, *, unit_id: str | None = None,
                   tenant_id: str | None = None, default: bool = False) -> bool:
        tenant_id = tenant_id or tenancy.current_tenant()
        row = self._get(flag, tenant_id)
        if not row:
            return default
        if not row["enabled"]:
            return False
        rollout = row["rollout"]
        if rollout >= 1.0 or unit_id is None:
            return rollout >= 1.0 if unit_id is None else True
        return _bucket(unit_id, flag) < rollout
