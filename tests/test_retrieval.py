"""Tests for core.retrieval — RAG over the candidate's experience corpus."""
import os
import unittest
from unittest.mock import patch

from core import retrieval

_PROFILE = {
    "value_proposition": "MBA candidate blending AI strategy and product management.",
    "experience": [
        {"title": "HR Analyst", "company": "Acme", "responsibilities": [
            "Built people analytics dashboards and workforce planning models in SQL and Tableau.",
            "Led talent acquisition analytics across hiring funnels."]},
        {"title": "Product Intern", "company": "Beta", "responsibilities": [
            "Owned the product roadmap and AI feature strategy for a SaaS platform.",
            "Ran A/B tests and product analytics to prioritise features."]},
    ],
    "education": [{"institution": "POLIMI", "degree": "MBA", "field": "AI",
                   "key_coursework": ["AI Strategy", "Digital Transformation"]}],
    "skills": {"hr_core": ["People Analytics", "Workforce Planning", "Talent Acquisition"],
               "product": ["Product Strategy", "Roadmapping", "A/B Testing"]},
    "metadata": {"profile_version": "test"},
}


class TestChunking(unittest.TestCase):
    def test_chunks_are_nonempty_and_sourced(self):
        retrieval.clear_cache()
        chunks = retrieval._chunk_profile(_PROFILE)
        self.assertGreaterEqual(len(chunks), 6)          # summary + 4 bullets + edu + skills
        self.assertTrue(all(c["text"].strip() for c in chunks))
        self.assertTrue(all(c["source"] for c in chunks))

    def test_empty_profile_yields_no_chunks(self):
        self.assertEqual(retrieval._chunk_profile({}), [])


class TestEnabled(unittest.TestCase):
    def test_enabled_when_env_set(self):
        with patch.dict(os.environ, {"ENABLE_RAG": "1"}):
            self.assertTrue(retrieval.is_enabled())

    def test_disabled_by_default(self):
        env = {k: v for k, v in os.environ.items() if k != "ENABLE_RAG"}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(retrieval.is_enabled())


class TestRetrieve(unittest.TestCase):
    def test_people_analytics_jd_retrieves_hr_experience_first(self):
        retrieval.clear_cache()
        job = {"title": "People Analytics Intern",
               "description": "Workforce planning, HR analytics dashboards, talent acquisition data."}
        hits = retrieval.retrieve(job, _PROFILE, k=3)
        if not hits:                       # sentence-transformers / numpy unavailable
            self.skipTest("embeddings unavailable in this environment")
        self.assertLessEqual(len(hits), 3)
        # The top hit should be the HR/people-analytics material, not product roadmap.
        top = hits[0]["text"].lower()
        self.assertTrue(any(w in top for w in
                            ("people analytics", "workforce", "talent", "hr")))
        self.assertEqual(hits, sorted(hits, key=lambda h: h["score"], reverse=True))

    def test_product_jd_retrieves_product_experience_first(self):
        retrieval.clear_cache()
        job = {"title": "Product Manager Intern",
               "description": "Product roadmap, feature prioritisation, A/B testing, product strategy."}
        hits = retrieval.retrieve(job, _PROFILE, k=3)
        if not hits:
            self.skipTest("embeddings unavailable in this environment")
        self.assertTrue(any(w in hits[0]["text"].lower() for w in
                            ("product", "roadmap", "a/b", "feature")))


if __name__ == "__main__":
    unittest.main()
