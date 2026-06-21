"""Tests for core.prefilter.prefilter_jobs + the prefilter_jobs graph node.

End-to-end "downstream receives filtered jobs" and "checkpoint replay works" are
exercised by tests/test_replay_idempotency.py, whose acquisition flow now routes
acquire -> prefilter -> job_ingestion.
"""
import unittest

from core.prefilter import prefilter_jobs
from graph.nodes import prefilter_jobs_node
from schemas.control import Phase


def _job(title="Product Manager Intern", company="Acme", location="Milan, Italy", **kw):
    j = {"title": title, "company": company, "location": location}
    j.update(kw)
    return j


class TestPrefilterRules(unittest.TestCase):
    def test_empty_input(self):
        kept, stats = prefilter_jobs([], None)
        self.assertEqual(kept, [])
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["kept"], 0)

    def test_expired_rejected(self):
        kept, stats = prefilter_jobs([_job(is_expired=True)], None)
        self.assertEqual(kept, [])
        self.assertEqual(stats["expired"], 1)

    def test_duplicate_rejected(self):
        kept, stats = prefilter_jobs([_job(), _job()], None)   # identical company+title+loc
        self.assertEqual(len(kept), 1)
        self.assertEqual(stats["duplicates"], 1)

    def test_eu_retained(self):
        kept, _ = prefilter_jobs([_job(location="Milan, Italy")], None)
        self.assertEqual(len(kept), 1)

    def test_non_eu_rejected(self):
        kept, stats = prefilter_jobs([_job(location="New York, USA")], None)
        self.assertEqual(kept, [])
        self.assertEqual(stats["geo_rejected"], 1)

    def test_remote_retained(self):
        kept, _ = prefilter_jobs([_job(location="Remote")], None)
        self.assertEqual(len(kept), 1)

    def test_target_roles_retained(self):
        for title in ("AI Strategy Intern", "Data Analyst", "Product Owner",
                      "Digital Transformation Lead", "Machine Learning Intern"):
            kept, _ = prefilter_jobs([_job(title=title)], None)
            self.assertEqual(len(kept), 1, title)

    def test_unrelated_role_rejected(self):
        kept, stats = prefilter_jobs([_job(title="Plumber")], None)
        self.assertEqual(kept, [])
        self.assertEqual(stats["role_rejected"], 1)

    def test_missing_location_retained(self):
        kept, _ = prefilter_jobs([_job(location=None)], None)  # not "clearly outside"
        self.assertEqual(len(kept), 1)

    def test_missing_title_rejected(self):
        kept, stats = prefilter_jobs([_job(title=None)], None)
        self.assertEqual(kept, [])
        self.assertEqual(stats["role_rejected"], 1)

    def test_stats_shape(self):
        _, stats = prefilter_jobs([_job()], None)
        self.assertEqual(set(stats), {"duplicates", "expired", "geo_rejected",
                                      "role_rejected", "kept", "total"})


class TestPrefilterNode(unittest.TestCase):
    def test_node_executes_filters_and_emits_stats(self):
        from graph.nodes import EXECUTION_LOG, reset_execution_log
        reset_execution_log()
        state = {"jobs": [_job(),
                          _job(location="New York"),     # geo reject
                          _job(title="Plumber")]}        # role reject
        out = prefilter_jobs_node(state)
        self.assertIn("prefilter_jobs", EXECUTION_LOG)   # node executed
        self.assertEqual(out["phase"], Phase.PREFILTER)
        self.assertIn("prefilter_stats", out)            # state carries stats
        self.assertEqual(len(out["jobs"]), 1)            # downstream gets filtered jobs
        self.assertEqual(out["prefilter_stats"]["kept"], 1)

    def test_node_empty_state(self):
        out = prefilter_jobs_node({})
        self.assertEqual(out["jobs"], [])
        self.assertEqual(out["prefilter_stats"]["total"], 0)

    def test_registered_as_graph_worker(self):
        from graph.build import _WORKERS
        self.assertIn("prefilter_jobs", _WORKERS)


if __name__ == "__main__":
    unittest.main()
