"""AI Learning Layer (v5).

Learns from outcomes which companies/skills/score-bands/recommendations convert,
and provides optimization primitives:

  * feature_importance()   — interview-rate lift per feature value
  * EpsilonGreedyBandit / ThompsonBandit — multi-armed bandits for online
    optimization of recommendation strategies
  * calibration_metrics()  — how well score bands predict interviews (ECE)
  * learned_rank()         — learning-to-rank by learned feature weights

Pure where possible; the data-reading helpers use the feedback tables.
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field

from core import database

_INTERVIEW_PLUS = {"Interview", "Final Round", "Offer"}
_APPLIED_PLUS = {"Applied", "Online Assessment", "Interview", "Final Round", "Offer"}


def _score_band(score) -> str:
    s = score or 0
    if s >= 90: return "90-100"
    if s >= 80: return "80-89"
    if s >= 70: return "70-79"
    return "<70"


# ── Feature importance ──────────────────────────────────────────────────────────

def feature_importance(rows: list[dict] | None = None) -> dict[str, dict]:
    """For each feature (company, role_category, score_band, work_mode), return
    the interview rate per value and an overall importance = spread of rates."""
    rows = rows if rows is not None else database.get_outcomes_joined()
    features = {
        "company": lambda r: r.get("company"),
        "role_category": lambda r: r.get("role_category"),
        "score_band": lambda r: _score_band(r.get("total_score")),
        "work_mode": lambda r: r.get("work_mode") or "Unknown",
    }
    out: dict[str, dict] = {}
    for fname, fn in features.items():
        applied: dict[str, int] = defaultdict(int)
        interviews: dict[str, int] = defaultdict(int)
        for r in rows:
            if r.get("status") in _APPLIED_PLUS:
                v = fn(r) or "Unknown"
                applied[v] += 1
                if r.get("status") in _INTERVIEW_PLUS:
                    interviews[v] += 1
        rates = {v: round(interviews[v] / applied[v], 3) for v in applied if applied[v]}
        spread = round(max(rates.values()) - min(rates.values()), 3) if rates else 0.0
        out[fname] = {"rates": rates, "importance": spread}
    return out


# ── Multi-armed bandits ──────────────────────────────────────────────────────────

@dataclass
class EpsilonGreedyBandit:
    arms: list[str]
    epsilon: float = 0.1
    counts: dict[str, int] = field(default_factory=dict)
    values: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        for a in self.arms:
            self.counts.setdefault(a, 0)
            self.values.setdefault(a, 0.0)

    def select(self, rng: random.Random | None = None) -> str:
        rng = rng or random
        if rng.random() < self.epsilon:
            return rng.choice(self.arms)
        return self.best_arm()

    def update(self, arm: str, reward: float) -> None:
        self.counts[arm] += 1
        n = self.counts[arm]
        # Incremental mean.
        self.values[arm] += (reward - self.values[arm]) / n

    def best_arm(self) -> str:
        return max(self.arms, key=lambda a: self.values[a])


@dataclass
class ThompsonBandit:
    arms: list[str]
    alpha: dict[str, float] = field(default_factory=dict)
    beta: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        for a in self.arms:
            self.alpha.setdefault(a, 1.0)
            self.beta.setdefault(a, 1.0)

    def select(self, rng: random.Random | None = None) -> str:
        rng = rng or random
        samples = {a: rng.betavariate(self.alpha[a], self.beta[a]) for a in self.arms}
        return max(samples, key=samples.get)

    def update(self, arm: str, reward: float) -> None:
        # reward in [0,1]; Bernoulli-style update.
        self.alpha[arm] += reward
        self.beta[arm] += (1.0 - reward)

    def best_arm(self) -> str:
        return max(self.arms, key=lambda a: self.alpha[a] / (self.alpha[a] + self.beta[a]))


# ── Calibration ──────────────────────────────────────────────────────────────────

def calibration_metrics(rows: list[dict] | None = None) -> dict:
    """Expected Calibration Error between score-band predicted probability
    (band midpoint / 100) and observed interview rate."""
    rows = rows if rows is not None else database.get_outcomes_joined()
    band_mid = {"90-100": 0.95, "80-89": 0.85, "70-79": 0.75, "<70": 0.5}
    bins: dict[str, dict] = defaultdict(lambda: {"n": 0, "interviews": 0})
    for r in rows:
        if r.get("status") in _APPLIED_PLUS:
            band = _score_band(r.get("total_score"))
            bins[band]["n"] += 1
            if r.get("status") in _INTERVIEW_PLUS:
                bins[band]["interviews"] += 1
    total = sum(b["n"] for b in bins.values())
    ece = 0.0
    per_band = {}
    for band, b in bins.items():
        if not b["n"]:
            continue
        observed = b["interviews"] / b["n"]
        predicted = band_mid.get(band, 0.5)
        per_band[band] = {"predicted": predicted, "observed": round(observed, 3), "n": b["n"]}
        ece += (b["n"] / total) * abs(predicted - observed)
    return {"ece": round(ece, 4) if total else 0.0, "bins": per_band, "samples": total}


# ── Learning-to-rank ─────────────────────────────────────────────────────────────

def learned_rank(jobs: list[dict], weights: dict[str, float]) -> list[dict]:
    """Rank jobs by a linear score over weighted features (pure; non-mutating).

    Each job gets `_learned_score = sum(weights[f] * value(f))`; returns a new
    sorted list (desc). Supported features: total_score, has_research (0/1),
    sponsorship_pass (0/1).
    """
    def _feat(job: dict) -> dict:
        return {
            "total_score": (job.get("total_score") or 0) / 100.0,
            "has_research": 1.0 if job.get("has_research") else 0.0,
            "sponsorship_pass": 1.0 if (job.get("visa_gate") == "PASS") else 0.0,
        }

    scored = []
    for job in jobs:
        feats = _feat(job)
        item = dict(job)
        item["_learned_score"] = round(sum(weights.get(k, 0.0) * v for k, v in feats.items()), 4)
        scored.append(item)
    return sorted(scored, key=lambda r: r["_learned_score"], reverse=True)
