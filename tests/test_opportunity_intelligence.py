"""Unit tests for core/opportunity_intelligence.assess() — spec v1.0."""
import unittest

from core.opportunity_intelligence import assess
from schemas.control import Track
from schemas.intelligence import IntelLLMAssessment
from schemas.jobs import ClassifiedJob, GateResult, IngestedJob
from schemas.profile import CareerProfile
from schemas.scoring import PriorityBucket, ScoreComponents, ScoredJob


def _profile():
    return CareerProfile(name="C", years_experience=6, strengths=["x"], gaps=[],
                         target_primary=["business_strategy"], locations=["EU"])


def _job(jid=1, company="StubCo"):
    g = GateResult(passed=True, reason="ok")
    return IngestedJob(job_id=jid, title="Strategy & Ops", company=company, location="Berlin",
                       url="u", geography_gate=g, language_gate=g, visa_gate=g, eligible=True)


def _scored(jid=1, ta=24, sk=20, mba=15, cq=15, intl=10, pivot=0, total=80):
    comp = ScoreComponents(track_alignment=ta, skill_match=sk, mba_relevance=mba,
                           company_quality=cq, intl_friendliness=intl, pivot_bonus=pivot)
    return ScoredJob(job_id=jid, total_score=total, components=comp,
                     priority_bucket=PriorityBucket.MEDIUM)


class _NoCompanyProfile:
    visa_friendliness_score = 5


class TestDeterministicMetrics(unittest.TestCase):
    def test_scaling_formulas(self):
        oi = assess(_profile(), _job(), ClassifiedJob(job_id=1, role_category="business_strategy",
                    track=Track.A), _scored(ta=30, mba=15, cq=15),
                    company_profile=_NoCompanyProfile())
        self.assertEqual(oi.mba_fit, 100)       # 15/15*100
        self.assertEqual(oi.ai_dt_fit, 100)     # 30/30*100
        self.assertEqual(oi.brand, 100)         # 15/15*100
        self.assertEqual(oi.pivot_value, 100)   # reuses track_alignment

    def test_sponsorship_formula(self):
        # visa_accessibility=40 -> 100; visa_friendliness=10 -> 100; intl=10 -> 100
        class CP: visa_friendliness_score = 10
        oi = assess(_profile(), _job(), ClassifiedJob(job_id=1, role_category="x", track=Track.A),
                    _scored(intl=10), company_profile=CP(),
                    eligibility={"visa_accessibility": 40})
        self.assertEqual(oi.sponsorship_prob, 100)  # 0.5*100+0.3*100+0.2*100

    def test_sponsorship_partial(self):
        class CP: visa_friendliness_score = 0
        oi = assess(_profile(), _job(), ClassifiedJob(job_id=1, role_category="x", track=Track.A),
                    _scored(intl=0), company_profile=CP(),
                    eligibility={"visa_accessibility": 0})
        self.assertEqual(oi.sponsorship_prob, 0)


class TestLLMMetrics(unittest.TestCase):
    def test_hook_used(self):
        def hook(p, j, s):
            return IntelLLMAssessment(interview_probability=42, application_effort=58, rationale="r")
        oi = assess(_profile(), _job(), ClassifiedJob(job_id=1, role_category="x", track=Track.A),
                    _scored(), company_profile=_NoCompanyProfile(), claude_hook=hook)
        self.assertEqual(oi.interview_prob, 42)
        self.assertEqual(oi.effort, 58)
        self.assertEqual(oi.status, "ok")

    def test_no_hook_degraded_fallback(self):
        oi = assess(_profile(), _job(), ClassifiedJob(job_id=1, role_category="x", track=Track.A),
                    _scored(total=70), company_profile=_NoCompanyProfile())
        self.assertEqual(oi.interview_prob, 70)       # fallback = total_score
        self.assertEqual(oi.effort, 30)               # 100 - 70
        self.assertEqual(oi.status, "degraded")

    def test_hook_exception_degraded(self):
        def hook(p, j, s):
            raise RuntimeError("llm down")
        oi = assess(_profile(), _job(), ClassifiedJob(job_id=1, role_category="x", track=Track.A),
                    _scored(total=55), company_profile=_NoCompanyProfile(), claude_hook=hook)
        self.assertEqual(oi.interview_prob, 55)
        self.assertEqual(oi.status, "degraded")


class TestROI(unittest.TestCase):
    def test_roi_formula(self):
        # interview=80, pivot=100, sponsorship=50, effort=0
        def hook(p, j, s):
            return IntelLLMAssessment(interview_probability=80, application_effort=0, rationale="r")
        class CP: visa_friendliness_score = 0
        oi = assess(_profile(), _job(), ClassifiedJob(job_id=1, role_category="x", track=Track.A),
                    _scored(ta=30, intl=0), company_profile=CP(),
                    eligibility={"visa_accessibility": 40}, claude_hook=hook)
        # sponsorship = 0.5*100+0.3*0+0.2*0 = 50 ; pivot=100
        # base = 0.5*80 + 0.3*100 + 0.2*50 = 40+30+10 = 80 ; effort 0 -> roi 80
        self.assertEqual(oi.roi, 80)

    def test_roi_effort_discount(self):
        def hook(p, j, s):
            return IntelLLMAssessment(interview_probability=80, application_effort=100, rationale="r")
        class CP: visa_friendliness_score = 0
        oi = assess(_profile(), _job(), ClassifiedJob(job_id=1, role_category="x", track=Track.A),
                    _scored(ta=30, intl=0), company_profile=CP(),
                    eligibility={"visa_accessibility": 40}, claude_hook=hook)
        # base = 80 ; effort 100 -> roi = round(80 * (1 - 0.30)) = 56
        self.assertEqual(oi.roi, 56)


if __name__ == "__main__":
    unittest.main()
