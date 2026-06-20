"""Tests for the writable job-acquisition node (graph/ingestion_writable.py).

Proves the two-layer idempotency: a run-level replay guard (same run re-running
does no work) and row-level dedup (a different run over the same data inserts
nothing new). Uses a real temp-file SQLite DB so the actual persistence path
(insert_job / dedup / unique url) is exercised.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core import config, database
from graph import ingestion_writable, providers


_SEED = [
    {"title": "Product Manager Intern", "company": "Acme",
     "location": "Milan, Italy", "url": "https://acme.test/jobs/1",
     "description": "Internship for an aspiring product manager based in Milan. " * 3,
     "job_board": "seed", "posted_date": "2026-06-01"},
    {"title": "People Analytics Intern", "company": "Globex",
     "location": "Berlin, Germany", "url": "https://globex.test/jobs/2",
     "description": "A graduate internship in people analytics in Berlin. " * 3,
     "job_board": "seed", "posted_date": "2026-06-02"},
]


def _count_jobs() -> int:
    with database.get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


class TestWritableIngestion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "ingest_test.db")
        self._p = patch.object(config, "DB_PATH", self.db_path)
        self._p.start()
        database.initialize()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        for rid in ("run1", "runA", "runB"):
            ingestion_writable.clear_raw_source(rid)
            providers.clear_provider(rid)

    def test_first_run_persists(self):
        ingestion_writable.set_raw_source("run1", ingestion_writable.SeedRawSource(_SEED))
        out = ingestion_writable.acquire_jobs_node({"run_id": "run1"})
        self.assertEqual(_count_jobs(), len(_SEED))
        self.assertEqual(out["audit_log"], ["acquire_jobs"])
        self.assertEqual(out["ingestion_stats"].passed, len(_SEED))

    def test_replay_same_run_is_noop(self):
        src = ingestion_writable.SeedRawSource(_SEED)
        ingestion_writable.set_raw_source("run1", src)
        ingestion_writable.acquire_jobs_node({"run_id": "run1"})
        n_after_first = _count_jobs()

        # Re-run the SAME node for the SAME run (simulates checkpoint replay).
        out2 = ingestion_writable.acquire_jobs_node({"run_id": "run1"})
        self.assertEqual(_count_jobs(), n_after_first)          # no new rows
        self.assertIn("skipped", out2["audit_log"][0])           # guard tripped

    def test_row_dedup_across_runs(self):
        ingestion_writable.set_raw_source("runA", ingestion_writable.SeedRawSource(_SEED))
        ingestion_writable.acquire_jobs_node({"run_id": "runA"})
        before = _count_jobs()

        # A *different* run over identical data: run-level guard does NOT apply,
        # but row-level dedup (dedup_hash / unique url) must prevent new inserts.
        ingestion_writable.set_raw_source("runB", ingestion_writable.SeedRawSource(_SEED))
        ingestion_writable.acquire_jobs_node({"run_id": "runB"})
        self.assertEqual(_count_jobs(), before)

    def test_registers_readonly_provider_for_downstream(self):
        ingestion_writable.set_raw_source("run1", ingestion_writable.SeedRawSource(_SEED))
        ingestion_writable.acquire_jobs_node({"run_id": "run1"})
        # The existing read-only provider should now project what we acquired.
        prov = providers.get_provider("run1")
        self.assertIsInstance(prov, providers.DbJobProvider)
        self.assertEqual(len(prov.fetch()), len(_SEED))


if __name__ == "__main__":
    unittest.main()
