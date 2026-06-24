"""Node wrappers around the EXISTING core/ and agents/ business modules.

Wrapper boundary rules (enforced here):
  - call existing modules; transform outputs into schemas; catch exceptions;
    append AgentError; write ONLY schema objects to state.
  - NO new business rules / formulas / thresholds / duplicated algorithms.

Every node:
  - retries its core call up to MAX_NODE_RETRIES (bumping retry_count),
  - on exhaustion appends an AgentError and a schema-valid fallback,
  - ALWAYS advances `phase` so the graph never loops or crashes.

Nodes 6-7 (intelligence/prioritization) are deterministic ADAPTERS over the
existing scorer component outputs + the declared COMPOSITE_WEIGHTS, because no
standalone implementation of those exists yet (see Sprint-3 report). They add
no new thresholds beyond the already-finalised weights.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.logging_config import get_logger
from graph.config import MAX_NODE_RETRIES
from graph.providers import get_claude_hook, get_intel_hook, get_provider
from graph.state import CareerState
from schemas.control import AgentError, Phase, Track
from schemas.jobs import (
    ClassifiedJob,
    GateResult,
    IngestedJob,
    IngestionStats,
    TaxonomyStats,
)
from schemas.profile import CareerProfile, TrackStrategy
from schemas.scoring import PriorityBucket, ScoreComponents, ScoredJob, ScoringStats

logger = get_logger(__name__)

EXECUTION_LOG: list[str] = []


def reset_execution_log() -> None:
    EXECUTION_LOG.clear()


def _enter(name: str) -> None:
    EXECUTION_LOG.append(name)
    logger.info("node: %s", name)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# --- Supervisor (rule-based, no LLM) -------------------------------------- #
def supervisor_node(state: CareerState) -> dict:
    _enter("supervisor")
    errs = state.get("errors") or []
    if errs:
        logger.info("supervisor: %d error(s) recorded so far", len(errs))
    update: dict = {"audit_log": ["supervisor"]}

    # v6 Phase 3: when an execution plan is present, mark any plan task whose
    # node has already executed (recorded in audit_log) as completed. Only the
    # newly-finished tasks are emitted — the `completed_tasks` reducer (extend)
    # accumulates them, so this stays idempotent and duplicate-free. Without a
    # plan this block is skipped and behaviour is exactly the legacy path.
    from graph.routing import as_plan  # local import: avoid import cycle at load
    plan = as_plan(state.get("execution_plan"))
    if plan is not None:
        completed = set(state.get("completed_tasks") or [])
        ran = set(state.get("audit_log") or [])
        newly = [t.id for t in plan.tasks if t.agent in ran and t.id not in completed]
        if newly:
            update["completed_tasks"] = newly
            update["current_task"] = newly[-1]
    return update


# --- 1. Profile & Career Strategy ----------------------------------------- #
_FALLBACK_PROFILE = CareerProfile(
    name="Unknown Candidate",
    years_experience=0,
    strengths=[],
    gaps=[],
    target_primary=["unknown"],
    target_secondary=[],
    locations=["EU"],
    needs_sponsorship=True,
)


def profile_strategy_node(state: CareerState) -> dict:
    _enter("profile_strategy")
    path = Path(state.get("profile_path") or "")
    try:
        if not path.is_absolute():
            from core.config import BASE_DIR
            cand = BASE_DIR / "data" / path.name
            path = cand if cand.exists() else path
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        profile = _map_profile(raw)
        track = Track.A if profile.target_primary and profile.target_primary[0] in (
            "ai_strategy", "product_management", "business_strategy", "digital_transformation"
        ) else Track.B
        return {
            "phase": Phase.PROFILE,
            "profile": profile,
            "target_categories": list(profile.target_primary),
            "track_strategy": TrackStrategy(primary_track=track, rationale="derived from target_primary"),
            "audit_log": ["profile_strategy"],
        }
    except Exception as exc:  # missing/malformed -> warn, error, fallback, continue
        logger.warning("profile load failed (%s); using fallback profile", exc)
        return {
            "phase": Phase.PROFILE,
            "profile": _FALLBACK_PROFILE,
            "target_categories": ["unknown"],
            "track_strategy": TrackStrategy(primary_track=Track.UNCLASSIFIED, rationale="fallback"),
            "errors": [AgentError(node="profile_strategy", message=str(exc), recoverable=True)],
            "audit_log": ["profile_strategy"],
        }


def _map_profile(raw: dict) -> CareerProfile:
    """Map candidate_profile.json -> CareerProfile.

    WHY THIS CHANGED: the canonical profile schema is NESTED (personal / skills /
    target_roles / target_geography / ...), but this mapper previously read only
    FLAT top-level keys. With a nested profile every lookup missed, so the model
    silently fell back to placeholders (name='Candidate',
    target_primary=['unknown'], locations=['EU']). It now reads the nested schema
    FIRST and falls back to the legacy flat keys, so both shapes work (backward
    compatible). No new business rules: values are taken verbatim from the data —
    role titles are NOT re-classified here, so the existing track gate in
    profile_strategy_node is unchanged.
    """
    def _as_list(v):
        if isinstance(v, list):
            return [str(x) for x in v]
        if isinstance(v, str):
            return [v]
        return []

    personal = raw.get("personal") if isinstance(raw.get("personal"), dict) else {}
    geography = raw.get("target_geography") if isinstance(raw.get("target_geography"), dict) else {}
    roles = raw.get("target_roles")
    skills = raw.get("skills")

    # name: nested personal.name (+ last_name) -> legacy flat name/full_name.
    nested_name = " ".join(str(personal[k]) for k in ("name", "last_name") if personal.get(k))
    name = nested_name or raw.get("name") or raw.get("full_name") or "Candidate"

    # strengths: flatten the nested skills dict's lists -> legacy flat skills list.
    if isinstance(skills, dict):
        strengths = [str(s) for group in skills.values()
                     if isinstance(group, list) for s in group]
    else:
        strengths = _as_list(raw.get("strengths") or skills)

    # target roles: flatten nested track lists (track_1* -> primary, track_2* ->
    # secondary) -> legacy flat target_primary / target_roles list.
    if isinstance(roles, dict):
        primary = [str(x) for k, v in roles.items()
                   if k.startswith("track_1") and isinstance(v, list) for x in v]
        secondary = [str(x) for k, v in roles.items()
                     if k.startswith("track_2") and isinstance(v, list) for x in v]
    else:
        primary = _as_list(raw.get("target_primary") or raw.get("target_roles"))
        secondary = _as_list(raw.get("target_secondary"))
    target_primary = primary or _as_list(raw.get("target_primary")) or ["unknown"]
    target_secondary = secondary or _as_list(raw.get("target_secondary"))

    # locations: nested target_geography.preferred_locations / based_in /
    # personal.location -> legacy flat locations / location.
    nested_locs = (_as_list(geography.get("preferred_locations"))
                   or _as_list(geography.get("based_in"))
                   or _as_list(personal.get("location")))
    locations = nested_locs or _as_list(raw.get("locations") or raw.get("location")) or ["EU"]

    return CareerProfile(
        name=str(name),
        years_experience=int(raw.get("years_experience") or raw.get("years") or 0),
        strengths=strengths,
        gaps=_as_list(raw.get("gaps")),
        target_primary=target_primary,
        target_secondary=target_secondary,
        locations=locations,
        needs_sponsorship=bool(raw.get("needs_sponsorship", True)),
    )


# --- 1b. PreFilter (cheap, deterministic, pre-persistence) ----------------- #
def prefilter_jobs_node(state: CareerState) -> dict:
    """Filter raw acquired jobs (state["jobs"]) before persistence/scoring.

    Coarse, deterministic gate only (expired / duplicate / geo / role keywords) —
    no scoring, taxonomy, language/visa gates, embeddings, or LLM calls.
    """
    _enter("prefilter_jobs")
    from core.prefilter import prefilter_jobs

    kept, stats = prefilter_jobs(state.get("jobs") or [], state.get("profile"))
    logger.info(
        "Prefilter: total=%d kept=%d duplicates=%d geo_rejected=%d role_rejected=%d expired=%d",
        stats["total"], stats["kept"], stats["duplicates"], stats["geo_rejected"],
        stats["role_rejected"], stats["expired"],
    )
    return {"phase": Phase.PREFILTER, "jobs": kept, "prefilter_stats": stats,
            "audit_log": ["prefilter_jobs"]}


# --- 2. Job Ingestion (wraps search_agent gates) -------------------------- #
def job_ingestion_node(state: CareerState) -> dict:
    _enter("job_ingestion")
    from agents.search_agent import _check_geography

    run_id = state.get("run_id")
    try:
        rows = get_provider(run_id).fetch()
        # Acquisition path: state["jobs"] holds the prefiltered lightweight refs;
        # ingest only those (by url). Legacy/read-only runs have no state["jobs"]
        # and ingest every provider row (unchanged).
        kept = state.get("jobs")
        if kept is not None:
            keep_urls = {r.get("url") for r in kept if r.get("url")}
            rows = [row for row in rows if row.get("url") in keep_urls]
        ingested: list[IngestedJob] = []
        rejected: list[dict] = []
        reasons: dict[str, int] = {}
        from core.prefilter import _seniority_rejected, intern_only, is_intern_role
        _intern_only = intern_only()
        for row in rows:
            loc = row.get("location") or ""
            geo_ok, geo_reason = _check_geography(loc)
            lang_fail = (row.get("language_gate") == "FAIL")
            visa_fail = (row.get("visa_gate") == "FAIL")
            elig_status = (row.get("eligibility_status") or "UNCHECKED")
            # Candidate targets internship/graduate roles — drop senior titles and,
            # when INTERN_ONLY is set, anything that isn't an internship/graduate role.
            senior = _seniority_rejected(row.get("title"))
            non_intern = _intern_only and not is_intern_role(row.get("title"))
            eligible = (geo_ok and not lang_fail and not visa_fail
                        and elig_status != "REJECTED" and not senior and not non_intern)
            ij = IngestedJob(
                job_id=int(row["id"]),
                title=row.get("title") or "",
                company=row.get("company") or "",
                location=loc or None,
                url=row.get("url") or f"seed://{row['id']}",
                geography_gate=GateResult(passed=geo_ok, reason=geo_reason),
                language_gate=GateResult(
                    passed=not lang_fail,
                    reason=row.get("language_rejection_reason") or ("fail" if lang_fail else "pass"),
                ),
                visa_gate=GateResult(
                    passed=not visa_fail,
                    reason=row.get("visa_rejection_reason") or ("fail" if visa_fail else "pass"),
                ),
                eligible=eligible,
            )
            if eligible:
                ingested.append(ij)
            else:
                reason = ("non_eu" if not geo_ok else "language" if lang_fail else
                          "visa" if visa_fail else "seniority" if senior else
                          "non_intern" if non_intern else "eligibility")
                reasons[reason] = reasons.get(reason, 0) + 1
                rejected.append({"job_id": ij.job_id, "reason": reason})
        return {
            "phase": Phase.INGESTION,
            "ingested_jobs": ingested,
            "rejected_jobs": rejected,
            "ingestion_stats": IngestionStats(
                fetched=len(rows), passed=len(ingested),
                rejected=len(rejected), rejected_by_reason=reasons,
            ),
            "audit_log": ["job_ingestion"],
        }
    except Exception as exc:
        logger.exception("job_ingestion failed")
        return {
            "phase": Phase.INGESTION,
            "ingested_jobs": [],
            "ingestion_stats": IngestionStats(),
            "errors": [AgentError(node="job_ingestion", message=str(exc))],
            "retry_count": {"job_ingestion": MAX_NODE_RETRIES + 1},
            "audit_log": ["job_ingestion"],
        }


# --- 3. Taxonomy (wraps role_loader + _assign_track) ---------------------- #
def taxonomy_node(state: CareerState) -> dict:
    _enter("taxonomy")
    from agents.search_agent import _assign_track
    from core.role_loader import classify_title

    try:
        classified: list[ClassifiedJob] = []
        counts: dict[str, int] = {}
        unknown = 0
        for ij in state.get("ingested_jobs") or []:
            cat = classify_title(ij.title)
            track = _assign_track(cat)
            classified.append(ClassifiedJob(job_id=ij.job_id, role_category=cat, track=Track(track)))
            counts[cat] = counts.get(cat, 0) + 1
            if cat == "unknown":
                unknown += 1
        return {
            "phase": Phase.TAXONOMY,
            "classified_jobs": classified,
            "taxonomy_stats": TaxonomyStats(by_category=counts, unknown_count=unknown),
            "audit_log": ["taxonomy"],
        }
    except Exception as exc:
        logger.exception("taxonomy failed")
        return {
            "phase": Phase.TAXONOMY,
            "classified_jobs": [],
            "taxonomy_stats": TaxonomyStats(),
            "errors": [AgentError(node="taxonomy", message=str(exc))],
            "retry_count": {"taxonomy": MAX_NODE_RETRIES + 1},
            "audit_log": ["taxonomy"],
        }


# --- 4. Scoring (wraps scorer.score_job; Claude optional/mockable) -------- #
def scoring_node(state: CareerState) -> dict:
    _enter("scoring")
    from core.scorer import score_job

    run_id = state.get("run_id")
    provider = get_provider(run_id)
    claude_hook = get_claude_hook(run_id)
    cls_by_id = {c.job_id: c for c in (state.get("classified_jobs") or [])}

    scored: list[ScoredJob] = []
    by_bucket: dict[str, int] = {}
    claude_calls = 0
    claude_errors = 0

    for ij in state.get("ingested_jobs") or []:
        if not ij.eligible:
            continue
        try:
            job = provider.get(ij.job_id) or {}
            job = dict(job)
            cls = cls_by_id.get(ij.job_id)
            job["role_category"] = cls.role_category if cls else "unknown"
            res = score_job(job)  # deterministic; semantic handled inside

            total = res.total_score
            claude_expl = None
            if claude_hook is not None:
                try:
                    adj = claude_hook(job, res)  # -> ClaudeScoreAdjustment
                    total = int(_clamp(total + adj.score_adjustment, 0, 100))
                    claude_expl = adj.explanation
                    claude_calls += 1
                except Exception:  # Claude failure must not break scoring
                    claude_errors += 1
                    logger.warning("claude hook failed for job %s; deterministic only", ij.job_id)

            comp = ScoreComponents(
                track_alignment=res.track_alignment_score,
                skill_match=res.skill_match_score,
                mba_relevance=res.mba_relevance_score,
                company_quality=res.company_quality_score,
                intl_friendliness=res.intl_friendliness_score,
                pivot_bonus=res.pivot_bonus_score,
            )
            sj = ScoredJob(
                job_id=ij.job_id,
                total_score=total,
                components=comp,
                priority_bucket=PriorityBucket(res.priority_bucket),
                semantic_similarity=_clamp(res.semantic_similarity or 0.0, 0.0, 1.0) or None,
                claude_explanation=claude_expl,
            )
            scored.append(sj)
            by_bucket[sj.priority_bucket.value] = by_bucket.get(sj.priority_bucket.value, 0) + 1
        except Exception as exc:
            logger.warning("scoring job %s failed: %s", ij.job_id, exc)
            # per-job isolation: skip the job, keep the batch alive
            continue

    # v6 (opt-in): persist scores to the DB. Idempotent upsert by job_id, so a
    # checkpoint replay / retry overwrites rather than duplicates. Off by default.
    from graph.persistence import persist_scores, score_persistence_enabled
    if score_persistence_enabled() and scored:
        role_by_id = {c.job_id: c.role_category for c in (state.get("classified_jobs") or [])}
        n = persist_scores(scored, role_by_id)
        logger.info("scoring: persisted %d score(s) to DB", n)

    return {
        "phase": Phase.SCORING,
        "scored_jobs": scored,
        "scoring_stats": ScoringStats(
            scored=len(scored), claude_calls=claude_calls,
            claude_errors=claude_errors, by_bucket=by_bucket,
        ),
        "audit_log": ["scoring"],
    }


# --- 6. Opportunity Intelligence (thin wrapper over core module) ---------- #
# Business logic lives in core/opportunity_intelligence.py (spec v1.0). This
# wrapper only: resolves inputs, calls assess(), maps to schema, handles errors.
def opportunity_intel_node(state: CareerState) -> dict:
    _enter("opportunity_intel")
    from core.opportunity_intelligence import assess

    run_id = state.get("run_id")
    provider = get_provider(run_id)
    intel_hook = get_intel_hook(run_id)
    profile = state.get("profile")
    ij_by_id = {ij.job_id: ij for ij in (state.get("ingested_jobs") or [])}
    cls_by_id = {c.job_id: c for c in (state.get("classified_jobs") or [])}

    out = []
    for sj in state.get("scored_jobs") or []:
        try:
            ij = ij_by_id.get(sj.job_id)
            cls = cls_by_id.get(sj.job_id)
            if ij is None or cls is None:
                continue
            row = provider.get(sj.job_id) or {}
            eligibility = {"visa_accessibility": row.get("visa_accessibility", 0)}
            out.append(assess(
                profile=profile, job=ij, classified=cls, score=sj,
                claude_hook=intel_hook, eligibility=eligibility,
            ))
        except Exception as exc:
            logger.warning("opportunity_intel: job %s failed: %s", sj.job_id, exc)
            continue
    return {"phase": Phase.INTELLIGENCE, "intelligence": out, "audit_log": ["opportunity_intel"]}


# --- 7. Prioritization (thin wrapper over core module) -------------------- #
# Business logic lives in core/prioritization.py (spec v1.0 §2).
def prioritization_node(state: CareerState) -> dict:
    _enter("prioritization")
    from core.prioritization import build_plan, rank

    try:
        intel = state.get("intelligence") or []
        ij_by_id = {ij.job_id: ij for ij in (state.get("ingested_jobs") or [])}
        total_scores = {sj.job_id: sj.total_score for sj in (state.get("scored_jobs") or [])}
        prioritized = rank(intel, total_scores)
        plan = build_plan(prioritized, intel, ij_by_id)
        return {
            "phase": Phase.PRIORITIZATION,
            "prioritized": prioritized,
            "this_week": plan.this_week,
            "next_week": plan.next_week,
            "audit_log": ["prioritization"],
        }
    except Exception as exc:
        logger.exception("prioritization failed")
        return {
            "phase": Phase.PRIORITIZATION, "prioritized": [], "this_week": [], "next_week": [],
            "errors": [AgentError(node="prioritization", message=str(exc))],
            "retry_count": {"prioritization": MAX_NODE_RETRIES + 1},
            "audit_log": ["prioritization"],
        }


# --- 8. Recommendations (thin wrapper over core module) ------------------- #
# Business logic lives in core/recommendations.py. Wrapper only maps state.
def recommendations_node(state: CareerState) -> dict:
    _enter("recommendations")
    from core.recommendations import generate_recommendations

    try:
        intel = state.get("intelligence") or []
        prioritized = state.get("prioritized") or []
        jobs = {ij.job_id: ij for ij in (state.get("ingested_jobs") or [])}
        classified = {c.job_id: c for c in (state.get("classified_jobs") or [])}
        recs = generate_recommendations(intel, prioritized, jobs, classified)
        return {
            "phase": Phase.RECOMMENDATIONS,
            "recommendations": recs,
            "audit_log": ["recommendations"],
        }
    except Exception as exc:
        logger.exception("recommendations failed")
        return {
            "phase": Phase.RECOMMENDATIONS, "recommendations": [],
            "errors": [AgentError(node="recommendations", message=str(exc))],
            "retry_count": {"recommendations": MAX_NODE_RETRIES + 1},
            "audit_log": ["recommendations"],
        }


# --- 9. Output Experience (thin wrapper over core module) ----------------- #
# Presentation layer only. Business logic lives in core/output_experience.py.
def output_experience_node(state: CareerState) -> dict:
    _enter("output_experience")
    from core.output_experience import (
        build_api_response,
        build_dashboard,
        build_export_payload,
        build_summary,
    )

    try:
        return {
            "phase": Phase.OUTPUT,
            "summary": build_summary(state),
            "dashboard": build_dashboard(state),
            "exports": build_export_payload(state),
            "api_response": build_api_response(state),
            "audit_log": ["output_experience"],
        }
    except Exception as exc:
        logger.exception("output_experience failed")
        return {
            "phase": Phase.OUTPUT,
            "errors": [AgentError(node="output_experience", message=str(exc))],
            "retry_count": {"output_experience": MAX_NODE_RETRIES + 1},
            "audit_log": ["output_experience"],
        }
