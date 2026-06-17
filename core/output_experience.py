"""Output Experience — Phase 7 presentation layer.

Builds deterministic, serialization-safe views (summary, dashboard, export,
API response) over already-computed state. NO scoring, NO recommendation logic,
NO LLM. Every value is read from existing ratified state.
"""
from __future__ import annotations

from schemas.control import Tier
from schemas.output import (
    OUTPUT_VERSION,
    ApiResponse,
    CareerSummary,
    Dashboard,
    DashboardSection,
    ExportPayload,
    TopOpportunity,
)
from schemas.recommendations import RecommendationType


def _tier_counts(prioritized) -> dict[str, int]:
    counts = {"tier_1": 0, "tier_2": 0, "tier_3": 0}
    for cs in prioritized:
        if cs.tier == Tier.TIER_1:
            counts["tier_1"] += 1
        elif cs.tier == Tier.TIER_2:
            counts["tier_2"] += 1
        else:
            counts["tier_3"] += 1
    return counts


def _rec_type_counts(recommendations) -> dict[str, int]:
    counts = {t.value: 0 for t in RecommendationType}
    for r in recommendations:
        counts[r.recommendation_type.value] += 1
    return counts


def build_summary(state) -> CareerSummary:
    profile = state.get("profile")
    prioritized = state.get("prioritized") or []
    recommendations = state.get("recommendations") or []
    this_week = state.get("this_week") or []
    next_week = state.get("next_week") or []

    if profile is not None:
        track = state.get("track_strategy")
        track_label = f" (Track {track.primary_track.value})" if track else ""
        career_direction = (
            f"Primary pivot toward {', '.join(profile.target_primary)}{track_label}; "
            f"targeting {', '.join(profile.locations)}."
        )
    else:
        career_direction = "Candidate profile unavailable."

    tc = _tier_counts(prioritized)
    application_status = (
        f"{len(recommendations)} recommendations: {tc['tier_1']} Tier-1, "
        f"{tc['tier_2']} Tier-2, {tc['tier_3']} Tier-3. "
        f"{len(this_week)} role(s) planned this week, {len(next_week)} next week."
    )

    rc = _rec_type_counts(recommendations)
    recommended_focus = (
        f"Focus: {rc['APPLY_IMMEDIATELY']} apply-immediately, "
        f"{rc['APPLY_THIS_WEEK']} apply-this-week, {rc['STRETCH_ROLE']} stretch, "
        f"{rc['MONITOR']} monitor."
    )

    immediate_actions = [f"Apply to {it.company} — {it.title}" for it in this_week]
    if not immediate_actions:
        immediate_actions = ["No Tier 1/2 roles this week; expand sourcing or revisit Tier 3."]

    return CareerSummary(
        career_direction=career_direction,
        application_status=application_status,
        recommended_focus=recommended_focus,
        immediate_actions=immediate_actions,
    )


def build_dashboard(state) -> Dashboard:
    profile = state.get("profile")
    prioritized = state.get("prioritized") or []
    recommendations = state.get("recommendations") or []
    intelligence = state.get("intelligence") or []
    ingested = state.get("ingested_jobs") or []
    this_week = state.get("this_week") or []
    next_week = state.get("next_week") or []
    track = state.get("track_strategy")
    istats = state.get("ingestion_stats")

    profile_summary = DashboardSection(title="Profile", data={
        "candidate": profile.name if profile else "Unknown",
        "target_tracks": profile.target_primary if profile else [],
        "target_locations": profile.locations if profile else [],
        "primary_track": track.primary_track.value if track else "unclassified",
    })

    pipeline_summary = DashboardSection(title="Pipeline", data={
        "jobs_ingested": istats.fetched if istats else len(ingested),
        "eligible_jobs": istats.passed if istats else len(ingested),
        "classified_jobs": len(state.get("classified_jobs") or []),
        "scored_jobs": len(state.get("scored_jobs") or []),
        "recommendations_generated": len(recommendations),
    })

    tc = _tier_counts(prioritized)
    application_summary = DashboardSection(title="Applications", data={
        "tier_1": tc["tier_1"], "tier_2": tc["tier_2"], "tier_3": tc["tier_3"],
        "by_type": _rec_type_counts(recommendations),
    })

    weekly_plan = DashboardSection(title="Weekly Plan", data={
        "this_week": [f"{it.company} — {it.title}" for it in this_week],
        "next_week": [f"{it.company} — {it.title}" for it in next_week],
    })

    rec_by_id = {r.job_id: r for r in recommendations}
    oi_by_id = {o.job_id: o for o in intelligence}
    ij_by_id = {j.job_id: j for j in ingested}
    top: list[TopOpportunity] = []
    for cs in sorted(prioritized, key=lambda c: c.rank):
        rec = rec_by_id.get(cs.job_id)
        oi = oi_by_id.get(cs.job_id)
        if rec is None or oi is None:
            continue
        ij = ij_by_id.get(cs.job_id)
        top.append(TopOpportunity(
            rank=cs.rank, job_id=cs.job_id,
            company=ij.company if ij else "Unknown",
            title=ij.title if ij else "Unknown",
            recommendation_type=rec.recommendation_type,
            interview_prob=oi.interview_prob, roi=oi.roi,
            sponsorship_prob=oi.sponsorship_prob,
            preparation_actions=[a.action for a in rec.preparation_actions],
        ))

    return Dashboard(
        profile_summary=profile_summary,
        pipeline_summary=pipeline_summary,
        application_summary=application_summary,
        weekly_plan=weekly_plan,
        top_opportunities=top,
    )


def build_export_payload(state) -> ExportPayload:
    prioritized = state.get("prioritized") or []
    recommendations = state.get("recommendations") or []
    return ExportPayload(
        profile=state.get("profile"),
        prioritized=prioritized,
        recommendations=recommendations,
        this_week=state.get("this_week") or [],
        next_week=state.get("next_week") or [],
        summary=build_summary(state),
        dashboard_metadata={
            "version": OUTPUT_VERSION,
            "tier_counts": _tier_counts(prioritized),
            "recommendation_counts": _rec_type_counts(recommendations),
            "this_week": len(state.get("this_week") or []),
            "next_week": len(state.get("next_week") or []),
        },
    )


def build_api_response(state, generated_at: str | None = None) -> ApiResponse:
    """Deterministic API response. generated_at defaults to "" so the core stays
    reproducible; a real timestamp is injected at the transport edge."""
    return ApiResponse(
        success=True,
        version=OUTPUT_VERSION,
        generated_at=generated_at or "",
        summary=build_summary(state),
        dashboard=build_dashboard(state),
        export_payload=build_export_payload(state),
    )
