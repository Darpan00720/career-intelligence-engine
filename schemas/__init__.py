"""Career Agent structured-output schemas (Pydantic v2).

Import surface for all agents and the graph state. Keeping a flat re-export
here means nodes import from one place: `from schemas import ScoredJob`.
"""
from schemas.base import CareerBaseModel
from schemas.control import (
    AgentError,
    Horizon,
    Importance,
    Phase,
    RoutingDecision,
    RunMeta,
    RunStatus,
    Tier,
    Track,
)
from schemas.intelligence import (
    GapCategory,
    IntelLLMAssessment,
    OpportunityIntelligence,
    SkillGap,
)
from schemas.jobs import (
    ClassifiedJob,
    GateResult,
    IngestedJob,
    IngestionStats,
    RawJob,
    TaxonomyStats,
)
from schemas.planning import (
    COMPOSITE_WEIGHTS,
    MAX_THIS_WEEK,
    ApplyItem,
    CompositeScore,
    WeeklyPlan,
)
from schemas.profile import CareerProfile, TrackStrategy
from schemas.output import (
    OUTPUT_VERSION,
    ApiResponse,
    CareerSummary,
    Dashboard,
    DashboardSection,
    ExportPayload,
    TopOpportunity,
)
from schemas.recommendations import (
    PreparationAction,
    Recommendation,
    RecommendationType,
)
from schemas.scoring import (
    ClaudeScoreAdjustment,
    PriorityBucket,
    ScoreComponents,
    ScoredJob,
    ScoringStats,
)

__all__ = [
    "CareerBaseModel",
    "AgentError", "RoutingDecision", "RunMeta", "Phase", "RunStatus",
    "Track", "Tier", "Importance", "Horizon",
    "CareerProfile", "TrackStrategy",
    "RawJob", "GateResult", "IngestedJob", "ClassifiedJob",
    "IngestionStats", "TaxonomyStats",
    "ScoreComponents", "ClaudeScoreAdjustment", "ScoredJob",
    "ScoringStats", "PriorityBucket",
    "GapCategory", "SkillGap", "OpportunityIntelligence", "IntelLLMAssessment",
    "CompositeScore", "ApplyItem", "WeeklyPlan",
    "COMPOSITE_WEIGHTS", "MAX_THIS_WEEK",
    "Recommendation", "RecommendationType", "PreparationAction",
    "CareerSummary", "TopOpportunity", "DashboardSection", "Dashboard",
    "ExportPayload", "ApiResponse", "OUTPUT_VERSION",
]
