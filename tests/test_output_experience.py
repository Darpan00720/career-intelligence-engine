"""Tests for core/output_experience + graph integration (Phase 7)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from core.config import DB_PATH
from core.output_experience import (
    build_api_response,
    build_dashboard,
    build_export_payload,
    build_summary,
)
from graph.build import build_graph
from graph.checkpoint import _career_serde
from graph.nodes import EXECUTION_LOG, reset_execution_log
from graph.providers import SeedJobProvider, clear_intel_hook, clear_provider, set_intel_hook, set_provider
from graph.state import new_career_state, validate_state
from schemas.intelligence import IntelLLMAssessment
from schemas.output import ApiResponse, CareerSummary, Dashboard, ExportPayload

_COLS = ("id,title,company,location,description,url,role_category,language_gate,visa_gate,"
         "language_rejection_reason,visa_rejection_reason,eligibility_status,visa_accessibility")


def _intel(p, j, s):
    senior = any(w in j.title.lower() for w in ("senior", "staff", "principal"))
    iv = 35 if senior else 70
    return IntelLLMAssessment(interview_probability=iv, application_effort=100 - iv, rationale="m")


def _seed():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(conn.execute(f"SELECT {_COLS} FROM jobs WHERE id=?", (i,)).fetchone())
            for i in (19, 248, 13, 55, 424)]
    conn.close()
    return rows


def _run(tmp, thread, interrupt_before=None):
    from tests._support import make_saver
    g = build_graph(checkpointer=make_saver(Path(tmp) / "cp.db"),
                    interrupt_before=interrupt_before)
    set_provider(thread, SeedJobProvider(_seed())); set_intel_hook(thread, _intel)
    cfg = {"configurable": {"thread_id": thread}}
    r = g.invoke(new_career_state(run_id=thread, profile_path="candidate_profile.json"), cfg)
    return g, cfg, r


class TestBuilders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_execution_log()
        with tempfile.TemporaryDirectory() as tmp:
            _, _, cls.state = _run(tmp, "ox_b")
        clear_provider("ox_b"); clear_intel_hook("ox_b")

    @classmethod
    def tearDownClass(cls):
        from tests._support import close_all
        close_all()

    def test_summary_deterministic(self):
        a = build_summary(self.state).model_dump_json()
        b = build_summary(self.state).model_dump_json()
        self.assertEqual(a, b)
        self.assertIsInstance(build_summary(self.state), CareerSummary)

    def test_summary_sections_nonempty(self):
        s = build_summary(self.state)
        self.assertTrue(s.career_direction and s.application_status and s.recommended_focus)
        self.assertGreaterEqual(len(s.immediate_actions), 1)

    def test_dashboard_reconciles(self):
        d = build_dashboard(self.state)
        appd = d.application_summary.data
        self.assertEqual(appd["tier_1"] + appd["tier_2"] + appd["tier_3"],
                         len(self.state["prioritized"]))
        self.assertEqual(sum(appd["by_type"].values()), len(self.state["recommendations"]))
        # top opportunities reference valid recommendations
        rec_ids = {r.job_id for r in self.state["recommendations"]}
        for top in d.top_opportunities:
            self.assertIn(top.job_id, rec_ids)

    def test_dashboard_pipeline_counts(self):
        d = build_dashboard(self.state).pipeline_summary.data
        self.assertEqual(d["scored_jobs"], len(self.state["scored_jobs"]))
        self.assertEqual(d["recommendations_generated"], len(self.state["recommendations"]))

    def test_export_reproducible_and_serializable(self):
        e1 = build_export_payload(self.state)
        e2 = build_export_payload(self.state)
        self.assertIsInstance(e1, ExportPayload)
        self.assertEqual(e1.to_json(), e2.to_json())  # reproducible
        # round-trip
        ExportPayload.model_validate_json(e1.to_json())

    def test_api_response_deterministic(self):
        a = build_api_response(self.state)
        self.assertIsInstance(a, ApiResponse)
        self.assertTrue(a.success)
        self.assertEqual(a.version, "1.0")
        self.assertEqual(a.generated_at, "")  # deterministic core
        self.assertEqual(build_api_response(self.state).model_dump_json(),
                         build_api_response(self.state).model_dump_json())

    def test_api_response_serializable(self):
        a = build_api_response(self.state)
        ApiResponse.model_validate_json(a.model_dump_json())


class TestGraphIntegration(unittest.TestCase):
    def setUp(self):
        reset_execution_log()

    def tearDown(self):
        from tests._support import close_all
        for t in ("ox", "ox_i"):
            clear_provider(t); clear_intel_hook(t)
        close_all()

    def test_full_pipeline_produces_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, r = _run(tmp, "ox")
            validate_state(r)
            for key in ("summary", "dashboard", "exports", "api_response"):
                self.assertIn(key, r)
                self.assertIsNotNone(r[key])
            self.assertEqual([n for n in EXECUTION_LOG if n != "supervisor"][-1],
                             "output_experience")

    def test_checkpoint_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            g, cfg, _ = _run(tmp, "ox")
            n = g.checkpointer.conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id='ox'").fetchone()[0]
            self.assertGreater(n, 0)
            reset_execution_log()
            resumed = g.invoke(None, cfg)
            self.assertEqual(EXECUTION_LOG, [])
            self.assertIsNotNone(resumed["api_response"])

    def test_interrupt_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            g, cfg, _ = _run(tmp, "ox_i", interrupt_before=["output_experience"])
            snap = g.get_state(cfg)
            self.assertIn("output_experience", snap.next)
            self.assertIsNone(snap.values.get("api_response"))
            reset_execution_log()
            resumed = g.invoke(None, cfg)
            self.assertIn("output_experience", EXECUTION_LOG)
            self.assertIsNotNone(resumed["summary"])


if __name__ == "__main__":
    unittest.main()
