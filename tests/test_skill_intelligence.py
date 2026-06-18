"""Skills intelligence tests — proficiency, similarity, matching, gap analysis."""
import unittest

from tests._career_support import CareerDB
from core.career_graph import CareerGraph
from core.skill_intelligence import (
    ProficiencyEstimator,
    SkillGapAnalyzer,
    SkillMatcher,
    SkillProfiler,
    SkillSimilarityEngine,
)

NORMALIZED = {
    "skills": [
        {"skill": "python", "name": "Python", "category": "hard", "confidence": 0.9},
        {"skill": "sql", "name": "SQL", "category": "hard", "confidence": 0.8},
        {"skill": "leadership", "name": "Leadership", "category": "soft", "confidence": 0.7},
    ],
    "experience_months": 60,
}


class TestProficiency(unittest.TestCase):
    def test_more_evidence_higher_proficiency(self):
        est = ProficiencyEstimator()
        low = est.estimate(mentions=1, experience_months=0, base_confidence=0.5)
        high = est.estimate(mentions=5, experience_months=120, base_confidence=1.0)
        self.assertGreater(high["score"], low["score"])
        self.assertEqual(high["band"], "expert")

    def test_score_bounded(self):
        est = ProficiencyEstimator()
        self.assertLessEqual(est.estimate(mentions=99, experience_months=999)["score"], 100.0)


class TestProfiler(unittest.TestCase):
    def test_build_groups_by_category(self):
        sp = SkillProfiler().build(NORMALIZED)
        self.assertEqual({s["skill"] for s in sp.hard}, {"python", "sql"})
        self.assertEqual({s["skill"] for s in sp.soft}, {"leadership"})
        self.assertIn("python", sp.all_slugs())


class TestSimilarity(unittest.TestCase):
    def test_identical_is_one(self):
        sim = SkillSimilarityEngine()
        self.assertEqual(sim.similarity("Python", "python"), 1.0)

    def test_clustering(self):
        sim = SkillSimilarityEngine()
        clusters = sim.cluster(["machine learning", "machine learning ops", "cooking"],
                               threshold=0.4)
        # the two ML-ish terms cluster together, cooking stands alone
        sizes = sorted(len(c) for c in clusters)
        self.assertEqual(sizes, [1, 2])


class TestMatcher(unittest.TestCase):
    def test_match_coverage_and_missing(self):
        m = SkillMatcher(threshold=0.6)
        result = m.match(["python", "sql"], ["python", "sql", "tableau"])
        self.assertAlmostEqual(result.coverage, 2 / 3, places=2)
        self.assertEqual([x["required"] for x in result.missing], ["tableau"])
        self.assertIn("coverage", result.explanation)


class TestGapAnalysis(CareerDB):
    def _graph(self):
        g = CareerGraph()
        g.add_role("Data Scientist",
                   required_skills=["python", "sql", "machine learning", "statistics"])
        g.add_skill("machine learning")
        g.add_prerequisite("machine learning", "statistics")
        return g

    def test_gap_analysis_explainable(self):
        g = self._graph()
        sp = SkillProfiler().build(NORMALIZED)
        analysis = SkillGapAnalyzer(g).analyze(sp, "Data Scientist")
        self.assertLess(analysis["coverage"], 1.0)
        gap_skills = {x["skill"] for x in analysis["gaps"]}
        self.assertIn("machine-learning", gap_skills)
        ml_gap = next(x for x in analysis["gaps"] if x["skill"] == "machine-learning")
        self.assertIn("statistics", ml_gap["prerequisites"])
        self.assertTrue(ml_gap["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
