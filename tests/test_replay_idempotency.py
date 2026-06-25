"""End-to-end replay/idempotency evidence for the converged write-capable graph.

Unlike the node-level tests, these drive the REAL compiled graph with a real
SqliteSaver checkpointer and BOTH side-effect flags enabled, then:

  * interrupt mid-run (after acquisition + ingestion persisted), resume, and
    verify the resumed run produces no duplicate jobs and no duplicate scores;
  * run two independent threads over identical source data and verify row-level
    idempotency holds end-to-end (no duplicate jobs, no duplicate scores).

Writes go to a temp DB (config.DB_PATH patched); checkpoints to a temp saver.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import config, database
from graph import ingestion_writable, providers
from graph.build import build_graph
from graph.nodes import EXECUTION_LOG, reset_execution_log
from graph.state import new_career_state
from tests._support import close_all, make_saver


_RAW = [
    {"title": "Product Manager Intern", "company": "Acme", "location": "Milan, Italy",
     "url": "https://acme.test/jobs/1", "job_board": "seed", "posted_date": "2026-06-01",
     "description": "Internship for an aspiring product manager in Milan. " * 3},
    {"title": "People Analytics Intern", "company": "Globex", "location": "Amsterdam, Netherlands",
     "url": "https://globex.test/jobs/2", "job_board": "seed", "posted_date": "2026-06-02",
     "description": "A graduate internship in people analytics in Amsterdam. " * 3},
]


def _count(table: str) -> int:
    with database.get_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TestReplayIdempotency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._db = patch.object(config, "DB_PATH", os.path.join(self.tmp, "engine.db"))
        self._db.start()
        database.initialize()
        self._env = patch.dict(os.environ, {"ENABLE_ACQUISITION": "1", "PERSIST_SCORES": "1"})
        self._env.start()
        reset_execution_log()

    def tearDown(self):
        for t in ("resume_t", "runA", "runB", "rs1", "rs2"):
            ingestion_writable.clear_raw_source(t)
            providers.clear_provider(t)
        close_all()
        self._env.stop()
        self._db.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _graph(self, interrupt_before=None):
        return build_graph(checkpointer=make_saver(Path(self.tmp) / "cp.db"),
                           interrupt_before=interrupt_before)

    def test_interrupt_then_resume_no_duplicates(self):
        thread = "resume_t"
        ingestion_writable.set_raw_source(thread, ingestion_writable.SeedRawSource(_RAW))
        cfg = {"configurable": {"thread_id": thread}}

        # Pause after acquisition + ingestion have run, before taxonomy/scoring.
        g = self._graph(interrupt_before=["taxonomy"])
        g.invoke(new_career_state(run_id=thread, profile_path="candidate_profile.json"), cfg)

        self.assertIn("acquire_jobs", EXECUTION_LOG)        # acquisition really ran
        jobs_at_pause = _count("jobs")
        self.assertEqual(jobs_at_pause, len(_RAW))           # jobs persisted
        self.assertEqual(_count("scores"), 0)                # scoring not reached yet

        # Resume from the checkpoint. Completed nodes (incl. acquire_jobs) must
        # NOT re-execute -> no second scrape, no duplicate jobs.
        g.invoke(None, cfg)

        self.assertEqual(_count("jobs"), jobs_at_pause)      # no duplicate jobs on resume
        self.assertEqual(_count("scores"), len(_RAW))        # scores persisted exactly once

    def test_independent_runs_are_idempotent(self):
        # Run the full graph twice on identical source data via two threads.
        for thread in ("runA", "runB"):
            ingestion_writable.set_raw_source(thread, ingestion_writable.SeedRawSource(_RAW))
            g = self._graph()
            g.invoke(new_career_state(run_id=thread, profile_path="candidate_profile.json"),
                     {"configurable": {"thread_id": thread}})
            # After each run the totals are stable: row-level dedup (jobs) and
            # upsert-by-job_id (scores) prevent any duplication across runs.
            self.assertEqual(_count("jobs"), len(_RAW))
            self.assertEqual(_count("scores"), len(_RAW))

    def test_research_idempotent_across_graph_runs(self):
        # Full converged chain (acquire + persist + research) through the real
        # graph, twice. Research is TTL-guarded, so the second run re-scores the
        # same jobs but does NOT issue a second (paid) Claude call or write a
        # duplicate company_research row.
        with patch.dict(os.environ, {"ENABLE_RESEARCH": "1", "ANTHROPIC_API_KEY": "x",
                                     "RESEARCH_MIN_SCORE": "0"}), \
                patch("agents.research_agent._call_claude",
                      return_value={"research_quality": 5, "mission": "m"}) as mock_claude:
            for thread in ("rs1", "rs2"):
                ingestion_writable.set_raw_source(
                    thread, ingestion_writable.SeedRawSource(_RAW))
                g = self._graph()
                g.invoke(new_career_state(run_id=thread, profile_path="candidate_profile.json"),
                         {"configurable": {"thread_id": thread}})
                self.assertEqual(_count("company_research"), len(_RAW))
            # Across both runs, each company researched exactly once (TTL fresh).
            self.assertEqual(mock_claude.call_count, len(_RAW))

    def test_terminal_stages_run_idempotently_in_graph(self):
        # Enable export + tracker (no Claude needed). The terminal runner executes
        # both in the real graph; re-running is idempotent (export overwrites one
        # file; tracker inserts no duplicate application rows).
        out = Path(self.tmp) / "outputs"
        with patch.dict(os.environ, {"ENABLE_EXPORT": "1", "ENABLE_TRACKER": "1"}), \
                patch.object(config, "OUTPUTS_DIR", out):
            for thread in ("rs1", "rs2"):
                ingestion_writable.set_raw_source(
                    thread, ingestion_writable.SeedRawSource(_RAW))
                g = self._graph()
                result = g.invoke(
                    new_career_state(run_id=thread, profile_path="candidate_profile.json"),
                    {"configurable": {"thread_id": thread}})
                self.assertIn("terminal", result.get("audit_log", []))
                self.assertTrue((out / "jobs_master.xlsx").exists())   # single file
                with database.get_connection() as conn:
                    apps = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
                self.assertEqual(apps, _count("scores"))   # one tracker row per scored job


if __name__ == "__main__":
    unittest.main()
