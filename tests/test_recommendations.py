"""Tests for core/recommendations.generate_recommendations + graph integration."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from core.config import DB_PATH
from core.recommendations import generate_recommendations
from graph.build import build_graph
from graph.checkpoint import _career_serde
from graph.nodes import EXECUTION_LOG, reset_execution_log
from graph.providers import SeedJobProvider, clear_intel_hook, clear_provider, set_intel_hook, set_provider
from graph.state import new_career_state, validate_state
from schemas.control import Tier, Track
from schemas.intelligence import IntelLLMAssessment, OpportunityIntelligence
from schemas.jobs import ClassifiedJob, GateResult, IngestedJob
from schemas.planning import CompositeScore
from schemas.recommendations import RecommendationType


def _oi(jid, iv=70, ai=80, roi=65, mba=60, brand=60, spons=60, effort=30):
    return OpportunityIntelligence(job_id=jid, mba_fit=mba, ai_dt_fit=ai, brand=brand,
                                   interview_prob=iv, sponsorship_prob=spons,
                                   pivot_value=ai, effort=effort, roi=roi)


def _cs(jid, comp, tier, rank):
    return CompositeScore(job_id=jid, composite=comp, tier=tier, rank=rank)


def _job(jid, company="C"):
    g = GateResult(passed=True, reason="ok")
    return IngestedJob(job_id=jid, title=f"T{jid}", company=company, location="Berlin",
                       url="u", geography_gate=g, language_gate=g, visa_gate=g, eligible=True)


def _cls(jid, role):
    return ClassifiedJob(job_id=jid, role_category=role, track=Track.A)


class TestCategorization(unittest.TestCase):
    def _one(self, oi, cs, role="business_strategy"):
        recs = generate_recommendations([oi], [cs], {oi.job_id: _job(oi.job_id)},
                                        {oi.job_id: _cls(oi.job_id, role)})
        return recs[0]

    def test_tier1_apply_immediately(self):
        r = self._one(_oi(1), _cs(1, 70.0, Tier.TIER_1, 1))
        self.assertEqual(r.recommendation_type, RecommendationType.APPLY_IMMEDIATELY)

    def test_tier2_high_roi_apply_this_week(self):
        r = self._one(_oi(1, iv=70, roi=65), _cs(1, 60.0, Tier.TIER_2, 1))
        self.assertEqual(r.recommendation_type, RecommendationType.APPLY_THIS_WEEK)

    def test_tier2_stretch_role(self):
        # low interview + high strategic fit -> STRETCH
        r = self._one(_oi(1, iv=30, ai=85, roi=50), _cs(1, 55.0, Tier.TIER_2, 1))
        self.assertEqual(r.recommendation_type, RecommendationType.STRETCH_ROLE)

    def test_tier2_low_roi_monitor(self):
        r = self._one(_oi(1, iv=70, ai=50, roi=35), _cs(1, 52.0, Tier.TIER_2, 1))
        self.assertEqual(r.recommendation_type, RecommendationType.MONITOR)

    def test_tier3_monitor(self):
        r = self._one(_oi(1, roi=70), _cs(1, 40.0, Tier.TIER_3, 1))
        self.assertEqual(r.recommendation_type, RecommendationType.MONITOR)


class TestContent(unittest.TestCase):
    def test_always_has_preparation_action(self):
        recs = generate_recommendations([_oi(1)], [_cs(1, 70.0, Tier.TIER_1, 1)],
                                        {1: _job(1)}, {1: _cls(1, "product_management")})
        self.assertGreaterEqual(len(recs[0].preparation_actions), 1)

    def test_product_role_prep(self):
        recs = generate_recommendations([_oi(1)], [_cs(1, 70.0, Tier.TIER_1, 1)],
                                        {1: _job(1)}, {1: _cls(1, "product_management")})
        actions = " ".join(a.action.lower() for a in recs[0].preparation_actions)
        self.assertIn("product", actions)

    def test_low_interview_adds_networking(self):
        recs = generate_recommendations([_oi(1, iv=30, ai=85)], [_cs(1, 55.0, Tier.TIER_2, 1)],
                                        {1: _job(1, company="Stripe")}, {1: _cls(1, "business_strategy")})
        actions = " ".join(a.action.lower() for a in recs[0].preparation_actions)
        self.assertIn("network", actions)

    def test_reason_is_explainable(self):
        r = generate_recommendations([_oi(1)], [_cs(1, 66.95, Tier.TIER_1, 1)],
                                     {1: _job(1)}, {1: _cls(1, "business_strategy")})[0]
        self.assertIn("composite", r.recommendation_reason)
        self.assertIn("66.95", r.recommendation_reason)


class TestDeterminism(unittest.TestCase):
    def test_deterministic_and_ranked_order(self):
        ois = [_oi(3), _oi(1), _oi(2)]
        css = [_cs(3, 70.0, Tier.TIER_1, 3), _cs(1, 90.0, Tier.TIER_1, 1), _cs(2, 80.0, Tier.TIER_1, 2)]
        jobs = {i: _job(i) for i in (1, 2, 3)}
        cls = {i: _cls(i, "business_strategy") for i in (1, 2, 3)}
        r1 = generate_recommendations(ois, css, jobs, cls)
        r2 = generate_recommendations(ois, css, jobs, cls)
        self.assertEqual([x.job_id for x in r1], [1, 2, 3])  # by rank
        self.assertEqual([x.job_id for x in r1], [x.job_id for x in r2])

    def test_missing_intel_skipped(self):
        recs = generate_recommendations([], [_cs(1, 70.0, Tier.TIER_1, 1)], {1: _job(1)}, {})
        self.assertEqual(recs, [])


_COLS = ("id,title,company,location,description,url,role_category,language_gate,visa_gate,"
         "language_rejection_reason,visa_rejection_reason,eligibility_status,visa_accessibility")


def _intel(p, j, s):
    senior = any(w in j.title.lower() for w in ("senior", "staff", "principal"))
    iv = 35 if senior else 70
    return IntelLLMAssessment(interview_probability=iv, application_effort=100 - iv, rationale="m")


class TestGraphIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cls.seed = [dict(conn.execute(f"SELECT {_COLS} FROM jobs WHERE id=?", (i,)).fetchone())
                    for i in (19, 248, 13, 55, 424)]
        conn.close()

    def setUp(self):
        reset_execution_log()

    def _graph(self, tmp, interrupt_before=None):
        from tests._support import make_saver
        return build_graph(checkpointer=make_saver(Path(tmp) / "cp.db"),
                           interrupt_before=interrupt_before)

    def tearDown(self):
        from tests._support import close_all
        for t in ("recs", "recs_i"):
            clear_provider(t); clear_intel_hook(t)
        close_all()

    def test_full_pipeline_produces_recommendations(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._graph(tmp)
            set_provider("recs", SeedJobProvider(self.seed)); set_intel_hook("recs", _intel)
            cfg = {"configurable": {"thread_id": "recs"}}
            r = g.invoke(new_career_state(run_id="recs", profile_path="candidate_profile.json"), cfg)
            validate_state(r)
            workers = [n for n in EXECUTION_LOG if n != "supervisor"]
            self.assertIn("recommendations", workers)
            self.assertEqual(workers[-1], "output_experience")
            self.assertEqual(len(r["recommendations"]), len(r["prioritized"]))
            valid_ids = {c.job_id for c in r["prioritized"]}
            for rec in r["recommendations"]:
                self.assertIn(rec.job_id, valid_ids)
                self.assertGreaterEqual(len(rec.preparation_actions), 1)

    def test_checkpoint_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._graph(tmp)
            set_provider("recs", SeedJobProvider(self.seed)); set_intel_hook("recs", _intel)
            cfg = {"configurable": {"thread_id": "recs"}}
            g.invoke(new_career_state(run_id="recs", profile_path="p.json"), cfg)
            n = g.checkpointer.conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id='recs'").fetchone()[0]
            self.assertGreater(n, 0)
            reset_execution_log()
            resumed = g.invoke(None, cfg)
            self.assertEqual(EXECUTION_LOG, [])
            self.assertTrue(resumed["recommendations"])

    def test_interrupt_before_recommendations(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = self._graph(tmp, interrupt_before=["recommendations"])
            set_provider("recs_i", SeedJobProvider(self.seed)); set_intel_hook("recs_i", _intel)
            cfg = {"configurable": {"thread_id": "recs_i"}}
            g.invoke(new_career_state(run_id="recs_i", profile_path="p.json"), cfg)
            snap = g.get_state(cfg)
            self.assertIn("recommendations", snap.next)
            self.assertFalse(snap.values.get("recommendations"))
            reset_execution_log()
            resumed = g.invoke(None, cfg)
            self.assertIn("recommendations", EXECUTION_LOG)
            self.assertTrue(resumed["recommendations"])


if __name__ == "__main__":
    unittest.main()
