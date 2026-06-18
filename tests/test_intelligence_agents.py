"""Tests for the company-research and document-generation agents.

Claude is always mocked — no live API calls. Both agents must also degrade
gracefully when ANTHROPIC_API_KEY is absent.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents import document_agent, research_agent

_JOB = {
    "id": 1, "title": "AI Product Manager", "company": "Acme",
    "location": "Milan", "description": "Build AI products.",
}

_RESEARCH_JSON = {
    "mission": "Acme builds AI tools.",
    "recent_news": "Raised Series B.",
    "ai_initiatives": "LLM platform.",
    "mba_opportunities": "Rotational program.",
    "sponsorship_likelihood": "High — international team.",
    "product_maturity": "Mature PM org.",
    "networking_strategy": "Connect with PM leads.",
    "research_quality": 8,
}


class TestResearchAgent(unittest.TestCase):
    def test_skips_without_api_key(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(research_agent.database, "get_jobs_above_threshold",
                          return_value=[_JOB]):
            stats = research_agent.run(min_score=80)
        self.assertEqual(stats["researched"], 0)
        self.assertIn("ANTHROPIC_API_KEY", stats["reason"])

    def test_researches_and_persists(self):
        captured = {}

        def _fake_insert(**kwargs):
            captured.update(kwargs)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}), \
             patch.object(research_agent.database, "get_jobs_above_threshold",
                          return_value=[_JOB]), \
             patch.object(research_agent.database, "get_research_for_job", return_value=None), \
             patch.object(research_agent, "_call_claude", return_value=_RESEARCH_JSON), \
             patch.object(research_agent.database, "insert_research", side_effect=_fake_insert):
            stats = research_agent.run(min_score=80)

        self.assertEqual(stats["researched"], 1)
        self.assertEqual(captured["mission"], "Acme builds AI tools.")
        self.assertEqual(captured["research_quality"], 8)
        self.assertEqual(captured["research_quality_tier"], "Strong")
        # The 7 structured aspects are persisted as JSON in talking_points.
        self.assertIn("networking_strategy", captured["talking_points"])

    def test_skips_fresh_research(self):
        from datetime import datetime
        fresh = {"id": 99, "researched_at": datetime.now().isoformat()}
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}), \
             patch.object(research_agent.database, "get_jobs_above_threshold",
                          return_value=[_JOB]), \
             patch.object(research_agent.database, "get_research_for_job",
                          return_value=fresh), \
             patch.object(research_agent, "_call_claude") as call:
            stats = research_agent.run(min_score=80)
        self.assertEqual(stats["skipped_fresh"], 1)
        call.assert_not_called()


class TestDocumentAgent(unittest.TestCase):
    def test_skips_without_api_key(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(document_agent.database, "get_jobs_above_threshold",
                          return_value=[_JOB]):
            stats = document_agent.run(min_score=80)
        self.assertEqual(stats["resumes"], 0)
        self.assertIn("ANTHROPIC_API_KEY", stats["reason"])

    def test_generates_resume_and_cover_letter(self):
        inserted = []
        with tempfile.TemporaryDirectory() as td, \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}), \
             patch.object(document_agent.config, "OUTPUTS_DIR", td), \
             patch.object(document_agent.database, "get_jobs_above_threshold",
                          return_value=[_JOB]), \
             patch.object(document_agent.database, "get_research_for_job", return_value=None), \
             patch.object(document_agent.database, "get_latest_document", return_value=None), \
             patch.object(document_agent, "load_profile", return_value={"personal": {"name": "Darpan"}}), \
             patch.object(document_agent, "_call_claude", return_value="# Resume\nContent here."), \
             patch.object(document_agent.database, "insert_document",
                          side_effect=lambda **kw: inserted.append(kw["doc_type"])):
            stats = document_agent.run(min_score=80)

            self.assertEqual(stats["resumes"], 1)
            self.assertEqual(stats["cover_letters"], 1)
            self.assertEqual(sorted(inserted), ["cover_letter", "resume"])
            docs = list((Path(td) / "documents").glob("*.md"))
            self.assertEqual(len(docs), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
