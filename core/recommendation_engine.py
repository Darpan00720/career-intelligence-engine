"""Recommendation Engine (v3) — computed, explainable next-best-action per job.

Pure functions only: given a scored job dict (plus optional profile + a set of
job_ids that already have company research), produce an action recommendation
and a list of human-readable reasons. No DB writes, no LLM calls, no mutation
of inputs — these are computed export/view-layer fields.

    from core.recommendation_engine import recommend, recommend_all
    rec = recommend(job_dict, profile=profile, has_research=True)
    rows = recommend_all(ranked_rows, profile=profile, research_job_ids={1, 5})
"""
from __future__ import annotations

from core.scorer import application_priority

# Ordered by action priority (index 0 = most urgent) — also the dashboard sort key.
RECOMMENDATIONS: list[str] = [
    "Apply Immediately",
    "Apply This Week",
    "Find Referral First",
    "Research Company",
    "Save For Later",
    "Skip",
]
RECOMMENDATION_ORDER: dict[str, int] = {r: i for i, r in enumerate(RECOMMENDATIONS)}

# Score bands
_STRONG_SCORE = 90
_APPLY_SCORE = 70
_SAVE_SCORE = 50

_PRIMARY_TRACKS = {"ai_strategy", "product_management", "digital_transformation", "business_strategy"}
_AI_TRACKS = {"ai_strategy", "product_management", "digital_transformation"}

# Statuses meaning "already in the pipeline" → no new apply action needed.
_ADVANCED_STATUSES = {
    "Applied", "Online Assessment", "Interview", "Final Round", "Offer", "Rejected",
}
_POSITIVE_SPONSORSHIP = {"Likely", "High"}


def _sponsorship(job: dict) -> str:
    """Read sponsorship from an enriched row, else derive from the visa gate."""
    if job.get("sponsorship"):
        return job["sponsorship"]
    gate = (job.get("visa_gate") or "").upper()
    return {"PASS": "Likely", "FAIL": "No"}.get(gate, "Unknown")


def _geo_tokens(profile: dict | None) -> set[str]:
    """Flatten profile.target_geography into a set of lowercase string tokens."""
    if not profile:
        return set()
    tokens: set[str] = set()

    def _walk(obj):
        if isinstance(obj, str):
            tokens.add(obj.lower().strip())
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    _walk(profile.get("target_geography"))
    return {t for t in tokens if t}


def _matches_geo(job: dict, tokens: set[str]) -> bool:
    if not tokens:
        return False
    haystack = f"{job.get('location') or ''} {job.get('country') or ''}".lower()
    return any(tok in haystack for tok in tokens)


def _positive_signals(job: dict, profile: dict | None) -> list[str]:
    score = job.get("total_score") or 0
    role = (job.get("role_category") or "").strip()
    reasons: list[str] = []

    if score >= 80 or role in _PRIMARY_TRACKS:
        reasons.append("Strong role fit")
    if _sponsorship(job) in _POSITIVE_SPONSORSHIP:
        reasons.append("High sponsorship likelihood")
    if role in _AI_TRACKS:
        reasons.append("AI initiatives align with MBA specialization")
    if _matches_geo(job, _geo_tokens(profile)):
        reasons.append("Target geography match")
    if (job.get("work_mode") or "") in ("Remote", "Hybrid"):
        reasons.append("Flexible work mode")
    return reasons


def recommend(job: dict, profile: dict | None = None, has_research: bool | None = None) -> dict:
    """Return {'recommendation': str, 'recommendation_reason': [str, ...]} for a job.

    has_research overrides any 'has_research' flag already on the job dict.
    """
    score = job.get("total_score") or 0
    status = job.get("application_status") or "Not Started"
    sponsorship = _sponsorship(job)
    if has_research is None:
        has_research = bool(job.get("has_research"))

    # Already in the funnel → no new apply action.
    if status in _ADVANCED_STATUSES:
        return {"recommendation": "Skip",
                "recommendation_reason": [f"Already in pipeline ({status})"]}

    if score < _SAVE_SCORE:
        return {"recommendation": "Skip",
                "recommendation_reason": ["Score below strategic threshold"]}

    if score < _APPLY_SCORE:
        return {"recommendation": "Save For Later",
                "recommendation_reason": ["Moderate fit — revisit if capacity allows"]}

    # score >= 70 → actionable. Decide the action, then attach positive signals.
    signals = _positive_signals(job, profile)

    if sponsorship == "No":
        rec = "Find Referral First"
        reasons = ["Sponsorship unlikely — secure a referral to strengthen the case"] + signals
    elif not has_research and score >= 80:
        rec = "Research Company"
        reasons = ["High-fit role — research the company before applying"] + signals
    elif score >= _STRONG_SCORE:
        rec = "Apply Immediately"
        reasons = signals or ["Top-tier match"]
    else:
        rec = "Apply This Week"
        reasons = signals or ["Strong match"]

    # De-duplicate while preserving order.
    seen: set[str] = set()
    deduped = [r for r in reasons if not (r in seen or seen.add(r))]
    return {"recommendation": rec, "recommendation_reason": deduped}


def recommend_all(rows: list[dict], profile: dict | None = None,
                  research_job_ids: set[int] | None = None) -> list[dict]:
    """Attach recommendation fields to copies of rows (input not mutated).

    research_job_ids: ids of jobs that already have company research, used to
    decide between 'Research Company' and an apply action.
    """
    research_job_ids = research_job_ids or set()
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        rec = recommend(item, profile=profile,
                        has_research=item["id"] in research_job_ids if "id" in item else None)
        item["recommendation"] = rec["recommendation"]
        item["recommendation_reason"] = rec["recommendation_reason"]
        item["recommendation_rank"] = RECOMMENDATION_ORDER[rec["recommendation"]]
        out.append(item)
    return out


def sort_for_dashboard(rows: list[dict]) -> list[dict]:
    """Sort recommendation-enriched rows: recommendation priority, score DESC,
    date-found DESC. Pure — returns a new list."""
    return sorted(
        rows,
        key=lambda r: (
            r.get("recommendation_rank", len(RECOMMENDATIONS)),
            -(r.get("total_score") or 0),
            _neg_date(r.get("fetched_date")),
        ),
    )


def _neg_date(date_str):
    """Sort helper: most-recent date first within equal scores."""
    # ISO date strings sort lexicographically; invert by mapping to a tuple that
    # sorts descending when used in an ascending sort.
    return tuple(-ord(c) for c in (date_str or ""))


# ════════════════════════════════════════════════════════════════════════════════
# v5.5 — Personalized, explainable Recommendation Engine
#
# The v3 helpers above (recommend / recommend_all / sort_for_dashboard) are pure
# per-job view fields and remain unchanged for backward compatibility. The classes
# below add a full personalized recommendation platform — job, career-path and
# learning recommendations with ranking, explainability, confidence scores and
# feedback incorporation — composing the v5.5 career-graph / skill / market layers.
# ════════════════════════════════════════════════════════════════════════════════

import uuid as _uuid
from dataclasses import dataclass as _dataclass, field as _field

from core import database as _db, tenancy as _tenancy
from core.career_graph import CareerGraph as _CareerGraph, slugify as _slugify
from core.skill_intelligence import (
    SkillGapAnalyzer as _SkillGapAnalyzer,
    SkillProfiler as _SkillProfiler,
)

JOB = "job"
CAREER_PATH = "career_path"
LEARNING = "learning"


@_dataclass
class RecommendationItem:
    rec_type: str
    target: str
    score: float
    confidence: float = 0.0
    reasons: list = _field(default_factory=list)
    meta: dict = _field(default_factory=dict)
    recommendation_id: str = ""

    def to_dict(self) -> dict:
        return {"recommendation_id": self.recommendation_id, "rec_type": self.rec_type,
                "target": self.target, "score": round(self.score, 4),
                "confidence": round(self.confidence, 4), "reasons": self.reasons,
                "meta": self.meta}


class OpportunityScorer:
    """Blend skill coverage, market demand and salary fit into a 0-100 score."""

    # weights sum to 1.0
    W_COVERAGE = 0.55
    W_DEMAND = 0.30
    W_SALARY = 0.15

    def score(self, *, coverage: float, demand: int = 0, demand_max: int = 1,
              salary_fit: float = 0.5) -> float:
        demand_norm = (demand / demand_max) if demand_max else 0.0
        raw = (self.W_COVERAGE * coverage + self.W_DEMAND * min(1.0, demand_norm)
               + self.W_SALARY * max(0.0, min(1.0, salary_fit)))
        return round(raw * 100, 2)


class RecommendationExplainer:
    def explain(self, item: RecommendationItem) -> str:
        if item.reasons:
            return " ".join(item.reasons)
        return f"Recommended {item.target} (score {item.score:.0f}/100)."

    def build_reasons(self, *, coverage: float, demand: int, gaps: list) -> list[str]:
        reasons = [f"{coverage:.0%} skill-match with the role."]
        if demand:
            reasons.append(f"Strong market demand ({demand} recent postings).")
        if gaps:
            reasons.append(f"{len(gaps)} skill gap(s) to close: "
                           + ", ".join(g['skill'] for g in gaps[:3]) + ".")
        else:
            reasons.append("No remaining skill gaps — apply-ready.")
        return reasons


class RecommendationRanker:
    """Rank items by score, adjusted by accumulated feedback (online learning)."""

    def rank(self, items: list[RecommendationItem],
             feedback_weights: dict[str, float] | None = None) -> list[RecommendationItem]:
        feedback_weights = feedback_weights or {}
        def key(it: RecommendationItem):
            adj = feedback_weights.get(it.target, 0.0)
            return -(it.score + adj)
        return sorted(items, key=key)


class LearningPathGenerator:
    def __init__(self, graph: _CareerGraph | None = None,
                 gap_analyzer: _SkillGapAnalyzer | None = None):
        self.graph = graph or _CareerGraph()
        self.gap = gap_analyzer or _SkillGapAnalyzer(self.graph)

    def generate(self, skill_profile, target_role: str, *, profile_id: str = "",
                 persist: bool = False, tenant_id: str | None = None) -> dict:
        analysis = self.gap.analyze(skill_profile, target_role, tenant_id=tenant_id)
        steps = []
        order = 0
        for g in analysis["gaps"]:
            # learn prerequisites before the gap skill itself
            for prereq in g["prerequisites"]:
                order += 1
                steps.append({"order": order, "skill": prereq, "kind": "prerequisite"})
            order += 1
            steps.append({"order": order, "skill": g["skill"], "kind": "target_skill"})
        path = {"path_id": _uuid.uuid4().hex, "target_role": _slugify(target_role),
                "profile_id": profile_id, "steps": steps, "total_steps": len(steps),
                "coverage": analysis["coverage"]}
        if persist:
            self._save(path, tenant_id=tenant_id)
        return path

    def _save(self, path: dict, *, tenant_id: str | None = None) -> None:
        tenant_id = tenant_id or _tenancy.current_tenant()
        with _db.get_connection() as conn:
            conn.execute(
                "INSERT INTO learning_paths "
                "(path_id, tenant_id, profile_id, target_role, steps, total_steps) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (path["path_id"], tenant_id, path["profile_id"], path["target_role"],
                 _json.dumps(path["steps"]), path["total_steps"]))


class RecommendationEngine:
    """Facade producing personalized, explainable, feedback-aware recommendations."""

    def __init__(self, *, graph: _CareerGraph | None = None,
                 scorer: OpportunityScorer | None = None,
                 ranker: RecommendationRanker | None = None,
                 explainer: RecommendationExplainer | None = None,
                 gap_analyzer: _SkillGapAnalyzer | None = None,
                 profiler: _SkillProfiler | None = None):
        self.graph = graph or _CareerGraph()
        self.scorer = scorer or OpportunityScorer()
        self.ranker = ranker or RecommendationRanker()
        self.explainer = explainer or RecommendationExplainer()
        self.gap = gap_analyzer or _SkillGapAnalyzer(self.graph)
        self.profiler = profiler or _SkillProfiler()
        self.learning = LearningPathGenerator(self.graph, self.gap)

    def recommend_roles(self, normalized_profile: dict, *, candidate_roles=None,
                        demand: dict[str, int] | None = None, persist: bool = False,
                        tenant_id: str | None = None) -> list[RecommendationItem]:
        demand = demand or {}
        demand_max = max(demand.values()) if demand else 1
        skill_profile = self.profiler.build(normalized_profile)
        roles = candidate_roles or [r.slug for r in self.graph.roles.list_roles(tenant_id=tenant_id)]
        items: list[RecommendationItem] = []
        for role in roles:
            analysis = self.gap.analyze(skill_profile, role, tenant_id=tenant_id)
            d = demand.get(_slugify(role), 0)
            score = self.scorer.score(coverage=analysis["coverage"], demand=d,
                                      demand_max=demand_max)
            reasons = self.explainer.build_reasons(
                coverage=analysis["coverage"], demand=d, gaps=analysis["gaps"])
            # confidence rises with coverage and (inverse) gap count
            confidence = round(min(1.0, 0.4 + 0.6 * analysis["coverage"]), 4)
            items.append(RecommendationItem(
                CAREER_PATH, _slugify(role), score, confidence, reasons,
                {"coverage": analysis["coverage"], "gaps": analysis["gaps"], "demand": d},
                recommendation_id=_uuid.uuid4().hex))
        ranked = self.ranker.rank(items, self.feedback_weights(tenant_id=tenant_id))
        if persist:
            for it in ranked:
                self._save(it, normalized_profile.get("profile_id", ""), tenant_id=tenant_id)
        return ranked

    def recommend_learning(self, normalized_profile: dict, target_role: str, *,
                           persist: bool = False, tenant_id: str | None = None) -> dict:
        skill_profile = self.profiler.build(normalized_profile)
        return self.learning.generate(
            skill_profile, target_role,
            profile_id=normalized_profile.get("profile_id", ""),
            persist=persist, tenant_id=tenant_id)

    # ── feedback incorporation ───────────────────────────────────────────────────
    def record_feedback(self, recommendation_id: str, signal: str, *, weight: float = 1.0,
                        profile_id: str = "", tenant_id: str | None = None) -> None:
        tenant_id = tenant_id or _tenancy.current_tenant()
        with _db.get_connection() as conn:
            conn.execute(
                "INSERT INTO recommendation_feedback "
                "(tenant_id, recommendation_id, profile_id, signal, weight) "
                "VALUES (?, ?, ?, ?, ?)",
                (tenant_id, recommendation_id, profile_id, signal, weight))

    _POSITIVE = {"accepted", "applied", "saved"}
    _NEGATIVE = {"rejected", "dismissed"}

    def feedback_weights(self, *, tenant_id: str | None = None) -> dict[str, float]:
        """Per-target score adjustment learned from feedback: +/- up to ~10 points."""
        tenant_id = tenant_id or _tenancy.current_tenant()
        with _db.get_connection() as conn:
            rows = conn.execute(
                "SELECT r.target AS target, f.signal AS signal, f.weight AS weight "
                "FROM recommendation_feedback f JOIN recommendations r "
                "ON f.recommendation_id = r.recommendation_id "
                "WHERE f.tenant_id = ?", (tenant_id,)).fetchall()
        weights: dict[str, float] = {}
        for r in rows:
            delta = r["weight"] if r["signal"] in self._POSITIVE else (
                -r["weight"] if r["signal"] in self._NEGATIVE else 0.0)
            weights[r["target"]] = weights.get(r["target"], 0.0) + 5.0 * delta
        return weights

    def _save(self, item: RecommendationItem, profile_id: str, *,
              tenant_id: str | None = None) -> None:
        tenant_id = tenant_id or _tenancy.current_tenant()
        with _db.get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO recommendations "
                "(recommendation_id, tenant_id, profile_id, rec_type, target, score, "
                "confidence, explanation) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (item.recommendation_id, tenant_id, profile_id, item.rec_type,
                 item.target, item.score, item.confidence, self.explainer.explain(item)))


import json as _json  # noqa: E402  (used by LearningPathGenerator._save)
