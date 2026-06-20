"""Tests for the opt-in documents stage (graph/documents_writable.py).

Covers planner/routing/build wiring (gated) and the artifact-generation
idempotency model (D2): DB is content-authoritative; the file is a
write-only-if-missing projection. The keystone test deletes the file and proves
it is restored from the DB with NO Claude call.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import config, database
from graph import documents_writable, planner, providers
from schemas.control import Phase
from schemas.scoring import PriorityBucket, ScoreComponents, ScoredJob


def _scored(job_id: int, total: int) -> ScoredJob:
    return ScoredJob(
        job_id=job_id, total_score=total,
        components=ScoreComponents(track_alignment=20, skill_match=18, mba_relevance=10,
                                   company_quality=12, intl_friendliness=6, pivot_bonus=3),
        priority_bucket=PriorityBucket.HIGH, semantic_similarity=0.5, claude_explanation="x")


# Documents is wired via the terminal runner (see test_terminal_stages.py).
# These tests cover the D2 artifact idempotency of the documents stage itself,
# exercised through the standalone documents_node form.

# ── D2 artifact idempotency ───────────────────────────────────────────────────

class TestDocumentsIdempotency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._db = patch.object(config, "DB_PATH", os.path.join(self.tmp, "docs.db"))
        self._db.start()
        self._out = patch.object(config, "OUTPUTS_DIR", Path(self.tmp) / "outputs")
        self._out.start()
        database.initialize()
        with database.get_connection() as conn:
            conn.execute("INSERT INTO jobs (id, title, company, description) VALUES (?,?,?,?)",
                         (1, "PM Intern", "Acme", "desc-one"))
        self._env = patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x", "DOCUMENTS_MIN_SCORE": "70"})
        self._env.start()
        self.state = {"run_id": "dt", "scored_jobs": [_scored(1, 90)]}

    def tearDown(self):
        providers.clear_provider("dt")
        self._env.stop()
        self._out.stop()
        self._db.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_provider(self, description="desc-one"):
        providers.set_provider("dt", providers.SeedJobProvider([
            {"id": 1, "company": "Acme", "title": "PM Intern",
             "location": "Milan", "description": description}]))

    def _docs(self):
        with database.get_connection() as conn:
            return conn.execute("SELECT * FROM documents ORDER BY version, id").fetchall()

    def _files(self):
        return sorted((Path(config.OUTPUTS_DIR) / "documents").glob("*.md"))

    def test_generate_persists_db_and_file(self):
        self._set_provider()
        with patch("agents.document_agent._call_claude", return_value="DRAFT BODY") as m:
            out = documents_writable.documents_node(self.state)
        self.assertEqual(out["documents_stats"]["resumes"], 1)
        self.assertEqual(out["documents_stats"]["cover_letters"], 1)
        self.assertEqual(m.call_count, 2)               # resume + cover letter
        rows = self._docs()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["content"] == "DRAFT BODY" for r in rows))   # DB authoritative
        self.assertEqual(len(self._files()), 2)

    def test_reuse_makes_no_claude_call(self):
        self._set_provider()
        with patch("agents.document_agent._call_claude", return_value="DRAFT BODY") as m:
            documents_writable.documents_node(self.state)
            out2 = documents_writable.documents_node(self.state)   # files present, hashes match
        self.assertEqual(m.call_count, 2)               # no new calls on the second run
        self.assertEqual(out2["documents_stats"]["reused"], 2)
        self.assertEqual(len(self._docs()), 2)          # no new versions

    def test_missing_file_rematerialized_without_claude(self):
        """Keystone D2 test: delete the deliverable; replay restores it for free."""
        self._set_provider()
        with patch("agents.document_agent._call_claude", return_value="DRAFT BODY") as m:
            documents_writable.documents_node(self.state)
            for f in self._files():     # simulate lost filesystem output
                f.unlink()
            self.assertEqual(len(self._files()), 0)

            out = documents_writable.documents_node(self.state)
            self.assertEqual(out["documents_stats"]["rematerialized"], 2)
            self.assertEqual(len(self._files()), 2)     # restored from DB
            self.assertEqual(m.call_count, 2)           # NO extra Claude call
        # restored content matches the authoritative DB content
        for f in self._files():
            self.assertEqual(f.read_text(), "DRAFT BODY")

    def test_atomic_write_leaves_no_truncated_file(self):
        """A crash at the replace boundary must not corrupt the canonical file."""
        from agents.document_agent import atomic_write_text
        target = Path(self.tmp) / "doc.md"
        target.write_text("OLD-COMPLETE")
        # Simulate process death exactly at the atomic commit point.
        with patch("agents.document_agent.os.replace", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                atomic_write_text(target, "NEW-LONGER-CONTENT")
        self.assertEqual(target.read_text(), "OLD-COMPLETE")          # never truncated
        self.assertEqual(list(Path(self.tmp).glob("*.tmp")), [])      # temp cleaned up

    def test_content_change_creates_new_version(self):
        self._set_provider("desc-one")
        with patch("agents.document_agent._call_claude", return_value="V1") as m:
            documents_writable.documents_node(self.state)
            first_calls = m.call_count
            # Job description changes → source_hash changes → regenerate.
            with database.get_connection() as conn:
                conn.execute("UPDATE jobs SET description = ? WHERE id = 1", ("desc-two",))
            self._set_provider("desc-two")
            out = documents_writable.documents_node(self.state)
        self.assertGreater(m.call_count, first_calls)   # regenerated
        self.assertEqual(out["documents_stats"]["resumes"], 1)
        versions = {(r["type"], r["version"]) for r in self._docs()}
        self.assertIn(("resume", 2), versions)          # appended v2, kept v1


if __name__ == "__main__":
    unittest.main()
