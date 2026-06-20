"""Tests for the wired-in, opt-in research stage (graph/research_writable.py).

Covers planner/routing/build wiring (flag-gated) and the research-class
idempotency mechanism: TTL staleness. A re-run skips fresh rows — no second
(paid) Claude call and no duplicate company_research row.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core import config, database
from graph import planner, providers, research_writable
from schemas.control import Phase
from schemas.scoring import PriorityBucket, ScoreComponents, ScoredJob


def _scored(job_id: int, total: int) -> ScoredJob:
    return ScoredJob(
        job_id=job_id, total_score=total,
        components=ScoreComponents(track_alignment=20, skill_match=18, mba_relevance=10,
                                   company_quality=12, intl_friendliness=6, pivot_bonus=3),
        priority_bucket=PriorityBucket.HIGH, semantic_similarity=0.5,
        claude_explanation="x")


# ── Planner / routing / build wiring ────────────────────────────────────────────

class TestResearchWiring(unittest.TestCase):
    def test_disabled_plan_has_no_research(self):
        with patch.dict(os.environ, {"ENABLE_RESEARCH": "0"}):
            ids = planner.build_execution_plan("").task_ids()
        self.assertNotIn("research", ids)
        self.assertEqual(len(ids), 8)

    def test_enabled_plan_inserts_research_after_scoring(self):
        with patch.dict(os.environ, {"ENABLE_RESEARCH": "1"}):
            plan = planner.build_execution_plan("")
            ids = plan.task_ids()
            self.assertIn("research", ids)
            self.assertLess(ids.index("scoring"), ids.index("research"))
            self.assertLess(ids.index("research"), ids.index("opportunity_intel"))
            intel = next(t for t in plan.tasks if t.id == "opportunity_intel")
            self.assertEqual(intel.depends_on, ["research"])

    def test_routing_gated(self):
        from graph import routing
        with patch.dict(os.environ, {"ENABLE_RESEARCH": "0"}):
            self.assertEqual(routing.route_from_supervisor({"phase": Phase.SCORING}),
                             "opportunity_intel")
        with patch.dict(os.environ, {"ENABLE_RESEARCH": "1"}):
            self.assertEqual(routing.route_from_supervisor({"phase": Phase.SCORING}),
                             "research")
        self.assertEqual(routing.route_from_supervisor({"phase": Phase.RESEARCH}),
                         "opportunity_intel")

    def test_registered_as_worker(self):
        from graph.build import _WORKERS
        self.assertIn("research", _WORKERS)


# ── TTL idempotency ─────────────────────────────────────────────────────────────

class TestResearchTTLIdempotency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._db = patch.object(config, "DB_PATH", os.path.join(self.tmp, "research.db"))
        self._db.start()
        database.initialize()
        # Seed jobs (company_research.job_id has a FK to jobs(id)).
        with database.get_connection() as conn:
            for jid in (1, 2):
                conn.execute("INSERT INTO jobs (id, title, company) VALUES (?, ?, ?)",
                             (jid, f"Role {jid}", f"Company {jid}"))
        providers.set_provider("rt", providers.SeedJobProvider([
            {"id": 1, "company": "Company 1", "title": "Role 1",
             "location": "Milan", "description": "d1"},
            {"id": 2, "company": "Company 2", "title": "Role 2",
             "location": "Berlin", "description": "d2"},
        ]))
        self._env = patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x", "RESEARCH_MIN_SCORE": "70"})
        self._env.start()

    def tearDown(self):
        providers.clear_provider("rt")
        self._env.stop()
        self._db.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _count(self) -> int:
        with database.get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM company_research").fetchone()[0]

    def test_rerun_skips_fresh_no_duplicate_calls(self):
        state = {"run_id": "rt", "scored_jobs": [_scored(1, 90), _scored(2, 80)]}
        with patch("agents.research_agent._call_claude",
                   return_value={"research_quality": 7, "mission": "m"}) as mock_claude:
            out1 = research_writable.research_node(state)
            self.assertEqual(out1["research_stats"]["researched"], 2)
            self.assertEqual(self._count(), 2)
            self.assertEqual(mock_claude.call_count, 2)

            # Re-run: TTL guard -> fresh rows skipped, no new Claude calls/rows.
            out2 = research_writable.research_node(state)
            self.assertEqual(out2["research_stats"]["skipped_fresh"], 2)
            self.assertEqual(out2["research_stats"]["researched"], 0)
            self.assertEqual(self._count(), 2)              # no duplicate rows
            self.assertEqual(mock_claude.call_count, 2)     # no second paid call

    def test_below_threshold_not_researched(self):
        state = {"run_id": "rt", "scored_jobs": [_scored(1, 50)]}  # < 70
        with patch("agents.research_agent._call_claude", return_value={}) as mock_claude:
            out = research_writable.research_node(state)
        self.assertEqual(out["research_stats"]["eligible"], 0)
        self.assertEqual(mock_claude.call_count, 0)
        self.assertEqual(self._count(), 0)


if __name__ == "__main__":
    unittest.main()
