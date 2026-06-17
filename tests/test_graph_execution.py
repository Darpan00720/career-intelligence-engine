"""Sprint-2 graph execution tests: trace, validation, checkpoint, resume, interrupt.

Uses a temp-file SqliteSaver per test (never the live checkpoint DB).
"""
import tempfile
import unittest
from pathlib import Path

from graph.build import build_graph
from graph.nodes import EXECUTION_LOG, reset_execution_log
from graph.routing import route_from_supervisor
from graph.state import new_career_state, validate_state
from langgraph.graph import END
from tests._support import close_all, make_saver


def _saver(tmp: str):
    return make_saver(Path(tmp) / "cp.db")


class TestGraphExecution(unittest.TestCase):
    def setUp(self):
        reset_execution_log()

    def tearDown(self):
        close_all()

    def test_invoke_completes_and_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = build_graph(checkpointer=_saver(tmp))
            cfg = {"configurable": {"thread_id": "test"}}
            result = graph.invoke(
                new_career_state(run_id="test", profile_path="candidate_profile.json"),
                config=cfg,
            )
            validate_state(result)  # criterion 1
            self.assertTrue(result.get("scored_jobs"))
            self.assertTrue(result.get("classified_jobs"))

    def test_execution_trace_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = build_graph(checkpointer=_saver(tmp))
            graph.invoke(
                new_career_state(run_id="t", profile_path="p.json"),
                config={"configurable": {"thread_id": "t"}},
            )
            workers = [n for n in EXECUTION_LOG if n != "supervisor"]
            # Phase-7 contract: full path through output_experience.
            self.assertEqual(
                workers,
                ["profile_strategy", "job_ingestion", "taxonomy", "scoring",
                 "opportunity_intel", "prioritization", "recommendations",
                 "output_experience"],
            )  # criterion 2

    def test_checkpoint_row_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            saver = _saver(tmp)
            graph = build_graph(checkpointer=saver)
            cfg = {"configurable": {"thread_id": "test"}}
            graph.invoke(new_career_state(run_id="test", profile_path="p.json"), cfg)
            n = saver.conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id='test'"
            ).fetchone()[0]
            self.assertGreater(n, 0)  # criterion 3
            snapshot = graph.get_state(cfg)
            self.assertTrue(snapshot.values.get("scored_jobs"))

    def test_resume_does_not_rerun_completed_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = build_graph(checkpointer=_saver(tmp))
            cfg = {"configurable": {"thread_id": "test"}}
            graph.invoke(new_career_state(run_id="test", profile_path="p.json"), cfg)
            reset_execution_log()
            resumed = graph.invoke(None, cfg)  # criterion 4
            self.assertEqual(EXECUTION_LOG, [])  # no node re-ran
            self.assertTrue(resumed.get("scored_jobs"))  # state loaded from checkpoint

    def test_interrupt_before_scaffolding(self):
        # Human-in-the-loop scaffolding: pause before scoring, then resume.
        with tempfile.TemporaryDirectory() as tmp:
            graph = build_graph(checkpointer=_saver(tmp), interrupt_before=["scoring"])
            cfg = {"configurable": {"thread_id": "i"}}
            graph.invoke(new_career_state(run_id="i", profile_path="p.json"), cfg)
            snap = graph.get_state(cfg)
            self.assertIn("scoring", snap.next)        # paused before scoring
            self.assertFalse(snap.values.get("scored_jobs"))
            reset_execution_log()
            resumed = graph.invoke(None, cfg)          # resume past the interrupt
            self.assertIn("scoring", EXECUTION_LOG)
            self.assertTrue(resumed.get("scored_jobs"))


class TestRouting(unittest.TestCase):
    def test_routes_through_phases(self):
        # Sprint-3 contract: phase-based progression (robust to empty results).
        from schemas.control import Phase
        self.assertEqual(route_from_supervisor({}), "profile_strategy")
        self.assertEqual(route_from_supervisor({"phase": Phase.PROFILE}), "job_ingestion")
        self.assertEqual(route_from_supervisor({"phase": Phase.INGESTION}), "taxonomy")
        self.assertEqual(route_from_supervisor({"phase": Phase.TAXONOMY}), "scoring")
        self.assertEqual(route_from_supervisor({"phase": Phase.SCORING}), "opportunity_intel")
        self.assertEqual(route_from_supervisor({"phase": Phase.INTELLIGENCE}), "prioritization")
        self.assertEqual(route_from_supervisor({"phase": Phase.PRIORITIZATION}), "recommendations")
        self.assertEqual(route_from_supervisor({"phase": Phase.RECOMMENDATIONS}), "output_experience")
        self.assertEqual(route_from_supervisor({"phase": Phase.OUTPUT}), END)


if __name__ == "__main__":
    unittest.main()
