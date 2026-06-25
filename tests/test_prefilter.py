"""Tests for core.prefilter.prefilter_jobs + the prefilter_jobs graph node.

End-to-end "downstream receives filtered jobs" and "checkpoint replay works" are
exercised by tests/test_replay_idempotency.py, whose acquisition flow now routes
acquire -> prefilter -> job_ingestion.
"""
import os
import unittest
from unittest.mock import patch

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
                      "Digital Transformation Analyst", "Machine Learning Intern"):
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
                                      "role_rejected", "seniority_rejected",
                                      "non_intern_rejected", "kept", "total"})


class TestInternOnly(unittest.TestCase):
    def test_intern_only_keeps_only_intern_roles(self):
        with patch.dict(os.environ, {"INTERN_ONLY": "1"}):
            jobs = [_job(title="Product Manager"),              # mid -> drop
                    _job(title="Product Manager Intern"),       # keep
                    _job(title="AI Strategy Graduate Programme"),  # keep
                    _job(title="Data Analyst"),                 # mid -> drop
                    _job(title="Working Student – Product")]    # keep
            kept, stats = prefilter_jobs(jobs, None)
        titles = {j["title"] for j in kept}
        self.assertIn("Product Manager Intern", titles)
        self.assertIn("AI Strategy Graduate Programme", titles)
        self.assertIn("Working Student – Product", titles)
        self.assertNotIn("Product Manager", titles)
        self.assertNotIn("Data Analyst", titles)
        self.assertEqual(stats["non_intern_rejected"], 2)

    def test_off_by_default_keeps_plain_roles(self):
        env = {k: v for k, v in os.environ.items() if k != "INTERN_ONLY"}
        with patch.dict(os.environ, env, clear=True):
            kept, _ = prefilter_jobs([_job(title="Product Manager")], None)
        self.assertEqual(len(kept), 1)   # intern-only off -> plain PM kept

    def test_senior_roles_rejected(self):
        for title in ("(Senior) Product Manager", "Senior Product Manager",
                      "Staff Product Manager", "Principal Product Manager",
                      "Lead Product Strategy", "Head of Product",
                      "Director of Strategy", "VP Product"):
            kept, stats = prefilter_jobs([_job(title=title)], None)
            self.assertEqual(kept, [], title)
            self.assertEqual(stats["seniority_rejected"], 1, title)

    def test_intern_and_plain_roles_kept(self):
        for title in ("Product Manager Intern", "AI Product Manager Intern",
                      "Product Manager", "Junior Product Analyst",
                      "Data Analyst", "Senior Product Manager Internship"):
            kept, _ = prefilter_jobs([_job(title=title)], None)
            self.assertEqual(len(kept), 1, title)   # intern override / not senior


class TestGeoFocus(unittest.TestCase):
    def test_focus_keeps_target_countries(self):
        with patch.dict(os.environ, {"GEO_COUNTRIES": "it,nl"}):
            for loc in ("Milan, Italy", "Rome", "Turin", "Amsterdam", "Rotterdam",
                        "The Hague", "Remote", "Remote - Europe", "Remote (Italy)"):
                kept, _ = prefilter_jobs([_job(location=loc)], None)
                self.assertEqual(len(kept), 1, loc)

    def test_focus_rejects_other_countries(self):
        with patch.dict(os.environ, {"GEO_COUNTRIES": "it,nl"}):
            for loc in ("Berlin, Germany", "London", "Madrid, Spain", "Paris",
                        "Dublin, Ireland", "Hybrid - Berlin", "New York, USA"):
                kept, stats = prefilter_jobs([_job(location=loc)], None)
                self.assertEqual(kept, [], loc)
                self.assertEqual(stats["geo_rejected"], 1, loc)

    def test_romania_not_mistaken_for_italy(self):
        # "roma" inside "Romania" must NOT false-match Italy focus.
        with patch.dict(os.environ, {"GEO_COUNTRIES": "it,nl"}):
            kept, stats = prefilter_jobs([_job(location="Bucharest, Romania")], None)
            self.assertEqual(kept, [], "Romania should be outside it,nl focus")

    def test_explicit_empty_focus_keeps_eu_wide(self):
        with patch.dict(os.environ, {"GEO_COUNTRIES": ""}):
            kept, _ = prefilter_jobs([_job(location="Berlin, Germany")], None)
            self.assertEqual(len(kept), 1)   # legacy EU-wide keeps Berlin


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
