"""Offline training + online inference (v5.1).

A dependency-free learning-to-rank trainer: it fits per-feature linear weights
from a feature set using each feature's correlation with the binary label, then
registers the artifact in the ModelRegistry. Offline evaluation reports ranking
quality (pairwise accuracy). InferenceService scores feature dicts online.

This is intentionally simple and pure (no numpy/sklearn) so it runs anywhere;
swap the trainer for a real LTR model behind the same register/predict surface.
"""
from __future__ import annotations

from statistics import mean

from ml_platform.feature_store import FeatureStore
from ml_platform.registry import ModelRegistry


def _correlation(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = mean(xs), mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    return cov / (vx * vy) if vx and vy else 0.0


class TrainingPipeline:
    def __init__(self, registry: ModelRegistry | None = None,
                 store: FeatureStore | None = None):
        self.registry = registry or ModelRegistry()
        self.store = store or FeatureStore()

    def train_ltr(self, model_name: str, feature_set: str,
                  feature_columns: list[str], label_column: str = "label",
                  version: int | None = None) -> dict:
        """Fit linear LTR weights and register the model. Returns the artifact."""
        rows = self.store.materialize(feature_set, version)
        if not rows:
            raise ValueError(f"feature set {feature_set!r} has no data")
        labels = [float(r.get(label_column, 0)) for r in rows]
        weights: dict[str, float] = {}
        for col in feature_columns:
            xs = [float(r.get(col, 0) or 0) for r in rows]
            weights[col] = round(_correlation(xs, labels), 4)

        artifact = {"weights": weights, "features": feature_columns, "label": label_column}
        lineage = {"feature_set": feature_set, "feature_set_version": version,
                   "rows": len(rows)}
        metadata = {"type": "linear_ltr", "n_features": len(feature_columns)}
        model_version = self.registry.register(model_name, artifact, metadata, lineage)
        artifact["model_version"] = model_version
        return artifact

    def offline_evaluate(self, model_name: str, feature_set: str,
                         label_column: str = "label", version: int | None = None) -> dict:
        """Pairwise ranking accuracy: fraction of (pos, neg) pairs ordered correctly."""
        model = self.registry.get(model_name)
        if not model:
            raise KeyError(f"no model {model_name}")
        svc = InferenceService(model)
        rows = self.store.materialize(feature_set, version)
        scored = [(svc.predict(r), float(r.get(label_column, 0))) for r in rows]
        pos = [s for s, lab in scored if lab >= 0.5]
        neg = [s for s, lab in scored if lab < 0.5]
        if not pos or not neg:
            return {"pairwise_accuracy": 0.0, "pairs": 0, "samples": len(rows)}
        correct = sum(1 for p in pos for n in neg if p > n)
        total = len(pos) * len(neg)
        return {"pairwise_accuracy": round(correct / total, 4), "pairs": total,
                "samples": len(rows)}


class InferenceService:
    """Online inference over a registered linear-LTR artifact."""

    def __init__(self, model: dict):
        artifact = model.get("artifact", model)
        self.weights: dict[str, float] = artifact.get("weights", {})

    def predict(self, features: dict) -> float:
        return round(sum(self.weights.get(k, 0.0) * float(features.get(k, 0) or 0)
                         for k in self.weights), 4)

    def rank(self, items: list[dict]) -> list[dict]:
        scored = [{**it, "_score": self.predict(it)} for it in items]
        return sorted(scored, key=lambda r: r["_score"], reverse=True)
