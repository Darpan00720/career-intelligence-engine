"""Concrete v4 agents — thin BaseAgent wrappers over existing, tested logic.

Each agent is independently testable: inject fakes via `deps`, or let it bind
the real implementation lazily. The legacy module-level run() functions remain
untouched for backward compatibility.

    SearchAgent  ScoringAgent  ResearchAgent  DocumentAgent
    RecommendationAgent  AnalyticsAgent  TrackerAgent  NotificationAgent
"""
from __future__ import annotations

from agents.base_agent import BaseAgent
from core import database


def _count(table: str) -> int:
    with database.get_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class SearchAgent(BaseAgent):
    name = "search"

    def run(self) -> dict:
        run_search = self.dep(
            "run_search",
            lambda: __import__("agents.search_agent", fromlist=["run"]).run(),
        )
        run_search()
        return {"jobs_found": _count("jobs")}


class ScoringAgent(BaseAgent):
    name = "scoring"

    def run(self) -> dict:
        run_scoring = self.dep(
            "run_scoring",
            lambda: __import__("agents.scoring_agent", fromlist=["run"]).run(),
        )
        run_scoring()
        return {"jobs_scored": _count("scores")}


class ResearchAgent(BaseAgent):
    name = "research"

    def run(self) -> dict:
        min_score = self.context.get("research_min_score", 80)
        max_workers = self.context.get("max_workers", 1)
        run_research = self.dep(
            "run_research",
            lambda **kw: __import__("agents.research_agent", fromlist=["run"]).run(**kw),
        )
        return run_research(min_score=min_score, max_workers=max_workers)


class DocumentAgent(BaseAgent):
    name = "documents"

    def run(self) -> dict:
        min_score = self.context.get("research_min_score", 80)
        max_workers = self.context.get("max_workers", 1)
        run_documents = self.dep(
            "run_documents",
            lambda **kw: __import__("agents.document_agent", fromlist=["run"]).run(**kw),
        )
        return run_documents(min_score=min_score, max_workers=max_workers)


class RecommendationAgent(BaseAgent):
    name = "recommendation"

    def run(self) -> dict:
        from core.recommendation_engine import recommend_all
        from core.ranking import rank_jobs

        try:
            from core.profile_loader import load as _load_profile
            profile = _load_profile()
        except Exception:
            profile = None

        rows = recommend_all(
            rank_jobs(database.get_all_scored_jobs_ranked()),
            profile=profile,
            research_job_ids=database.get_researched_job_ids(),
        )
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["recommendation"]] = counts.get(r["recommendation"], 0) + 1
        return {"recommendations": counts, "total": len(rows)}


class AnalyticsAgent(BaseAgent):
    name = "analytics"

    def run(self) -> dict:
        from core import analytics
        return analytics.summary()


class TrackerAgent(BaseAgent):
    name = "tracker"

    def run(self) -> dict:
        from core.tracker import ensure_rows_for_scored
        created = ensure_rows_for_scored()
        return {"applications_updated": created}


class NotificationAgent(BaseAgent):
    name = "notification"

    def run(self) -> dict:
        from core.notifications import get_notifier

        notifier = self.deps.get("notifier") or get_notifier()
        events = self.context.get("events") or []
        sent = 0
        for ev in events:
            notifier.dispatch(ev.get("event", "event"), ev.get("message", ""),
                              level=ev.get("level", "INFO"))
            sent += 1
        return {"notifications_sent": sent}


# Convenience registry for the orchestrator / scheduler.
AGENT_CLASSES = {
    cls.name: cls
    for cls in (SearchAgent, ScoringAgent, ResearchAgent, DocumentAgent,
                RecommendationAgent, AnalyticsAgent, TrackerAgent, NotificationAgent)
}


def build_agents(context: dict | None = None) -> dict[str, BaseAgent]:
    """Instantiate one of every agent sharing a context (dependency injection point)."""
    return {name: cls(context=context) for name, cls in AGENT_CLASSES.items()}
