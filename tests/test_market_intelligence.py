"""Market intelligence tests — demand, salary, snapshots, trends, emerging skills."""
import unittest

from tests._career_support import CareerDB
from core import tenancy
from core.market_intelligence import (
    DemandAnalyzer,
    JobMarketCollector,
    MarketSnapshotService,
    SalaryIntelligence,
    TrendAnalyzer,
)

JOBS = [
    {"title": "AI Product Manager", "role_category": "AI Product Manager",
     "location": "Milan", "industry": "tech", "skills": ["Python", "ML"]},
    {"title": "AI Product Manager", "role_category": "AI Product Manager",
     "location": "Berlin", "industry": "tech", "skills": ["Python"]},
    {"title": "Data Analyst", "role_category": "Data Analyst",
     "location": "Milan", "industry": "finance", "skills": ["SQL"]},
]


class TestCollectorAndDemand(unittest.TestCase):
    def test_collect_and_demand_breakdowns(self):
        obs = JobMarketCollector().collect(JOBS)
        d = DemandAnalyzer()
        self.assertEqual(d.by_role(obs)["ai-product-manager"], 2)
        self.assertEqual(d.by_geography(obs)["Milan"], 2)
        self.assertEqual(d.by_industry(obs)["tech"], 2)
        self.assertEqual(d.top_roles(obs, 1), [("ai-product-manager", 2)])


class TestSalary(CareerDB):
    def test_percentiles(self):
        si = SalaryIntelligence()
        b = si.benchmark("AI PM", "Milan", [50000, 60000, 70000, 80000, 90000])
        self.assertEqual(b["p50"], 70000)
        self.assertLess(b["p25"], b["p50"])
        self.assertLess(b["p50"], b["p75"])
        self.assertEqual(b["sample_size"], 5)

    def test_empty_salaries(self):
        b = SalaryIntelligence().benchmark("AI PM", "Milan", [])
        self.assertEqual(b["sample_size"], 0)
        self.assertIsNone(b["p50"])

    def test_persist_and_lookup(self):
        si = SalaryIntelligence()
        si.benchmark("AI PM", "Milan", [50000, 70000, 90000], persist=True)
        found = si.lookup("AI PM", "Milan")
        self.assertEqual(found["p50"], 70000)
        self.assertIsNone(si.lookup("AI PM", "Berlin"))


class TestSnapshotsAndTrends(CareerDB):
    def test_snapshot_history_and_growth(self):
        svc = MarketSnapshotService()
        col = JobMarketCollector()
        svc.capture("AI Product Manager", "", col.collect(JOBS[:1]))
        svc.capture("AI Product Manager", "", col.collect(JOBS[:2]))
        history = svc.history("AI Product Manager")
        self.assertEqual([h["demand_count"] for h in history], [1, 2])
        self.assertEqual(TrendAnalyzer().demand_growth(history), 1.0)

    def test_emerging_skills(self):
        svc = MarketSnapshotService()
        col = JobMarketCollector()
        early = [{"title": "X", "role_category": "X", "location": "Milan", "skills": ["Python"]}]
        late = [{"title": "X", "role_category": "X", "location": "Milan",
                 "skills": ["Python", "LLM", "LLM"]}]
        svc.capture("X", "", col.collect(early))
        svc.capture("X", "", col.collect(late))
        emerging = TrendAnalyzer().emerging_skills(svc.history("X"), min_growth=1.0)
        self.assertIn("llm", emerging)

    def test_snapshot_tenant_isolation(self):
        svc = MarketSnapshotService()
        col = JobMarketCollector()
        with tenancy.use_tenant("acme"):
            svc.capture("AI Product Manager", "", col.collect(JOBS[:2]))
        with tenancy.use_tenant("beta"):
            self.assertEqual(svc.history("AI Product Manager"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
