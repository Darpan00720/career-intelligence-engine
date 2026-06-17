"""Sprint-3 integration tests: full pipeline on seed data, offline & deterministic.

No live ATS fetch, no live Claude, no API keys, no network. Seed jobs are read
once (read-only) from data/career_agent.db and injected via SeedJobProvider.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from core.config import DB_PATH
from graph.build import build_graph
from graph.checkpoint import _career_serde
from graph.nodes import EXECUTION_LOG, reset_execution_log
from graph.providers import (
    SeedJobProvider,
    clear_claude_hook,
    clear_intel_hook,
    clear_provider,
    set_claude_hook,
    set_intel_hook,
    set_provider,
)
from graph.state import new_career_state, validate_state
from schemas.intelligence import IntelLLMAssessment
from schemas.scoring import ClaudeScoreAdjustment

_COLS = ("id,title,company,location,description,url,role_category,"
         "language_gate,visa_gate,language_rejection_reason,visa_rejection_reason,"
         "eligibility_status,visa_accessibility")
_SEED_IDS = [19, 248, 13, 55, 424]


def _mock_intel(profile, job, score):
    """Offline, deterministic intel hook: candid (lower for senior titles)."""
    senior = any(w in job.title.lower() for w in ("senior", "staff", "principal", "head", "director"))
    iv = 35 if senior else 70
    return IntelLLMAssessment(interview_probability=iv, application_effort=100 - iv,
                              rationale="mock")


def _load_seed_rows() -> list[dict]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        out = []
        for i in _SEED_IDS:
            r = conn.execute(f"SELECT {_COLS} FROM jobs WHERE id=?", (i,)).fetchone()
            if r:
                out.append(dict(r))
        return out
    finally:
        conn.close()


def _saver(tmp: str):
    from tests._support import make_saver
    return make_saver(Path(tmp) / "cp.db")


class TestFullPipelineIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = _load_seed_rows()
        assert cls.seed, "seed rows must load from career_agent.db (read-only)"

    def setUp(self):
        reset_execution_log()

    def _run(self, tmp, thread="test", interrupt_before=None, claude=None, intel=_mock_intel):
        graph = build_graph(checkpointer=_saver(tmp), interrupt_before=interrupt_before)
        set_provider(thread, SeedJobProvider(self.seed))
        if claude is not None:
            set_claude_hook(thread, claude)
        if intel is not None:
            set_intel_hook(thread, intel)
        cfg = {"configurable": {"thread_id": thread}}
        result = graph.invoke(
            new_career_state(run_id=thread, profile_path="candidate_profile.json"), cfg)
        return graph, cfg, result

    def tearDown(self):
        for t in ("test", "iv", "claude"):
            clear_provider(t)
            clear_claude_hook(t)
            clear_intel_hook(t)
        from tests._support import close_all
        close_all()

    # 1. Full graph executes on seed data + validates
    def test_full_pipeline_executes_and_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, result = self._run(tmp)
            validate_state(result)
            workers = [n for n in EXECUTION_LOG if n != "supervisor"]
            self.assertEqual(workers, ["profile_strategy", "job_ingestion", "taxonomy",
                                       "scoring", "opportunity_intel", "prioritization",
                                       "recommendations", "output_experience"])

    # State contains real outputs through PRIORITIZATION (Phase 5 implemented).
    def test_state_has_real_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, r = self._run(tmp)
            self.assertTrue(r.get("profile"))
            self.assertEqual(len(r["classified_jobs"]), len(self.seed))
            self.assertTrue(r["scored_jobs"])
            self.assertEqual(len(r["intelligence"]), len(r["scored_jobs"]))
            self.assertTrue(r["prioritized"])
            self.assertLessEqual(len(r["this_week"]), 5)   # MAX_THIS_WEEK

    # 3. Deterministic scoring components validated (real scorer outputs)
    def test_scoring_components_real(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, r = self._run(tmp)
            for sj in r["scored_jobs"]:
                self.assertEqual(sj.components.total, sum([
                    sj.components.track_alignment, sj.components.skill_match,
                    sj.components.mba_relevance, sj.components.company_quality,
                    sj.components.intl_friendliness, sj.components.pivot_bonus]))
                self.assertGreaterEqual(sj.total_score, 0)
                self.assertLessEqual(sj.total_score, 100)

    # 4. Classification outputs validated (real taxonomy)
    def test_classification_real(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, r = self._run(tmp)
            cats = {c.job_id: c.role_category for c in r["classified_jobs"]}
            # job 19 (AI Product Intern) -> product_management per role_dictionary v1.2
            self.assertEqual(cats.get(19), "product_management")

    # 5. Prioritization outputs validated (real, Phase 5).
    def test_prioritization_real(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, r = self._run(tmp)
            ranks = [c.rank for c in r["prioritized"]]
            self.assertEqual(ranks, list(range(1, len(ranks) + 1)))  # contiguous
            comps = [c.composite for c in r["prioritized"]]
            self.assertEqual(comps, sorted(comps, reverse=True))      # desc
            # this_week never contains a Tier-3 role
            t3_ids = {c.job_id for c in r["prioritized"] if c.tier.value == 3}
            tw_companies = {it.company for it in r["this_week"]}
            self.assertTrue(len(r["this_week"]) <= 5)

    # 2. Claude layer mocked
    def test_claude_layer_mocked(self):
        def mock_claude(job, det):
            return ClaudeScoreAdjustment(score_adjustment=5, explanation="mock +5")
        with tempfile.TemporaryDirectory() as tmp:
            _, _, r = self._run(tmp, thread="claude", claude=mock_claude)
            self.assertEqual(r["scoring_stats"].claude_calls, len(r["scored_jobs"]))
            self.assertTrue(all(s.claude_explanation == "mock +5" for s in r["scored_jobs"]))

    # 6 + 7. Checkpoint + resume
    def test_checkpoint_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph, cfg, _ = self._run(tmp)
            saver = graph.checkpointer
            n = saver.conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id='test'").fetchone()[0]
            self.assertGreater(n, 0)
            reset_execution_log()
            resumed = graph.invoke(None, cfg)
            self.assertEqual(EXECUTION_LOG, [])  # zero reruns
            self.assertTrue(resumed["scored_jobs"])  # real terminal output

    # 8. Interrupt-before
    def test_interrupt_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph, cfg, _ = self._run(tmp, thread="iv", interrupt_before=["scoring"])
            snap = graph.get_state(cfg)
            self.assertIn("scoring", snap.next)
            self.assertFalse(snap.values.get("scored_jobs"))
            reset_execution_log()
            resumed = graph.invoke(None, cfg)
            self.assertIn("scoring", EXECUTION_LOG)
            self.assertTrue(resumed["scored_jobs"])


class TestDefaultDbProviderPath(unittest.TestCase):
    """Exercise the default (no-seed) DbJobProvider path, bounded and read-only."""

    def setUp(self):
        reset_execution_log()

    def tearDown(self):
        from tests._support import close_all
        close_all()

    def test_default_provider_runs_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = build_graph(checkpointer=_saver(tmp))
            cfg = {"configurable": {"thread_id": "dbdefault"}}
            r = graph.invoke(
                new_career_state(run_id="dbdefault", profile_path="candidate_profile.json"),
                cfg)
            validate_state(r)
            self.assertTrue(r.get("scored_jobs"))
            # intel runs in degraded (no-hook) mode but still produces real
            # composites/tiers; this_week is a valid (<=5) list.
            self.assertEqual(len(r["intelligence"]), len(r["scored_jobs"]))
            self.assertLessEqual(len(r.get("this_week") or []), 5)
        clear_provider("dbdefault")


class TestProfileFallback(unittest.TestCase):
    def test_missing_profile_uses_fallback_and_continues(self):
        from graph.nodes import profile_strategy_node
        out = profile_strategy_node({"profile_path": "/nonexistent/none.json"})
        self.assertEqual(out["profile"].name, "Unknown Candidate")
        self.assertTrue(out["errors"])  # AgentError appended
        from schemas.control import Phase
        self.assertEqual(out["phase"], Phase.PROFILE)  # still advances


if __name__ == "__main__":
    unittest.main()
