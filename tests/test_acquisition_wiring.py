"""Tests for the wired-in, opt-in acquisition + score-persistence stages.

Flags default OFF (covered by the rest of the suite staying green). Here we flip
ENABLE_ACQUISITION / PERSIST_SCORES on and assert the graph topology, planner,
and scoring node behave as designed.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core import config, database
from graph import planner, routing
from graph.persistence import persist_scored_job, persist_scores
from schemas.control import Phase
from schemas.scoring import PriorityBucket, ScoreComponents, ScoredJob


def _scored_job(job_id: int, total: int) -> ScoredJob:
    return ScoredJob(
        job_id=job_id,
        total_score=total,
        components=ScoreComponents(
            track_alignment=20, skill_match=18, mba_relevance=10,
            company_quality=12, intl_friendliness=6, pivot_bonus=3),
        priority_bucket=PriorityBucket.HIGH,
        semantic_similarity=0.5,
        claude_explanation="graph score",
    )


class TestPlannerAcquisition(unittest.TestCase):
    def test_disabled_plan_is_eight_nodes(self):
        with patch.dict(os.environ, {"ENABLE_ACQUISITION": "0"}):
            plan = planner.build_execution_plan("")  # full pipeline
        ids = plan.task_ids()
        self.assertNotIn("acquire_jobs", ids)
        self.assertEqual(len(ids), 8)

    def test_enabled_plan_includes_acquisition_before_ingestion(self):
        with patch.dict(os.environ, {"ENABLE_ACQUISITION": "1"}):
            plan = planner.build_execution_plan("")
            ids = plan.task_ids()
            self.assertIn("acquire_jobs", ids)
            self.assertLess(ids.index("acquire_jobs"), ids.index("job_ingestion"))
            # ingestion now depends on acquisition
            ing = next(t for t in plan.tasks if t.id == "job_ingestion")
            self.assertEqual(ing.depends_on, ["acquire_jobs"])

    def test_enabled_ingest_only_pulls_in_acquisition(self):
        with patch.dict(os.environ, {"ENABLE_ACQUISITION": "1"}):
            agents = planner.select_agents("job_ingestion")
        self.assertEqual(agents, ["profile_strategy", "acquire_jobs", "job_ingestion"])


class TestPhaseRouting(unittest.TestCase):
    def test_disabled_profile_routes_to_ingestion(self):
        with patch.dict(os.environ, {"ENABLE_ACQUISITION": "0"}):
            nxt = routing.route_from_supervisor({"phase": Phase.PROFILE})
        self.assertEqual(nxt, "job_ingestion")

    def test_enabled_profile_routes_to_acquisition(self):
        with patch.dict(os.environ, {"ENABLE_ACQUISITION": "1"}):
            nxt = routing.route_from_supervisor({"phase": Phase.PROFILE})
        self.assertEqual(nxt, "acquire_jobs")

    def test_acquisition_phase_routes_to_ingestion(self):
        nxt = routing.route_from_supervisor({"phase": Phase.ACQUISITION})
        self.assertEqual(nxt, "job_ingestion")


class TestBuildRegistersNode(unittest.TestCase):
    def test_acquire_jobs_registered_as_worker(self):
        from graph.build import _WORKERS
        self.assertIn("acquire_jobs", _WORKERS)


class TestScorePersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._p = patch.object(config, "DB_PATH", os.path.join(self.tmp, "scores.db"))
        self._p.start()
        database.initialize()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_jobs(self, *job_ids: int) -> None:
        # scores.job_id has a FK to jobs(id); the rows exist by the time scoring
        # runs (acquisition wrote them). Seed them for the unit test.
        with database.get_connection() as conn:
            for jid in job_ids:
                conn.execute(
                    "INSERT INTO jobs (id, title, company) VALUES (?, ?, ?)",
                    (jid, f"Role {jid}", f"Company {jid}"))

    def _count_scores(self) -> int:
        with database.get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]

    def _score_of(self, job_id: int) -> int:
        with database.get_connection() as conn:
            return conn.execute(
                "SELECT total_score FROM scores WHERE job_id = ?", (job_id,)
            ).fetchone()[0]

    def test_persists_batch(self):
        self._seed_jobs(1, 2)
        n = persist_scores([_scored_job(1, 80), _scored_job(2, 70)], {1: "pm", 2: "hr"})
        self.assertEqual(n, 2)
        self.assertEqual(self._count_scores(), 2)

    def test_upsert_is_idempotent(self):
        self._seed_jobs(1)
        persist_scored_job(_scored_job(1, 80), "pm")
        persist_scored_job(_scored_job(1, 95), "pm")  # re-persist same job_id
        self.assertEqual(self._count_scores(), 1)      # no duplicate row
        self.assertEqual(self._score_of(1), 95)        # value overwritten


if __name__ == "__main__":
    unittest.main()
