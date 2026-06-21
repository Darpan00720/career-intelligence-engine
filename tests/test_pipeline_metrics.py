"""Tests for core.pipeline_metrics + the observability API/dashboard routes."""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core import config, database, pipeline_metrics


def _seed(scores=(85, 72, 71, 68, 40, 10)):
    for i, sc in enumerate(scores, start=1):
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO jobs (id, title, company, job_board, role_category, track) "
                "VALUES (?,?,?,?,?,?)",
                (i, f"Role {i}", f"Co{i}", "greenhouse" if i % 2 else "lever",
                 "product_management", "A" if i % 2 else "B"))
        database.insert_score(
            job_id=i, role_category="product_management", total_score=sc, role_fit=0,
            skills_match=0, location_fit=0, seniority_fit=0, explanation="", matched_categories={},
            prompt_version="t", dictionary_version="t", role_dictionary_version="t")


class TestPipelineMetricsDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._p = patch.object(config, "DB_PATH", os.path.join(self.tmp, "m.db"))
        self._p.start()
        database.initialize()
        _seed()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scoring_metrics(self):
        m = pipeline_metrics.scoring_metrics()
        self.assertEqual(m["jobs_scored"], 6)
        self.assertEqual(m["jobs_score_ge_80"], 1)        # only 85
        self.assertEqual(m["jobs_score_ge_70"], 3)        # 85,72,71
        self.assertEqual(m["distribution"]["80+"], 1)
        self.assertEqual(m["distribution"]["70-80"], 2)
        self.assertTrue(0 <= m["p50"] <= 100)
        self.assertEqual(m["top_companies_by_score"][0]["score"], 85)

    def test_source_and_taxonomy(self):
        src = pipeline_metrics.source_performance()
        self.assertEqual(src.get("greenhouse", 0) + src.get("lever", 0), 6)
        tax = pipeline_metrics.taxonomy_metrics()
        self.assertEqual(tax["category_distribution"]["product_management"], 6)
        self.assertEqual(tax["track_a"] + tax["track_b"], 6)

    def test_funnel_and_dashboard(self):
        f = pipeline_metrics.pipeline_funnel()["funnel"]
        stages = [s["stage"] for s in f]
        self.assertEqual(stages[:3], ["Fetched", "Prefilter Kept", "Scored"])
        self.assertEqual(next(s for s in f if s["stage"] == "Scored")["count"], 6)
        d = pipeline_metrics.dashboard_data()
        self.assertIn("scoring", d)
        self.assertIn("funnel", d)
        self.assertIn("source_performance", d)


class TestRecordRun(unittest.TestCase):
    def test_record_and_last_run(self):
        pipeline_metrics.record_run({
            "run_id": "r1",
            "prefilter_stats": {"total": 100, "kept": 30, "geo_rejected": 50, "role_rejected": 20},
            "research_stats": {"researched": 3, "skipped_fresh": 1},
            "recommendations": [1, 2, 3],
        })
        lr = pipeline_metrics.last_run()
        self.assertEqual(lr["run_id"], "r1")
        self.assertEqual(lr["prefilter"]["kept"], 30)
        self.assertEqual(lr["recommendations"], 3)


class TestObservabilityApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from api.app import app
        cls.client = TestClient(app)

    def test_endpoints(self):
        for path in ("/api/v2/metrics/funnel", "/api/v2/metrics/scoring",
                     "/api/v2/metrics/acquisition", "/api/v2/metrics/dashboard"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_dashboard_html(self):
        r = self.client.get("/dashboard")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Pipeline Dashboard", r.text)


if __name__ == "__main__":
    unittest.main()
