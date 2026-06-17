"""Unit tests for core/prioritization — spec v1.0 §2."""
import unittest

from core.prioritization import assign_tier, build_plan, composite, prioritize, rank
from schemas.control import Tier
from schemas.intelligence import OpportunityIntelligence
from schemas.jobs import GateResult, IngestedJob


def _oi(jid, iv=0, mba=0, ai=0, spons=0, brand=0, pivot=0, roi=0):
    return OpportunityIntelligence(job_id=jid, mba_fit=mba, ai_dt_fit=ai, brand=brand,
                                   interview_prob=iv, sponsorship_prob=spons,
                                   pivot_value=pivot, effort=0, roi=roi)


def _job(jid):
    g = GateResult(passed=True, reason="ok")
    return IngestedJob(job_id=jid, title=f"T{jid}", company=f"C{jid}", location="Berlin",
                       url="u", geography_gate=g, language_gate=g, visa_gate=g, eligible=True)


class TestComposite(unittest.TestCase):
    def test_weighted_sum(self):
        # all metrics 100 -> composite 100 (weights sum to 1)
        oi = _oi(1, iv=100, mba=100, ai=100, spons=100, brand=100, pivot=100)
        self.assertEqual(composite(oi), 100.0)

    def test_known_combination(self):
        oi = _oi(1, iv=100, mba=0, ai=0, spons=0, brand=0, pivot=0)
        self.assertEqual(composite(oi), 30.0)  # 0.30 weight
        oi2 = _oi(1, iv=0, mba=0, ai=0, spons=0, brand=100, pivot=100)
        self.assertEqual(composite(oi2), 15.0)  # 0.10 + 0.05


class TestAssignTier(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(assign_tier(65.0), Tier.TIER_1)
        self.assertEqual(assign_tier(64.99), Tier.TIER_2)
        self.assertEqual(assign_tier(50.0), Tier.TIER_2)
        self.assertEqual(assign_tier(49.99), Tier.TIER_3)
        self.assertEqual(assign_tier(0.0), Tier.TIER_3)


class TestRank(unittest.TestCase):
    def test_orders_desc(self):
        a = _oi(1, iv=50, mba=100)   # composite = 0.30*50 + 0.20*100 = 35.0
        b = _oi(2, iv=90, mba=100)   # composite = 0.30*90 + 0.20*100 = 47.0
        ranked = rank([a, b])
        self.assertEqual([r.rank for r in ranked], [1, 2])
        self.assertEqual(ranked[0].job_id, 2)  # higher composite first
        self.assertGreaterEqual(ranked[0].composite, ranked[1].composite)

    def test_tiebreak_by_interview_then_jobid(self):
        # identical metrics except job_id -> tie-break to lower job_id last key
        a = _oi(2, iv=80, mba=80, ai=80, spons=80, brand=80, pivot=80)
        b = _oi(1, iv=80, mba=80, ai=80, spons=80, brand=80, pivot=80)
        ranked = rank([a, b])
        # equal composite + equal interview_prob + equal total_score -> job_id asc
        self.assertEqual(ranked[0].job_id, 1)
        self.assertEqual(ranked[1].job_id, 2)


class TestBuildPlanAndPrioritize(unittest.TestCase):
    def _set(self, n, base_iv):
        ois = [_oi(i, iv=base_iv, mba=base_iv, ai=base_iv, spons=base_iv,
                   brand=base_iv, pivot=base_iv, roi=base_iv) for i in range(1, n + 1)]
        jobs = {i: _job(i) for i in range(1, n + 1)}
        return ois, jobs

    def test_this_week_max_5(self):
        ois, jobs = self._set(8, 90)  # all composite 90 -> Tier 1
        plan = prioritize(ois, jobs)
        self.assertEqual(len(plan.this_week), 5)
        self.assertEqual(len(plan.next_week), 3)

    def test_tier3_excluded_from_plan(self):
        ois, jobs = self._set(3, 40)  # composite 40 -> Tier 3
        plan = prioritize(ois, jobs)
        self.assertEqual(plan.this_week, [])
        self.assertEqual(plan.next_week, [])

    def test_mixed_tiers(self):
        ois = [_oi(1, iv=90, mba=90, ai=90, spons=90, brand=90, pivot=90),   # T1
               _oi(2, iv=40, mba=40, ai=40, spons=40, brand=40, pivot=40)]   # T3
        jobs = {1: _job(1), 2: _job(2)}
        plan = prioritize(ois, jobs)
        self.assertEqual(len(plan.this_week), 1)
        self.assertEqual(plan.this_week[0].company, "C1")

    def test_build_plan_uses_rank_output(self):
        ois, jobs = self._set(2, 90)
        ranked = rank(ois)
        plan = build_plan(ranked, ois, jobs)
        self.assertEqual(len(plan.this_week), 2)
        self.assertEqual(plan.this_week[0].interview_prob, 90)


if __name__ == "__main__":
    unittest.main()
