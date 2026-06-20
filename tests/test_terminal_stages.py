"""Tests for the terminal-stage runner (graph/terminal_stages.py) and the
export + tracker stages.

The runner executes enabled terminal stages (documents/export/tracker) in order
behind a single Phase.TERMINAL + one routing gate. Export is a projection
(idempotent overwrite); tracker is persistence (insert-if-missing).
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import config, database
from graph import export_writable, planner, terminal_stages, tracker_writable
from schemas.control import Phase


def _seed_scored(*job_ids: int) -> None:
    for jid in job_ids:
        with database.get_connection() as conn:
            conn.execute("INSERT INTO jobs (id, title, company) VALUES (?,?,?)",
                         (jid, f"Role {jid}", f"Company {jid}"))
        database.insert_score(
            job_id=jid, role_category="pm", total_score=85, role_fit=10, skills_match=10,
            location_fit=10, seniority_fit=10, explanation="", matched_categories={},
            prompt_version="t", dictionary_version="t", role_dictionary_version="t")


# ── Wiring ───────────────────────────────────────────────────────────────────

class TestTerminalWiring(unittest.TestCase):
    def test_disabled_plan_has_no_terminal(self):
        with patch.dict(os.environ, {"ENABLE_DOCUMENTS": "0", "ENABLE_EXPORT": "0",
                                     "ENABLE_TRACKER": "0"}):
            ids = planner.build_execution_plan("").task_ids()
        self.assertNotIn("terminal", ids)
        self.assertEqual(len(ids), 8)

    def test_any_enabled_inserts_terminal_before_output(self):
        with patch.dict(os.environ, {"ENABLE_EXPORT": "1"}):
            plan = planner.build_execution_plan("")
            ids = plan.task_ids()
            self.assertIn("terminal", ids)
            self.assertLess(ids.index("recommendations"), ids.index("terminal"))
            self.assertLess(ids.index("terminal"), ids.index("output_experience"))
            out = next(t for t in plan.tasks if t.id == "output_experience")
            self.assertEqual(out.depends_on, ["terminal"])

    def test_routing_gated(self):
        from graph import routing
        with patch.dict(os.environ, {"ENABLE_DOCUMENTS": "0", "ENABLE_EXPORT": "0",
                                     "ENABLE_TRACKER": "0"}):
            self.assertEqual(routing.route_from_supervisor({"phase": Phase.RECOMMENDATIONS}),
                             "output_experience")
        with patch.dict(os.environ, {"ENABLE_TRACKER": "1"}):
            self.assertEqual(routing.route_from_supervisor({"phase": Phase.RECOMMENDATIONS}),
                             "terminal")
        self.assertEqual(routing.route_from_supervisor({"phase": Phase.TERMINAL}),
                         "output_experience")

    def test_registered_and_documents_not_standalone(self):
        from graph.build import _WORKERS
        self.assertIn("terminal", _WORKERS)
        self.assertNotIn("documents", _WORKERS)


# ── Ordering / gate orthogonality ─────────────────────────────────────────────

class TestTerminalOrdering(unittest.TestCase):
    def _run_with(self, env: dict) -> list[str]:
        order: list[str] = []

        def mk(name):
            def _fn(state):
                order.append(name)
                return {"ran": name}
            return _fn

        with patch.dict(os.environ, env), \
                patch("graph.documents_writable.run_documents_stage", mk("documents")), \
                patch("graph.export_writable.run_export_stage", mk("export")), \
                patch("graph.tracker_writable.run_tracker_stage", mk("tracker")):
            out = terminal_stages.terminal_node({"run_id": "t"})
        self.assertEqual(out["phase"], Phase.TERMINAL)
        self.assertEqual(set(out["terminal_stats"]), set(order))
        return order

    def test_all_enabled_runs_in_canonical_order(self):
        order = self._run_with({"ENABLE_DOCUMENTS": "1", "ENABLE_EXPORT": "1",
                                "ENABLE_TRACKER": "1"})
        self.assertEqual(order, ["documents", "export", "tracker"])

    def test_subset_skips_disabled(self):
        order = self._run_with({"ENABLE_DOCUMENTS": "0", "ENABLE_EXPORT": "1",
                                "ENABLE_TRACKER": "1"})
        self.assertEqual(order, ["export", "tracker"])

    def test_one_stage_failure_isolated(self):
        def boom(state):
            raise RuntimeError("x")

        with patch.dict(os.environ, {"ENABLE_EXPORT": "1", "ENABLE_TRACKER": "1"}), \
                patch("graph.export_writable.run_export_stage", boom), \
                patch("graph.tracker_writable.run_tracker_stage",
                      lambda s: {"created": 0}):
            out = terminal_stages.terminal_node({"run_id": "t"})
        self.assertIn("error", out["terminal_stats"]["export"])
        self.assertEqual(out["terminal_stats"]["tracker"], {"created": 0})  # still ran


# ── Export (projection) + Tracker (persistence) idempotency ───────────────────

class TestExportTrackerIdempotency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._db = patch.object(config, "DB_PATH", os.path.join(self.tmp, "t.db"))
        self._db.start()
        self._out = patch.object(config, "OUTPUTS_DIR", Path(self.tmp) / "outputs")
        self._out.start()
        database.initialize()
        _seed_scored(1, 2)

    def tearDown(self):
        self._out.stop()
        self._db.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_overwrites_same_path(self):
        r1 = export_writable.run_export_stage({})
        r2 = export_writable.run_export_stage({})
        self.assertEqual(r1["path"], r2["path"])           # deterministic path
        self.assertTrue(r1["path"].endswith("jobs_master.xlsx"))
        self.assertTrue(Path(r1["path"]).exists())          # still a single file

    def test_tracker_insert_if_missing(self):
        r1 = tracker_writable.run_tracker_stage({})
        self.assertEqual(r1["created"], 2)                  # one row per scored job
        r2 = tracker_writable.run_tracker_stage({})
        self.assertEqual(r2["created"], 0)                  # idempotent: nothing new
        with database.get_connection() as conn:
            n = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        self.assertEqual(n, 2)


if __name__ == "__main__":
    unittest.main()
