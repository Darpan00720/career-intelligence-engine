"""Foundation tests: Pydantic schema validation rules."""
import unittest

from pydantic import ValidationError

from schemas import (
    COMPOSITE_WEIGHTS,
    ApplyItem,
    CareerProfile,
    ClassifiedJob,
    GateResult,
    IngestedJob,
    OpportunityIntelligence,
    PriorityBucket,
    ScoreComponents,
    ScoredJob,
    Track,
    WeeklyPlan,
)


class TestScoreComponents(unittest.TestCase):
    def test_total_computed(self):
        c = ScoreComponents(track_alignment=28, skill_match=20, mba_relevance=10,
                            company_quality=13, intl_friendliness=9, pivot_bonus=0)
        self.assertEqual(c.total, 80)

    def test_bounds_enforced(self):
        with self.assertRaises(ValidationError):
            ScoreComponents(track_alignment=31, skill_match=0, mba_relevance=0,
                            company_quality=0, intl_friendliness=0, pivot_bonus=0)


class TestIntelligenceBounds(unittest.TestCase):
    def test_score_range_rejected(self):
        with self.assertRaises(ValidationError):
            OpportunityIntelligence(job_id=1, mba_fit=150, ai_dt_fit=0, brand=0,
                                    interview_prob=0, sponsorship_prob=0,
                                    pivot_value=0, effort=0, roi=0)

    def test_valid_record(self):
        oi = OpportunityIntelligence(job_id=1, mba_fit=80, ai_dt_fit=60, brand=80,
                                     interview_prob=60, sponsorship_prob=65,
                                     pivot_value=75, effort=50, roi=70)
        self.assertEqual(oi.status, "ok")


class TestExtraForbidden(unittest.TestCase):
    def test_unknown_field_rejected(self):
        with self.assertRaises(ValidationError):
            ClassifiedJob(job_id=1, role_category="business_strategy",
                          track=Track.A, bogus="x")


class TestWeeklyPlanCap(unittest.TestCase):
    def _item(self, n):
        return ApplyItem(company=f"C{n}", title="T", composite=50.0,
                         interview_prob=50, roi=50, rationale="r")

    def test_max_five(self):
        with self.assertRaises(ValidationError):
            WeeklyPlan(this_week=[self._item(i) for i in range(6)])

    def test_five_ok(self):
        plan = WeeklyPlan(this_week=[self._item(i) for i in range(5)])
        self.assertEqual(len(plan.this_week), 5)


class TestCompositeWeights(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(COMPOSITE_WEIGHTS.values()), 1.0, places=9)


class TestProfileAndScoredJob(unittest.TestCase):
    def test_profile_requires_primary_and_location(self):
        with self.assertRaises(ValidationError):
            CareerProfile(name="X", years_experience=6, target_primary=[],
                          locations=["EU"])

    def test_scored_job_roundtrip(self):
        sj = ScoredJob(
            job_id=19, total_score=81,
            components=ScoreComponents(track_alignment=28, skill_match=25,
                                       mba_relevance=10, company_quality=13,
                                       intl_friendliness=5, pivot_bonus=0),
            priority_bucket=PriorityBucket.HIGH, semantic_similarity=0.5,
        )
        self.assertEqual(sj.priority_bucket, PriorityBucket.HIGH)

    def test_ingested_job_gate_nesting(self):
        j = IngestedJob(job_id=1, title="t", company="c", url="u",
                        geography_gate=GateResult(passed=True, reason="eu"),
                        language_gate=GateResult(passed=True, reason="en"),
                        visa_gate=GateResult(passed=True, reason="ok"),
                        eligible=True)
        self.assertTrue(j.eligible)


if __name__ == "__main__":
    unittest.main()
