"""CareerState: the LangGraph state object for the 7-agent pipeline.

Defined as a TypedDict with Annotated reducers on accumulating fields.
Importing this module does NOT import langgraph — the Annotated metadata is
only read by langgraph at graph-build time. A mirror Pydantic model
(CareerStateModel) and helpers provide validation and a clean initial state.
"""
from __future__ import annotations

from typing import Annotated, Optional, TypedDict

from pydantic import ConfigDict, model_validator

from graph.reducers import extend, merge_by_job_id, merge_dict
from schemas.base import CareerBaseModel
from schemas.control import AgentError, Phase, RunStatus
from schemas.execution import ExecutionPlan
from schemas.intelligence import OpportunityIntelligence
from schemas.jobs import ClassifiedJob, IngestedJob, IngestionStats, TaxonomyStats
from schemas.planning import ApplyItem, CompositeScore
from schemas.profile import CareerProfile, TrackStrategy
from schemas.output import ApiResponse, CareerSummary, Dashboard, ExportPayload
from schemas.recommendations import Recommendation
from schemas.scoring import ScoredJob, ScoringStats


class CareerState(TypedDict, total=False):
    """Graph state. total=False: every key is optional and filled as the run
    progresses. Accumulating lists/dicts carry reducers; scalars overwrite."""

    # --- run / control plane ---
    run_id: str
    phase: Phase
    status: RunStatus
    next_node: str

    # --- v6 planning plane (Phase 1: data only; phase-based routing still active) ---
    # Populated by the future Planner Agent. When `execution_plan` is absent the
    # graph behaves exactly as before (legacy phase routing). `completed_tasks`
    # accumulates (reducer); `pending_tasks`/`current_task` overwrite.
    user_query: str
    execution_plan: ExecutionPlan
    pending_tasks: list[str]
    completed_tasks: Annotated[list[str], extend]
    current_task: str

    # --- profile phase ---
    profile_path: str
    profile: CareerProfile
    target_categories: list[str]
    track_strategy: TrackStrategy
    resume_embedding_ref: Optional[str]

    # --- acquisition / prefilter phase (opt-in) ---
    # Raw fetched jobs flow acquire -> prefilter -> job_ingestion as plain dicts
    # (last-write-wins; no reducer). prefilter_stats is observability only.
    jobs: list[dict]
    prefilter_stats: dict

    # --- ingestion phase ---
    sources: list[str]
    ingested_jobs: Annotated[list[IngestedJob], merge_by_job_id]
    rejected_jobs: Annotated[list[dict], extend]
    ingestion_stats: IngestionStats

    # --- taxonomy phase ---
    classified_jobs: Annotated[list[ClassifiedJob], merge_by_job_id]
    taxonomy_stats: TaxonomyStats

    # --- scoring phase ---
    scored_jobs: Annotated[list[ScoredJob], merge_by_job_id]
    scoring_stats: ScoringStats

    # --- research phase (opt-in; ENABLE_RESEARCH) ---
    research_stats: dict

    # --- documents phase (opt-in; ENABLE_DOCUMENTS) ---
    documents_stats: dict

    # --- terminal runner (opt-in; documents/export/tracker) ---
    terminal_stats: dict

    # --- intelligence phase ---
    intelligence: Annotated[list[OpportunityIntelligence], merge_by_job_id]

    # --- prioritization phase ---
    prioritized: Annotated[list[CompositeScore], merge_by_job_id]
    this_week: list[ApplyItem]
    next_week: list[ApplyItem]

    # --- recommendations phase ---
    recommendations: Annotated[list[Recommendation], merge_by_job_id]

    # --- output-experience phase (presentation only) ---
    summary: CareerSummary
    dashboard: Dashboard
    exports: ExportPayload
    api_response: ApiResponse
    final_report: dict

    # --- human-in-the-loop + diagnostics ---
    human_review_pending: bool
    human_decisions: Annotated[dict, merge_dict]
    errors: Annotated[list[AgentError], extend]
    retry_count: Annotated[dict, merge_dict]
    audit_log: Annotated[list[str], extend]


class CareerStateModel(CareerBaseModel):
    """Pydantic mirror of CareerState used for validation in tests and at the
    API boundary. All fields optional; extra keys forbidden to catch typos."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    run_id: Optional[str] = None
    phase: Optional[Phase] = None
    status: Optional[RunStatus] = None
    next_node: Optional[str] = None

    # v6 planning plane (Phase 1: validated shape only)
    user_query: Optional[str] = None
    execution_plan: Optional[ExecutionPlan] = None
    pending_tasks: Optional[list[str]] = None
    completed_tasks: Optional[list[str]] = None
    current_task: Optional[str] = None

    profile_path: Optional[str] = None
    profile: Optional[CareerProfile] = None
    target_categories: Optional[list[str]] = None
    track_strategy: Optional[TrackStrategy] = None
    resume_embedding_ref: Optional[str] = None

    jobs: Optional[list[dict]] = None
    prefilter_stats: Optional[dict] = None
    sources: Optional[list[str]] = None
    ingested_jobs: Optional[list[IngestedJob]] = None
    rejected_jobs: Optional[list[dict]] = None
    ingestion_stats: Optional[IngestionStats] = None

    classified_jobs: Optional[list[ClassifiedJob]] = None
    taxonomy_stats: Optional[TaxonomyStats] = None

    scored_jobs: Optional[list[ScoredJob]] = None
    scoring_stats: Optional[ScoringStats] = None
    research_stats: Optional[dict] = None
    documents_stats: Optional[dict] = None
    terminal_stats: Optional[dict] = None

    intelligence: Optional[list[OpportunityIntelligence]] = None

    prioritized: Optional[list[CompositeScore]] = None
    this_week: Optional[list[ApplyItem]] = None
    next_week: Optional[list[ApplyItem]] = None
    recommendations: Optional[list[Recommendation]] = None
    summary: Optional[CareerSummary] = None
    dashboard: Optional[Dashboard] = None
    exports: Optional[ExportPayload] = None
    api_response: Optional[ApiResponse] = None
    final_report: Optional[dict] = None

    @model_validator(mode="after")
    def _check_cross_refs(self):
        """Cross-reference invariants, each guarded by presence so partial node
        updates still validate. Full results enforce all of them."""
        # Phase 6: recommendations reference prioritized job_ids.
        if self.recommendations and self.prioritized is not None:
            valid = {cs.job_id for cs in self.prioritized}
            for rec in self.recommendations:
                if rec.job_id not in valid:
                    raise ValueError(
                        f"recommendation job_id {rec.job_id} not in prioritized entries")
        # Phase 7: dashboard tier counts reconcile with prioritized.
        if self.dashboard is not None and self.prioritized is not None:
            d = self.dashboard.application_summary.data
            total = int(d.get("tier_1", 0)) + int(d.get("tier_2", 0)) + int(d.get("tier_3", 0))
            if total != len(self.prioritized):
                raise ValueError("dashboard tier counts do not reconcile with prioritized")
        # Phase 7: top opportunities reference valid recommendations.
        if self.dashboard is not None and self.recommendations is not None:
            rec_ids = {r.job_id for r in self.recommendations}
            for top in self.dashboard.top_opportunities:
                if top.job_id not in rec_ids:
                    raise ValueError(
                        f"top opportunity job_id {top.job_id} not in recommendations")
        return self

    human_review_pending: Optional[bool] = None
    human_decisions: Optional[dict] = None
    errors: Optional[list[AgentError]] = None
    retry_count: Optional[dict] = None
    audit_log: Optional[list[str]] = None


def new_career_state(run_id: str, profile_path: str) -> CareerState:
    """Return a well-formed initial state for a fresh run."""
    return CareerState(
        run_id=run_id,
        phase=Phase.INIT,
        status=RunStatus.RUNNING,
        profile_path=profile_path,
        human_review_pending=False,
        human_decisions={},
        errors=[],
        retry_count={},
        audit_log=[],
        # v6 planning plane: empty until a Planner Agent populates a plan.
        # Their presence is inert under legacy phase-based routing.
        pending_tasks=[],
        completed_tasks=[],
    )


def validate_state(state: dict) -> CareerStateModel:
    """Validate a raw state dict against the mirror model. Raises on bad shape."""
    return CareerStateModel.model_validate(state)
