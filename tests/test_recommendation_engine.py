"""Recommendation engine tests — scoring, ranking, explainability, learning
paths, feedback incorporation. Plus a v3 backward-compatibility guard."""
import unittest

from tests._career_support import CareerDB
from core.career_graph import CareerGraph
from core.recommendation_engine import (
    CAREER_PATH,
    LearningPathGenerator,
    OpportunityScorer,
    RecommendationEngine,
    RecommendationExplainer,
    RecommendationItem,
    RecommendationRanker,
    recommend,            # v3 — must still exist
    recommend_all,        # v3
)
from core.skill_intelligence import SkillProfiler

NORMALIZED = {
    "profile_id": "p1",
    "skills": [
        {"skill": "python", "name": "Python", "category": "hard", "confidence": 0.9},
        {"skill": "sql", "name": "SQL", "category": "hard", "confidence": 0.8},
        {"skill": "product-management", "name": "Product Management",
         "category": "hard", "confidence": 0.7},
    ],
    "experience_months": 48,
}


def _graph():
    g = CareerGraph()
    g.add_role("AI Product Manager",
               required_skills=["python", "product management", "machine learning", "sql"])
    g.add_role("Data Analyst", required_skills=["sql"])
    g.add_skill("machine learning")
    g.add_prerequisite("machine learning", "python")
    return g


class TestScorerAndExplainer(unittest.TestCase):
    def test_scorer_blend_bounded(self):
        s = OpportunityScorer()
        self.assertEqual(s.score(coverage=1.0, demand=10, demand_max=10, salary_fit=1.0), 100.0)
        self.assertGreaterEqual(s.score(coverage=0.0, demand=0, demand_max=1, salary_fit=0.0), 0.0)

    def test_explainer_reasons(self):
        ex = RecommendationExplainer()
        reasons = ex.build_reasons(coverage=0.75, demand=12, gaps=[{"skill": "ml"}])
        self.assertTrue(any("75%" in r for r in reasons))
        self.assertTrue(any("demand" in r for r in reasons))


class TestRanker(unittest.TestCase):
    def test_feedback_weights_reorder(self):
        a = RecommendationItem(CAREER_PATH, "role-a", score=70)
        b = RecommendationItem(CAREER_PATH, "role-b", score=72)
        ranker = RecommendationRanker()
        self.assertEqual([i.target for i in ranker.rank([a, b])], ["role-b", "role-a"])
        # strong positive feedback on role-a lifts it above role-b
        reordered = ranker.rank([a, b], {"role-a": 10.0})
        self.assertEqual([i.target for i in reordered], ["role-a", "role-b"])


class TestRecommendationEngine(CareerDB):
    def test_recommend_roles_scored_and_explained(self):
        eng = RecommendationEngine(graph=_graph())
        items = eng.recommend_roles(NORMALIZED, demand={"ai-product-manager": 5})
        self.assertTrue(items)
        top = items[0]
        self.assertGreater(top.score, 0)
        self.assertTrue(0 <= top.confidence <= 1.0)
        self.assertTrue(top.reasons)

    def test_persist_and_feedback_incorporation(self):
        eng = RecommendationEngine(graph=_graph())
        items = eng.recommend_roles(NORMALIZED, candidate_roles=["AI Product Manager"],
                                    persist=True)
        rec_id = items[0].recommendation_id
        eng.record_feedback(rec_id, "accepted", profile_id="p1")
        weights = eng.feedback_weights()
        self.assertGreater(weights.get("ai-product-manager", 0), 0)

    def test_learning_path_orders_prerequisites_first(self):
        eng = RecommendationEngine(graph=_graph())
        path = eng.recommend_learning(NORMALIZED, "AI Product Manager", persist=True)
        skills_in_order = [s["skill"] for s in path["steps"]]
        self.assertIn("machine-learning", skills_in_order)
        # python (prerequisite) appears before machine-learning
        self.assertLess(skills_in_order.index("python"),
                        skills_in_order.index("machine-learning"))
        self.assertEqual(path["total_steps"], len(path["steps"]))

    def test_learning_path_generator_standalone(self):
        gen = LearningPathGenerator(_graph())
        sp = SkillProfiler().build(NORMALIZED)
        path = gen.generate(sp, "Data Analyst")
        self.assertEqual(path["target_role"], "data-analyst")


class TestFeedbackPlatform(CareerDB):
    def test_feedback_acceptance_rate(self):
        from core.feedback_manager import FeedbackManager
        fm = FeedbackManager()
        fm.record("r1", "accepted")
        fm.record("r2", "applied")
        fm.record("r3", "rejected")
        self.assertEqual(fm.acceptance_rate(), round(2 / 3, 4))
        self.assertEqual(len(fm.signals_for("r1")), 1)

    def test_offline_evaluation_metrics(self):
        from core.feedback_manager import RecommendationEvaluator
        ev = RecommendationEvaluator()
        ranked = ["a", "b", "c", "d"]
        relevant = {"a", "c"}
        self.assertEqual(ev.precision_at_k(ranked, relevant, k=2), 0.5)
        # perfect ranking scores NDCG 1.0
        self.assertEqual(ev.ndcg_at_k(["a", "c", "b"], relevant, k=2), 1.0)

    def test_experiment_assignment_deterministic(self):
        from core.feedback_manager import ExperimentManager
        em = ExperimentManager()
        em.create("rank_exp", "ranking", ["control", "treatment"])
        first = em.assign("rank_exp", "user-42")
        self.assertEqual(first, em.assign("rank_exp", "user-42"))
        self.assertIn(first, {"control", "treatment"})

    def test_ab_winner_selection(self):
        from core.feedback_manager import ABTestingFramework, ExperimentManager
        em = ExperimentManager()
        em.create("rank_exp", "ranking", ["control", "treatment"])
        for _ in range(5):
            em.record_event("rank_exp", "treatment", "conversion", 1.0)
        for _ in range(5):
            em.record_event("rank_exp", "control", "conversion", 0.0)
        ab = ABTestingFramework(em)
        self.assertEqual(ab.conclude("rank_exp"), "treatment")

    def test_feature_flag_rollout(self):
        from core.feedback_manager import FeatureFlagManager
        ff = FeatureFlagManager()
        ff.set_flag("new_ranker", enabled=True, rollout=1.0)
        self.assertTrue(ff.is_enabled("new_ranker"))
        ff.set_flag("new_ranker", enabled=False, rollout=1.0)
        self.assertFalse(ff.is_enabled("new_ranker"))
        # unknown flag → default
        self.assertTrue(ff.is_enabled("missing", default=True))


class TestV3BackwardCompat(unittest.TestCase):
    def test_v3_recommend_still_works(self):
        job = {"total_score": 95, "role_category": "ai_strategy", "title": "AI Lead"}
        rec = recommend(job)
        self.assertIn("recommendation", rec)
        rows = recommend_all([{"id": 1, **job}])
        self.assertEqual(len(rows), 1)
        self.assertIn("recommendation_rank", rows[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
