"""Skills Intelligence Engine (v5.5).

Profiles a candidate's skills, estimates proficiency, matches against role
requirements semantically, and produces an explainable skill-gap analysis.

  ProficiencyEstimator  — evidence (mentions + tenure) → 0-100 level + band
  SkillSimilarityEngine — embedding cosine similarity + greedy clustering
  SkillMatcher          — semantic match of candidate vs. required skills
  SkillProfiler         — aggregate a normalized profile into a skill profile
  SkillGapAnalyzer      — prioritized, explainable gaps for a target role

Embeddings reuse the v5.4 ``EmbeddingService`` (bag-of-words by default,
injectable) so this works offline and a real embedding model can be dropped in
behind the same port.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.career_graph import CareerGraph, slugify
from core.memory_manager import EmbeddingService

# proficiency bands by 0-100 score
_BANDS = [(85, "expert"), (65, "advanced"), (40, "intermediate"), (0, "beginner")]


def _band(score: float) -> str:
    for cutoff, name in _BANDS:
        if score >= cutoff:
            return name
    return "beginner"


class ProficiencyEstimator:
    """Estimate proficiency 0-100 from evidence: mention count + years of tenure."""

    def estimate(self, *, mentions: int = 1, experience_months: int = 0,
                 base_confidence: float = 0.6) -> dict:
        mention_pts = min(40, mentions * 12)
        tenure_pts = min(40, (experience_months / 12.0) * 8)
        conf_pts = base_confidence * 20
        score = round(min(100.0, mention_pts + tenure_pts + conf_pts), 2)
        return {"score": score, "band": _band(score),
                "evidence": {"mentions": mentions, "experience_months": experience_months}}


@dataclass
class SkillProfile:
    hard: list[dict] = field(default_factory=list)
    soft: list[dict] = field(default_factory=list)
    transferable: list[dict] = field(default_factory=list)

    def all_slugs(self) -> set[str]:
        return {s["skill"] for s in self.hard + self.soft + self.transferable}

    def by_slug(self) -> dict[str, dict]:
        return {s["skill"]: s for s in self.hard + self.soft + self.transferable}


class SkillProfiler:
    def __init__(self, estimator: ProficiencyEstimator | None = None):
        self.estimator = estimator or ProficiencyEstimator()

    def build(self, normalized_profile: dict) -> SkillProfile:
        exp_months = normalized_profile.get("experience_months", 0)
        prof = SkillProfile()
        for s in normalized_profile.get("skills", []):
            est = self.estimator.estimate(
                mentions=s.get("confidence", 0.6) and
                max(1, int(round(s.get("confidence", 0.6) * 3))),
                experience_months=exp_months, base_confidence=s.get("confidence", 0.6))
            entry = {"skill": s["skill"], "name": s.get("name", s["skill"]),
                     "proficiency": est["score"], "band": est["band"]}
            cat = s.get("category", "hard")
            getattr(prof, cat if cat in ("hard", "soft", "transferable") else "hard").append(entry)
        return prof


class SkillSimilarityEngine:
    def __init__(self, embedder: EmbeddingService | None = None):
        self.embedder = embedder or EmbeddingService()

    def similarity(self, a: str, b: str) -> float:
        if slugify(a) == slugify(b):
            return 1.0
        return round(self.embedder.cosine(self.embedder.embed(a), self.embedder.embed(b)), 4)

    def cluster(self, skills: list[str], *, threshold: float = 0.5) -> list[list[str]]:
        """Greedy single-pass clustering by pairwise similarity."""
        clusters: list[list[str]] = []
        for skill in skills:
            placed = False
            for cluster in clusters:
                if self.similarity(skill, cluster[0]) >= threshold:
                    cluster.append(skill)
                    placed = True
                    break
            if not placed:
                clusters.append([skill])
        return clusters


@dataclass
class SkillMatch:
    matched: list[dict]
    missing: list[dict]
    coverage: float

    @property
    def explanation(self) -> str:
        return (f"Matched {len(self.matched)}/{len(self.matched) + len(self.missing)} "
                f"required skills ({self.coverage:.0%} coverage).")


class SkillMatcher:
    def __init__(self, similarity: SkillSimilarityEngine | None = None, *,
                 threshold: float = 0.6):
        self.sim = similarity or SkillSimilarityEngine()
        self.threshold = threshold

    def match(self, candidate_skills, required_skills) -> SkillMatch:
        cand = [slugify(s) for s in candidate_skills]
        matched, missing = [], []
        for req in required_skills:
            req_slug = slugify(req)
            best_skill, best_score = None, 0.0
            for cs in cand:
                score = self.sim.similarity(cs, req_slug)
                if score > best_score:
                    best_skill, best_score = cs, score
            if best_score >= self.threshold:
                matched.append({"required": req_slug, "via": best_skill,
                                "similarity": round(best_score, 4)})
            else:
                missing.append({"required": req_slug, "closest": best_skill,
                                "similarity": round(best_score, 4)})
        total = len(required_skills) or 1
        return SkillMatch(matched, missing, round(len(matched) / total, 4))


class SkillGapAnalyzer:
    def __init__(self, graph: CareerGraph | None = None,
                 matcher: SkillMatcher | None = None):
        self.graph = graph or CareerGraph()
        self.matcher = matcher or SkillMatcher()

    def analyze(self, skill_profile: SkillProfile, target_role: str, *,
                tenant_id: str | None = None) -> dict:
        role = self.graph.roles.get_role(target_role, tenant_id=tenant_id)
        required = role.required_skills if role else []
        result = self.matcher.match(list(skill_profile.all_slugs()), required)
        # prioritize missing skills: those that unlock prerequisites first
        gaps = []
        for m in result.missing:
            chain = self.graph.paths.prerequisite_chain(m["required"], tenant_id=tenant_id)
            gaps.append({
                "skill": m["required"],
                "priority": "high" if not chain else "foundational",
                "prerequisites": chain,
                "closest_existing": m["closest"],
                "reason": (f"Required for {target_role}; "
                           + (f"build prerequisites {chain} first" if chain
                              else "no prerequisites — learn directly")),
            })
        gaps.sort(key=lambda g: (g["priority"] != "foundational", g["skill"]))
        return {
            "target_role": slugify(target_role),
            "coverage": result.coverage,
            "matched": result.matched,
            "gaps": gaps,
            "explanation": result.explanation,
        }
