"""Tests for the acquisition node: persists full payloads to the DB and emits
ONLY lightweight refs to state (no description/raw_data → small checkpoints).
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
     "location": "Amsterdam, Netherlands", "url": "https://globex.test/jobs/2",
     "description": "A graduate internship in people analytics in Amsterdam. " * 3,
     "job_board": "seed", "posted_date": "2026-06-02"},
]


def _count_jobs() -> int:
    with database.get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


class TestWritableIngestion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._p = patch.object(config, "DB_PATH", os.path.join(self.tmp, "ingest_test.db"))
        self._p.start()
        database.initialize()
        ingestion_writable.set_raw_source("run1", ingestion_writable.SeedRawSource(_SEED))

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        ingestion_writable.clear_raw_source("run1")
        providers.clear_provider("run1")

    def test_acquire_persists_full_payload(self):
        ingestion_writable.acquire_jobs_node({"run_id": "run1"})
        self.assertEqual(_count_jobs(), len(_SEED))            # full payload in DB

    def test_state_jobs_are_lightweight_only(self):
        out = ingestion_writable.acquire_jobs_node({"run_id": "run1"})
        self.assertEqual(len(out["jobs"]), len(_SEED))
        for ref in out["jobs"]:                                # NO heavy fields in state
            self.assertEqual(set(ref), {"url", "title", "company", "location", "is_expired"})
            self.assertNotIn("description", ref)
            self.assertNotIn("raw_data", ref)

    def test_acquire_registers_provider(self):
        ingestion_writable.acquire_jobs_node({"run_id": "run1"})
        self.assertIsInstance(providers.get_provider("run1"), providers.DbJobProvider)

    def test_acquire_is_idempotent(self):
        ingestion_writable.acquire_jobs_node({"run_id": "run1"})
        n1 = _count_jobs()
        ingestion_writable.acquire_jobs_node({"run_id": "run1"})   # re-run same batch
        self.assertEqual(_count_jobs(), n1)                        # row dedup -> no dups


if __name__ == "__main__":
    unittest.main()
