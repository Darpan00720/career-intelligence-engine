"""
Tests for core/semantic_matcher.py and related scorer changes.

sentence-transformers is NOT required to be installed for these tests.
All model calls are mocked so the suite runs in CI without downloading weights.

Run:
    python3 -m unittest tests.test_semantic_matcher -v
"""
import contextlib
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False

_skip_no_numpy = unittest.skipUnless(_NUMPY_AVAILABLE, "numpy not installed")


# ---------------------------------------------------------------------------
# Helpers (only defined when numpy is available)
# ---------------------------------------------------------------------------

def _make_profile(version: str = "1.1") -> dict:
    return {
        "metadata": {"profile_version": version},
        "skills": {
            "ai_and_digital": ["AI Strategy", "Digital Transformation"],
            "strategy":       ["Business Strategy", "Product Thinking"],
        },
        "target_roles": {
            "track_1": ["AI Product Manager Intern"],
        },
        "value_proposition": "MBA candidate with AI & strategy focus.",
        "education": [
            {"key_coursework": ["AI Strategy"], "thesis_or_project": "AI-powered agent."}
        ],
        "experience": [
            {"responsibilities": ["Led product roadmap for AI features."]}
        ],
    }


def _unit_vec(dim: int = 384, seed: int = 0):
    if not _NUMPY_AVAILABLE:
        return None
    rng = np.random.default_rng(seed)
    v   = rng.random(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _make_job(job_id: int = 1, title: str = "AI Strategy Intern",
              description: str = "Drive AI roadmap and digital transformation strategy.") -> dict:
    return {"id": job_id, "title": title, "description": description}


# ---------------------------------------------------------------------------
# 1. sim_to_score — boundary and monotonicity (no numpy needed)
# ---------------------------------------------------------------------------

class TestSimToScore(unittest.TestCase):

    def _score(self, sim, **kw):
        from core.semantic_matcher import sim_to_score
        return sim_to_score(sim, **kw)

    def test_at_floor_returns_zero(self):
        self.assertEqual(self._score(0.30), 0)

    def test_below_floor_clamped_to_zero(self):
        self.assertEqual(self._score(0.0),  0)
        self.assertEqual(self._score(-1.0), 0)
        self.assertEqual(self._score(0.10), 0)

    def test_at_ceil_returns_25(self):
        self.assertEqual(self._score(0.82), 25)

    def test_above_ceil_clamped_to_25(self):
        self.assertEqual(self._score(0.90), 25)
        self.assertEqual(self._score(1.00), 25)

    def test_midpoint_near_12(self):
        # midpoint of [0.30, 0.82] = 0.56 → ~12–13
        mid = self._score(0.56)
        self.assertGreaterEqual(mid, 11)
        self.assertLessEqual(mid, 14)

    def test_monotonically_non_decreasing(self):
        scores = [self._score(s / 100) for s in range(0, 101)]
        for i in range(len(scores) - 1):
            self.assertLessEqual(scores[i], scores[i + 1],
                                 msg=f"Not monotonic at sim={i/100:.2f}")

    def test_returns_int(self):
        self.assertIsInstance(self._score(0.55), int)

    def test_output_range(self):
        for sim in [0.0, 0.3, 0.56, 0.82, 1.0]:
            score = self._score(sim)
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 25)

    # Issue #6 — custom floor/ceil
    def test_custom_floor_ceil(self):
        # With floor=0.50, ceil=0.90 → sim=0.70 is midpoint → ~12-13
        score = self._score(0.70, floor=0.50, ceil=0.90)
        self.assertGreaterEqual(score, 11)
        self.assertLessEqual(score, 14)

    def test_custom_floor_returns_zero_at_floor(self):
        self.assertEqual(self._score(0.50, floor=0.50, ceil=0.90), 0)

    def test_custom_ceil_returns_25_at_ceil(self):
        self.assertEqual(self._score(0.90, floor=0.50, ceil=0.90), 25)


# ---------------------------------------------------------------------------
# 2. cosine_similarity — unit-vector dot product
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestCosineSimilarity(unittest.TestCase):

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    def test_identical_vectors_return_one(self):
        from core.semantic_matcher import cosine_similarity
        v = _unit_vec()
        self.assertAlmostEqual(cosine_similarity(v, v), 1.0, places=5)

    def test_orthogonal_vectors_return_zero(self):
        from core.semantic_matcher import cosine_similarity
        a = np.zeros(384, dtype=np.float32)
        b = np.zeros(384, dtype=np.float32)
        a[0] = 1.0
        b[1] = 1.0
        self.assertAlmostEqual(cosine_similarity(a, b), 0.0, places=5)

    def test_similar_vectors_higher_than_dissimilar(self):
        from core.semantic_matcher import cosine_similarity
        base   = _unit_vec(seed=0)
        close  = base + _unit_vec(seed=1) * 0.1
        close /= np.linalg.norm(close)
        far    = _unit_vec(seed=42)
        self.assertGreater(cosine_similarity(base, close), cosine_similarity(base, far))

    def test_returns_float(self):
        from core.semantic_matcher import cosine_similarity
        result = cosine_similarity(_unit_vec(seed=1), _unit_vec(seed=2))
        self.assertIsInstance(result, float)


# ---------------------------------------------------------------------------
# 3. _extract_resume_phrases — profile parsing (no numpy needed)
# ---------------------------------------------------------------------------

class TestExtractResumePhrases(unittest.TestCase):

    def test_includes_skill_values(self):
        from core.semantic_matcher import _extract_resume_phrases
        profile  = _make_profile()
        phrases  = _extract_resume_phrases(profile)
        self.assertIn("AI Strategy", phrases)
        self.assertIn("Digital Transformation", phrases)

    def test_includes_value_proposition(self):
        from core.semantic_matcher import _extract_resume_phrases
        phrases = _extract_resume_phrases(_make_profile())
        self.assertTrue(any("MBA candidate" in p for p in phrases))

    def test_includes_coursework(self):
        from core.semantic_matcher import _extract_resume_phrases
        phrases = _extract_resume_phrases(_make_profile())
        self.assertIn("AI Strategy", phrases)

    def test_includes_thesis(self):
        from core.semantic_matcher import _extract_resume_phrases
        phrases = _extract_resume_phrases(_make_profile())
        self.assertTrue(any("AI-powered" in p for p in phrases))

    def test_includes_experience_bullets(self):
        from core.semantic_matcher import _extract_resume_phrases
        phrases = _extract_resume_phrases(_make_profile())
        self.assertTrue(any("roadmap" in p for p in phrases))

    def test_no_fill_in_placeholders(self):
        from core.semantic_matcher import _extract_resume_phrases
        profile = _make_profile()
        profile["skills"]["bad"] = ["FILL_IN some skill", "Real Skill"]
        phrases = _extract_resume_phrases(profile)
        self.assertFalse(any("FILL_IN" in p for p in phrases))

    def test_returns_non_empty_list(self):
        from core.semantic_matcher import _extract_resume_phrases
        phrases = _extract_resume_phrases(_make_profile())
        self.assertGreater(len(phrases), 3)

    def test_all_phrases_are_non_empty_strings(self):
        from core.semantic_matcher import _extract_resume_phrases
        for p in _extract_resume_phrases(_make_profile()):
            self.assertIsInstance(p, str)
            self.assertTrue(p.strip())


# ---------------------------------------------------------------------------
# 4. get_resume_embedding — disk caching (Issue #3: SHA256 key)
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestGetResumeEmbedding(unittest.TestCase):

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    @patch("core.semantic_matcher._embed")
    def test_saves_to_disk_on_first_call(self, mock_embed):
        import tempfile
        from pathlib import Path
        from core import semantic_matcher

        mock_embed.return_value = np.stack([_unit_vec(seed=i) for i in range(5)])

        with tempfile.TemporaryDirectory() as tmp:
            def fake_path(content_hash):
                return Path(tmp) / f"resume_{content_hash}.npy"

            with patch.object(semantic_matcher, "_resume_disk_path", fake_path):
                profile      = _make_profile()
                emb          = semantic_matcher.get_resume_embedding(profile)
                expected_hash = semantic_matcher._profile_content_hash(profile)
                saved_path   = fake_path(expected_hash)
                self.assertTrue(saved_path.exists())
                loaded = np.load(str(saved_path))
                np.testing.assert_allclose(emb, loaded, rtol=1e-5)

    @patch("core.semantic_matcher._embed")
    def test_loads_from_disk_on_second_call(self, mock_embed):
        import tempfile
        from pathlib import Path
        from core import semantic_matcher

        mock_embed.return_value = np.stack([_unit_vec(seed=i) for i in range(5)])

        with tempfile.TemporaryDirectory() as tmp:
            def fake_path(content_hash):
                return Path(tmp) / f"resume_{content_hash}.npy"

            with patch.object(semantic_matcher, "_resume_disk_path", fake_path):
                profile           = _make_profile()
                emb1              = semantic_matcher.get_resume_embedding(profile)
                embed_call_count  = mock_embed.call_count
                emb2              = semantic_matcher.get_resume_embedding(profile)
                self.assertEqual(mock_embed.call_count, embed_call_count)
                np.testing.assert_allclose(emb1, emb2, rtol=1e-5)

    @patch("core.semantic_matcher._embed")
    def test_result_is_unit_vector(self, mock_embed):
        import tempfile
        from pathlib import Path
        from core import semantic_matcher

        mock_embed.return_value = np.stack([_unit_vec(seed=i) for i in range(5)])

        with tempfile.TemporaryDirectory() as tmp:
            def fake_path(content_hash):
                return Path(tmp) / f"resume_{content_hash}.npy"

            with patch.object(semantic_matcher, "_resume_disk_path", fake_path):
                emb  = semantic_matcher.get_resume_embedding(_make_profile())
                norm = np.linalg.norm(emb)
                self.assertAlmostEqual(norm, 1.0, places=4)


# ---------------------------------------------------------------------------
# 5. Profile content hash — Issue #3 invalidation correctness
# ---------------------------------------------------------------------------

class TestResumeContentHash(unittest.TestCase):

    def test_same_profile_produces_same_hash(self):
        from core.semantic_matcher import _profile_content_hash
        h1 = _profile_content_hash(_make_profile("1.1"))
        h2 = _profile_content_hash(_make_profile("1.1"))
        self.assertEqual(h1, h2)

    def test_different_content_produces_different_hash(self):
        from core.semantic_matcher import _profile_content_hash
        p1 = _make_profile("1.1")
        p2 = _make_profile("1.1")
        p2["skills"]["extra"] = ["New Skill"]
        self.assertNotEqual(_profile_content_hash(p1), _profile_content_hash(p2))

    def test_same_content_different_version_different_hash(self):
        from core.semantic_matcher import _profile_content_hash
        h_v1 = _profile_content_hash(_make_profile("1.0"))
        h_v2 = _profile_content_hash(_make_profile("1.1"))
        self.assertNotEqual(h_v1, h_v2)

    def test_hash_is_16_hex_chars(self):
        from core.semantic_matcher import _profile_content_hash
        h = _profile_content_hash(_make_profile())
        self.assertEqual(len(h), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))


# ---------------------------------------------------------------------------
# 6. get_jd_embedding — three-level cache (with persist parameter)
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestGetJdEmbedding(unittest.TestCase):

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    @patch("core.semantic_matcher._embed")
    def test_computes_and_returns_array(self, mock_embed):
        from core import semantic_matcher
        expected = _unit_vec(seed=7)
        mock_embed.return_value = expected.reshape(1, -1)

        job = {"id": None, "title": "PM", "description": "Own the roadmap."}
        emb = semantic_matcher.get_jd_embedding(job, "1.1")
        mock_embed.assert_called_once()
        self.assertEqual(emb.shape, (384,))

    @patch("core.semantic_matcher._embed")
    def test_in_memory_cache_prevents_second_embed(self, mock_embed):
        from core import semantic_matcher
        mock_embed.return_value = _unit_vec(seed=5).reshape(1, -1)

        job = {"id": None, "title": "Strat", "description": "AI roadmap."}
        semantic_matcher.get_jd_embedding(job, "1.1")
        semantic_matcher.get_jd_embedding(job, "1.1")
        self.assertEqual(mock_embed.call_count, 1)

    @patch("core.semantic_matcher._embed")
    def test_different_profile_versions_not_shared(self, mock_embed):
        from core import semantic_matcher
        mock_embed.side_effect = [
            _unit_vec(seed=1).reshape(1, -1),
            _unit_vec(seed=2).reshape(1, -1),
        ]
        job = {"id": None, "title": "Strat", "description": "AI roadmap."}
        e1 = semantic_matcher.get_jd_embedding(job, "1.0")
        e2 = semantic_matcher.get_jd_embedding(job, "1.1")
        self.assertFalse(np.allclose(e1, e2))
        self.assertEqual(mock_embed.call_count, 2)

    @patch("core.semantic_matcher._embed")
    def test_db_cache_used_on_second_call_with_job_id(self, mock_embed):
        from core import semantic_matcher

        emb_vec = _unit_vec(seed=9)
        mock_embed.return_value = emb_vec.reshape(1, -1)

        with contextlib.closing(sqlite3.connect(":memory:")) as conn:
            conn.execute(
                """CREATE TABLE jd_embeddings (
                    id INTEGER PRIMARY KEY, job_id INTEGER, profile_version TEXT,
                    model_name TEXT, embedding BLOB, computed_at TEXT,
                    UNIQUE(job_id, profile_version)
                )"""
            )
            conn.row_factory = sqlite3.Row

            def fake_get(job_id, version):
                row = conn.execute(
                    "SELECT embedding FROM jd_embeddings WHERE job_id=? AND profile_version=?",
                    (job_id, version)
                ).fetchone()
                return bytes(row["embedding"]) if row else None

            def fake_upsert(job_id, profile_version, model_name, embedding_bytes):
                conn.execute(
                    """INSERT INTO jd_embeddings (job_id, profile_version, model_name, embedding)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(job_id, profile_version) DO UPDATE SET embedding=excluded.embedding""",
                    (job_id, profile_version, model_name, embedding_bytes)
                )

            job = {"id": 42, "title": "AI PM", "description": "Build AI products."}
            with patch("core.database.get_jd_embedding",    fake_get), \
                 patch("core.database.upsert_jd_embedding", fake_upsert):
                semantic_matcher.get_jd_embedding(job, "1.1")      # compute + persist
                semantic_matcher.clear_cache()                       # flush in-memory
                semantic_matcher.get_jd_embedding(job, "1.1")      # should hit DB
            self.assertEqual(mock_embed.call_count, 1)

    @patch("core.semantic_matcher._embed")
    def test_persist_false_skips_db_upsert(self, mock_embed):
        from core import semantic_matcher
        mock_embed.return_value = _unit_vec(seed=3).reshape(1, -1)

        with patch("core.database.upsert_jd_embedding") as mock_upsert:
            job = {"id": 99, "title": "Test", "description": "Test desc."}
            semantic_matcher.get_jd_embedding(job, "1.1", persist=False)
            mock_upsert.assert_not_called()


# ---------------------------------------------------------------------------
# 7. flush_jd_to_db — selective persistence (Issue #2)
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestFlushJdToDb(unittest.TestCase):

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    def test_flush_returns_false_when_not_cached(self):
        from core.semantic_matcher import flush_jd_to_db
        self.assertFalse(flush_jd_to_db(999, "1.1"))

    def test_flush_returns_false_for_none_job_id(self):
        from core.semantic_matcher import flush_jd_to_db
        self.assertFalse(flush_jd_to_db(None, "1.1"))

    def test_flush_persists_cached_embedding(self):
        from core import semantic_matcher
        from core.semantic_matcher import flush_jd_to_db
        # Manually seed in-memory cache
        emb = _unit_vec(seed=5)
        semantic_matcher._jd_mem_cache[(77, "1.1")] = emb

        with patch("core.database.upsert_jd_embedding") as mock_upsert:
            result = flush_jd_to_db(77, "1.1")
            self.assertTrue(result)
            mock_upsert.assert_called_once()
            call_kwargs = mock_upsert.call_args
            self.assertEqual(call_kwargs.kwargs["job_id"], 77)
            self.assertEqual(call_kwargs.kwargs["profile_version"], "1.1")

    def test_flush_embedding_bytes_match_original(self):
        from core import semantic_matcher
        from core.semantic_matcher import flush_jd_to_db
        emb = _unit_vec(seed=8)
        semantic_matcher._jd_mem_cache[(88, "1.1")] = emb

        captured = {}
        def capture_upsert(**kwargs):
            captured.update(kwargs)

        with patch("core.database.upsert_jd_embedding", side_effect=capture_upsert):
            flush_jd_to_db(88, "1.1")

        stored = np.frombuffer(captured["embedding_bytes"], dtype=np.float32)
        np.testing.assert_allclose(emb, stored, rtol=1e-5)


# ---------------------------------------------------------------------------
# 8. compute_semantic_match — end-to-end (model mocked)
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestComputeSemanticMatch(unittest.TestCase):

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    @patch("core.semantic_matcher.get_resume_embedding")
    @patch("core.semantic_matcher.get_jd_embedding")
    def test_high_similarity_gives_high_score(self, mock_jd, mock_resume):
        from core.semantic_matcher import compute_semantic_match
        v = _unit_vec(seed=0)
        mock_resume.return_value = v
        mock_jd.return_value     = v
        score, sim, note = compute_semantic_match(_make_job(), _make_profile())
        self.assertEqual(score, 25)
        self.assertAlmostEqual(sim, 1.0, places=3)

    @patch("core.semantic_matcher.get_resume_embedding")
    @patch("core.semantic_matcher.get_jd_embedding")
    def test_orthogonal_vectors_give_zero_score(self, mock_jd, mock_resume):
        from core.semantic_matcher import compute_semantic_match
        a = np.zeros(384, dtype=np.float32); a[0] = 1.0
        b = np.zeros(384, dtype=np.float32); b[1] = 1.0
        mock_resume.return_value = a
        mock_jd.return_value     = b
        score, sim, note = compute_semantic_match(_make_job(), _make_profile())
        self.assertEqual(score, 0)
        self.assertAlmostEqual(sim, 0.0, places=3)

    @patch("core.semantic_matcher.get_resume_embedding")
    @patch("core.semantic_matcher.get_jd_embedding")
    def test_returns_tuple_of_correct_types(self, mock_jd, mock_resume):
        from core.semantic_matcher import compute_semantic_match
        v = _unit_vec()
        mock_resume.return_value = v
        mock_jd.return_value     = v
        score, sim, note = compute_semantic_match(_make_job(), _make_profile())
        self.assertIsInstance(score, int)
        self.assertIsInstance(sim,   float)
        self.assertIsInstance(note,  str)

    def test_graceful_fallback_when_model_unavailable(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()
        with patch("core.semantic_matcher._load_model", return_value=None):
            score, sim, note = semantic_matcher.compute_semantic_match(
                _make_job(), _make_profile()
            )
        self.assertEqual(score, 0)
        self.assertEqual(sim,   0.0)
        self.assertEqual(note,  "sem_unavailable")

    @patch("core.semantic_matcher.get_resume_embedding")
    @patch("core.semantic_matcher.get_jd_embedding")
    def test_note_contains_sim_value(self, mock_jd, mock_resume):
        from core.semantic_matcher import compute_semantic_match
        v = _unit_vec()
        mock_resume.return_value = v
        mock_jd.return_value     = v
        _, _, note = compute_semantic_match(_make_job(), _make_profile())
        self.assertIn("sem_sim=", note)

    @patch("core.semantic_matcher.get_resume_embedding")
    @patch("core.semantic_matcher.get_jd_embedding")
    def test_score_in_valid_range(self, mock_jd, mock_resume):
        from core.semantic_matcher import compute_semantic_match
        mock_resume.return_value = _unit_vec(seed=1)
        mock_jd.return_value     = _unit_vec(seed=2)
        score, _, _ = compute_semantic_match(_make_job(), _make_profile())
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 25)

    @patch("core.semantic_matcher.get_resume_embedding")
    @patch("core.semantic_matcher.get_jd_embedding")
    def test_persist_false_forwarded_to_get_jd_embedding(self, mock_jd, mock_resume):
        from core.semantic_matcher import compute_semantic_match
        mock_resume.return_value = _unit_vec()
        mock_jd.return_value     = _unit_vec()
        compute_semantic_match(_make_job(), _make_profile(), persist=False)
        _, call_kwargs = mock_jd.call_args
        self.assertFalse(call_kwargs.get("persist", True))


# ---------------------------------------------------------------------------
# 8b. compute_semantic_match — exception fallback regression (Blocker 2 fix)
# ---------------------------------------------------------------------------
# These tests do NOT require numpy: they exercise the exception handling path
# before any numpy call is made, so they run in all environments.

class TestSemanticMatchExceptionFallback(unittest.TestCase):
    """
    Prove that compute_semantic_match() returns (0, 0.0, 'sem_unavailable')
    for every exception type that can escape from the semantic layer.

    Before the fix: only RuntimeError was caught. ValueError (empty profile,
    corrupt .npy file) and OSError (corrupt disk cache) silently escaped to
    the caller, causing every job in the run to be marked as an error.
    """

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    def test_value_error_from_resume_returns_sem_unavailable(self):
        from core import semantic_matcher
        with patch("core.semantic_matcher.get_resume_embedding",
                   side_effect=ValueError("candidate_profile.json has no embeddable skill phrases.")):
            score, sim, note = semantic_matcher.compute_semantic_match(
                {"id": 1, "title": "Test", "description": "Test JD"},
                {"metadata": {"profile_version": "1.1"}},
                persist=False,
            )
        self.assertEqual(note, "sem_unavailable")
        self.assertEqual(score, 0)
        self.assertEqual(sim, 0.0)

    def test_os_error_from_corrupt_resume_file_returns_sem_unavailable(self):
        from core import semantic_matcher
        with patch("core.semantic_matcher.get_resume_embedding",
                   side_effect=OSError("corrupt file")):
            score, sim, note = semantic_matcher.compute_semantic_match(
                {"id": 2, "title": "Test", "description": "Test JD"},
                {"metadata": {"profile_version": "1.1"}},
                persist=False,
            )
        self.assertEqual(note, "sem_unavailable")
        self.assertEqual(score, 0)
        self.assertEqual(sim, 0.0)

    def test_runtime_error_path_still_returns_sem_unavailable(self):
        from core import semantic_matcher
        with patch("core.semantic_matcher.get_resume_embedding",
                   side_effect=RuntimeError("sentence-transformers not installed")):
            score, sim, note = semantic_matcher.compute_semantic_match(
                {"id": 3, "title": "Test", "description": "Test JD"},
                {"metadata": {"profile_version": "1.1"}},
                persist=False,
            )
        self.assertEqual(note, "sem_unavailable")
        self.assertEqual(score, 0)
        self.assertEqual(sim, 0.0)


# ---------------------------------------------------------------------------
# 9. Database schema and functions
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestJdEmbeddingsDb(unittest.TestCase):

    def _make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE jd_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                profile_version TEXT NOT NULL,
                model_name TEXT NOT NULL DEFAULT 'all-MiniLM-L6-v2',
                embedding BLOB NOT NULL,
                computed_at DATETIME DEFAULT (DATETIME('now')),
                UNIQUE(job_id, profile_version)
            )
        """)
        return conn

    def _write_and_read(self, conn, job_id, version, emb):
        blob = emb.astype(np.float32).tobytes()
        conn.execute(
            """INSERT OR REPLACE INTO jd_embeddings
               (job_id, profile_version, model_name, embedding)
               VALUES (?, ?, 'all-MiniLM-L6-v2', ?)""",
            (job_id, version, blob)
        )
        row = conn.execute(
            "SELECT embedding FROM jd_embeddings WHERE job_id=? AND profile_version=?",
            (job_id, version)
        ).fetchone()
        return np.frombuffer(bytes(row["embedding"]), dtype=np.float32)

    def test_roundtrip_preserves_values(self):
        conn = self._make_conn()
        emb  = _unit_vec(seed=3)
        out  = self._write_and_read(conn, 1, "1.1", emb)
        np.testing.assert_allclose(emb, out, rtol=1e-5)

    def test_unique_constraint_on_job_profile(self):
        conn = self._make_conn()
        blob = _unit_vec().tobytes()
        conn.execute(
            "INSERT INTO jd_embeddings (job_id, profile_version, embedding) VALUES (1,'1.1',?)",
            (blob,)
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO jd_embeddings (job_id, profile_version, embedding) VALUES (1,'1.1',?)",
                (blob,)
            )

    def test_different_versions_coexist(self):
        conn = self._make_conn()
        blob = _unit_vec().tobytes()
        conn.execute(
            "INSERT INTO jd_embeddings (job_id, profile_version, embedding) VALUES (1,'1.0',?)",
            (blob,)
        )
        conn.execute(
            "INSERT INTO jd_embeddings (job_id, profile_version, embedding) VALUES (1,'1.1',?)",
            (blob,)
        )
        count = conn.execute("SELECT COUNT(*) FROM jd_embeddings WHERE job_id=1").fetchone()[0]
        self.assertEqual(count, 2)

    def test_upsert_replaces_existing_embedding(self):
        conn = self._make_conn()
        v1 = _unit_vec(seed=1)
        v2 = _unit_vec(seed=2)
        self._write_and_read(conn, 1, "1.1", v1)
        conn.execute(
            """INSERT OR REPLACE INTO jd_embeddings (job_id, profile_version, model_name, embedding)
               VALUES (1, '1.1', 'all-MiniLM-L6-v2', ?)""",
            (v2.tobytes(),)
        )
        out = np.frombuffer(
            bytes(conn.execute(
                "SELECT embedding FROM jd_embeddings WHERE job_id=1 AND profile_version='1.1'"
            ).fetchone()["embedding"]),
            dtype=np.float32
        )
        np.testing.assert_allclose(v2, out, rtol=1e-5)

    def test_blob_size_is_384_floats(self):
        conn  = self._make_conn()
        emb   = _unit_vec()
        blob  = emb.tobytes()
        self.assertEqual(len(blob), 384 * 4)   # 384 float32 = 1536 bytes


# ---------------------------------------------------------------------------
# 10. scorer._score_skill_match — Constraint 3 gate and hybrid (Issues #1 & #4)
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestConstraint3Gate(unittest.TestCase):
    """Empty matched_categories must force score to 0 (Constraint 3)."""

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    @patch("core.profile_loader.load")
    def test_empty_categories_returns_zero_score(self, mock_profile):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match
        score, detail, raw_sim, note = _score_skill_match(
            "Completely unrelated XYZ123", "No technology terms here.", job_id=None
        )
        self.assertEqual(score, 0)
        self.assertEqual(raw_sim, 0.0)

    @patch("core.profile_loader.load")
    def test_empty_categories_sets_kw_gate_note(self, mock_profile):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match
        _, _, _, note = _score_skill_match(
            "Completely unrelated XYZ123", "No technology terms here.", job_id=None
        )
        self.assertIn("kw_gate", note)

    @patch("core.profile_loader.load")
    def test_empty_categories_returns_empty_matched_dict(self, mock_profile):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match
        _, detail, _, _ = _score_skill_match(
            "Completely unrelated XYZ123", "No technology terms here.", job_id=None
        )
        self.assertEqual(detail["matched_categories"], {})

    @patch("core.semantic_matcher.compute_semantic_match")
    @patch("core.profile_loader.load")
    def test_semantic_not_called_when_categories_empty(self, mock_profile, mock_sem):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match
        _score_skill_match(
            "Completely unrelated XYZ123", "No technology terms here.", job_id=None
        )
        mock_sem.assert_not_called()


@_skip_no_numpy
class TestHybridScore(unittest.TestCase):
    """40% keyword + 60% semantic when both are available (Issue #1)."""

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    @patch("core.semantic_matcher.compute_semantic_match", return_value=(20, 0.75, "sem_sim=0.750 → 20/25"))
    @patch("core.profile_loader.load")
    def test_hybrid_formula_applied(self, mock_profile, mock_sem):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match, _keyword_score
        from core.skill_loader import match_keywords
        score, detail, raw_sim, note = _score_skill_match(
            "AI Strategy Intern", "AI strategy and digital transformation.", job_id=None
        )
        cats = match_keywords("AI Strategy Intern AI strategy and digital transformation.")
        if cats:
            kw    = _keyword_score(cats)
            expected = min(25, max(0, round(0.4 * kw + 0.6 * 20)))
            self.assertEqual(score, expected)
        # At minimum, score should be > 0 if keywords matched
        self.assertGreater(score, 0)

    @patch("core.semantic_matcher.compute_semantic_match", return_value=(20, 0.75, "sem_sim=0.750 → 20/25"))
    @patch("core.profile_loader.load")
    def test_hybrid_note_format(self, mock_profile, mock_sem):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match
        _, _, _, note = _score_skill_match(
            "AI Strategy Intern", "AI strategy and digital transformation.", job_id=None
        )
        self.assertIn("hybrid", note)

    @patch("core.semantic_matcher.compute_semantic_match", return_value=(20, 0.75, "sem_sim=0.750 → 20/25"))
    @patch("core.profile_loader.load")
    def test_hybrid_score_within_range(self, mock_profile, mock_sem):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match
        score, _, _, _ = _score_skill_match(
            "AI Strategy Intern", "AI strategy and digital transformation.", job_id=None
        )
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 25)

    @patch("core.semantic_matcher.compute_semantic_match", return_value=(20, 0.75, "sem_sim=0.750 → 20/25"))
    @patch("core.profile_loader.load")
    def test_hybrid_persist_false_passed_to_semantic(self, mock_profile, mock_sem):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match
        _score_skill_match(
            "AI Strategy Intern", "AI strategy and digital transformation.", job_id=None
        )
        # Verify compute_semantic_match was called with persist=False
        _, call_kwargs = mock_sem.call_args
        self.assertFalse(call_kwargs.get("persist", True))


# ---------------------------------------------------------------------------
# 11. Dependency fallback — keyword scoring when model unavailable (Issue #4)
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestDependencyFallback(unittest.TestCase):

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    @patch("core.semantic_matcher.compute_semantic_match", return_value=(0, 0.0, "sem_unavailable"))
    @patch("core.profile_loader.load")
    def test_fallback_to_keyword_when_model_unavailable_and_keywords_match(
        self, mock_profile, mock_sem
    ):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match
        score, _, _, note = _score_skill_match(
            "AI Strategy Intern", "AI strategy and digital transformation.", job_id=None
        )
        # Keywords should match → fallback score > 0
        self.assertGreater(score, 0)
        self.assertIn("kw_fallback", note)

    @patch("core.semantic_matcher.compute_semantic_match", return_value=(0, 0.0, "sem_unavailable"))
    @patch("core.profile_loader.load")
    def test_fallback_raw_sim_is_zero(self, mock_profile, mock_sem):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match
        _, _, raw_sim, _ = _score_skill_match(
            "AI Strategy Intern", "AI strategy and digital transformation.", job_id=None
        )
        self.assertEqual(raw_sim, 0.0)

    @patch("core.semantic_matcher.compute_semantic_match", return_value=(0, 0.0, "sem_unavailable"))
    @patch("core.profile_loader.load")
    def test_fallback_score_zero_when_no_keywords(self, mock_profile, mock_sem):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match
        score, _, _, _ = _score_skill_match(
            "Generic role", "No relevant content.", job_id=None
        )
        self.assertEqual(score, 0)


# ---------------------------------------------------------------------------
# 12. _keyword_score helper (Issue #1 + #4)
# ---------------------------------------------------------------------------

class TestKeywordScore(unittest.TestCase):

    def _score(self, cats):
        from core.scorer import _keyword_score
        return _keyword_score(cats)

    def test_empty_returns_zero(self):
        self.assertEqual(self._score({}), 0)

    def test_one_category_returns_five(self):
        self.assertEqual(self._score({"ai_strategy": ["ai"]}), 5)

    def test_five_categories_returns_25(self):
        cats = {f"cat_{i}": ["kw"] for i in range(5)}
        self.assertEqual(self._score(cats), 25)

    def test_more_than_five_capped_at_25(self):
        cats = {f"cat_{i}": ["kw"] for i in range(10)}
        self.assertEqual(self._score(cats), 25)

    def test_returns_int(self):
        self.assertIsInstance(self._score({"cat": ["kw"]}), int)


# ---------------------------------------------------------------------------
# 13. scorer integration — ScoreResult carries semantic_similarity
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestScorerIntegration(unittest.TestCase):

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    @patch("core.semantic_matcher.compute_semantic_match", return_value=(18, 0.72, "sem_sim=0.720 → 18/25"))
    @patch("core.profile_loader.load")
    def test_skill_match_returns_hybrid_score(self, mock_profile, mock_sem):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match, _keyword_score
        from core.skill_loader import match_keywords
        score, detail, raw_sim, note = _score_skill_match(
            "AI Strategy Intern", "Drive AI roadmap.", job_id=None
        )
        cats = match_keywords("AI Strategy Intern Drive AI roadmap.")
        if cats:
            kw_s     = _keyword_score(cats)
            expected = min(25, max(0, round(0.4 * kw_s + 0.6 * 18)))
            self.assertEqual(score, expected)
            self.assertIn("hybrid", note)
        else:
            self.assertEqual(score, 0)
        self.assertAlmostEqual(raw_sim, 0.72)

    @patch("core.semantic_matcher.compute_semantic_match", return_value=(12, 0.55, "sem_sim=0.550 → 12/25"))
    @patch("core.profile_loader.load")
    def test_matched_categories_still_populated(self, mock_profile, mock_sem):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match
        _, detail, _, _ = _score_skill_match("AI PM", "AI strategy and digital transformation.", job_id=None)
        self.assertIn("matched_categories", detail)
        self.assertIsInstance(detail["matched_categories"], dict)

    @patch("core.semantic_matcher.compute_semantic_match", return_value=(0, 0.0, "sem_unavailable"))
    @patch("core.profile_loader.load")
    def test_fallback_score_zero_when_model_unavailable_and_no_keywords(self, mock_profile, mock_sem):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match
        score, _, raw_sim, _ = _score_skill_match("Generic role", "No relevant content.", job_id=None)
        self.assertEqual(score, 0)
        self.assertEqual(raw_sim, 0.0)

    @patch("core.semantic_matcher.compute_semantic_match", return_value=(20, 0.75, "sem_sim=0.750 → 20/25"))
    @patch("core.profile_loader.load")
    def test_score_result_carries_semantic_similarity(self, mock_profile, mock_sem):
        mock_profile.return_value = _make_profile()
        from core.scorer import score_job
        job = {
            "id": None, "title": "AI Strategy Intern",
            "company": "Google", "location": "London",
            "description": "Drive AI roadmap and digital transformation strategy.",
            "role_category": "ai_strategy",
        }
        result = score_job(job)
        self.assertAlmostEqual(result.semantic_similarity, 0.75)

    @patch("core.semantic_matcher.compute_semantic_match", return_value=(20, 0.75, "sem_sim=0.750 → 20/25"))
    @patch("core.profile_loader.load")
    def test_skill_match_score_capped_at_25(self, mock_profile, mock_sem):
        mock_profile.return_value = _make_profile()
        from core.scorer import _score_skill_match
        score, _, _, _ = _score_skill_match("X", "Y", job_id=None)
        self.assertLessEqual(score, 25)


# ---------------------------------------------------------------------------
# 14. warm_embedding_model — Issue #5
# ---------------------------------------------------------------------------

class TestWarmEmbeddingModel(unittest.TestCase):

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    def test_returns_false_when_model_unavailable(self):
        from core import semantic_matcher
        with patch.object(semantic_matcher, "_load_model", return_value=None):
            result = semantic_matcher.warm_embedding_model()
        self.assertFalse(result)

    def test_returns_true_when_model_available(self):
        from core import semantic_matcher
        mock_model = MagicMock()
        # Patch _NUMPY_AVAILABLE to True so the early-exit guard is bypassed;
        # also patch _embed so no real encoding happens.
        with patch.object(semantic_matcher, "_NUMPY_AVAILABLE", True), \
             patch.object(semantic_matcher, "_load_model", return_value=mock_model), \
             patch.object(semantic_matcher, "_embed", return_value=MagicMock()):
            result = semantic_matcher.warm_embedding_model()
        self.assertTrue(result)

    def test_returns_false_when_numpy_unavailable(self):
        import core.semantic_matcher as sm
        orig = sm._NUMPY_AVAILABLE
        sm._NUMPY_AVAILABLE = False
        try:
            result = sm.warm_embedding_model()
            self.assertFalse(result)
        finally:
            sm._NUMPY_AVAILABLE = orig


# ---------------------------------------------------------------------------
# 15. calibrate_sim_bounds — Issue #6 percentile calibration
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestCalibrateSimBounds(unittest.TestCase):

    def test_returns_defaults_when_insufficient_data(self):
        from core.semantic_matcher import calibrate_sim_bounds, _SIM_FLOOR, _SIM_CEIL
        result = calibrate_sim_bounds([0.5] * 10)   # < MIN_CALIBRATION_SAMPLES
        self.assertEqual(result, (_SIM_FLOOR, _SIM_CEIL))

    def test_computes_p10_p90(self):
        from core.semantic_matcher import calibrate_sim_bounds
        sims  = [i / 100 for i in range(20, 91)]   # 71 values: 0.20..0.90
        floor, ceil = calibrate_sim_bounds(sims)
        self.assertAlmostEqual(floor, 0.27, delta=0.03)
        self.assertAlmostEqual(ceil,  0.83, delta=0.03)

    def test_returns_defaults_for_degenerate_distribution(self):
        from core.semantic_matcher import calibrate_sim_bounds, _SIM_FLOOR, _SIM_CEIL
        # All-same values → P90 - P10 < 0.05 → fallback
        sims = [0.55] * 50
        result = calibrate_sim_bounds(sims)
        self.assertEqual(result, (_SIM_FLOOR, _SIM_CEIL))

    def test_floor_less_than_ceil(self):
        from core.semantic_matcher import calibrate_sim_bounds
        sims = [i / 100 for i in range(0, 100)]
        floor, ceil = calibrate_sim_bounds(sims)
        self.assertLess(floor, ceil)

    def test_empty_list_returns_defaults(self):
        from core.semantic_matcher import calibrate_sim_bounds, _SIM_FLOOR, _SIM_CEIL
        result = calibrate_sim_bounds([])
        self.assertEqual(result, (_SIM_FLOOR, _SIM_CEIL))


# ---------------------------------------------------------------------------
# 16. batch_embed_jobs — Issue #7
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestBatchEmbedJobs(unittest.TestCase):

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    @patch("core.semantic_matcher._embed")
    def test_encodes_uncached_jobs_in_one_call(self, mock_embed):
        from core import semantic_matcher
        n = 5
        mock_embed.return_value = np.stack([_unit_vec(seed=i) for i in range(n)])
        jobs = [{"id": i, "title": f"Job {i}", "description": f"Desc {i}"} for i in range(n)]
        count = semantic_matcher.batch_embed_jobs(jobs, "1.1", persist=False)
        self.assertEqual(count, n)
        mock_embed.assert_called_once()   # single forward pass

    @patch("core.semantic_matcher._embed")
    def test_skips_already_cached_jobs(self, mock_embed):
        from core import semantic_matcher
        jobs = [{"id": i, "title": f"Job {i}", "description": f"D {i}"} for i in range(3)]
        # Seed cache for job 0
        semantic_matcher._jd_mem_cache[(0, "1.1")] = _unit_vec(seed=0)
        mock_embed.return_value = np.stack([_unit_vec(seed=i) for i in range(2)])
        count = semantic_matcher.batch_embed_jobs(jobs, "1.1", persist=False)
        self.assertEqual(count, 2)   # only 2 uncached jobs encoded

    @patch("core.semantic_matcher._embed")
    def test_results_populate_in_memory_cache(self, mock_embed):
        from core import semantic_matcher
        mock_embed.return_value = _unit_vec(seed=7).reshape(1, -1)
        jobs = [{"id": 55, "title": "X", "description": "Y"}]
        semantic_matcher.batch_embed_jobs(jobs, "1.1", persist=False)
        self.assertIn((55, "1.1"), semantic_matcher._jd_mem_cache)

    @patch("core.semantic_matcher._embed")
    def test_returns_zero_when_all_cached(self, mock_embed):
        from core import semantic_matcher
        jobs = [{"id": 1, "title": "T", "description": "D"}]
        semantic_matcher._jd_mem_cache[(1, "1.1")] = _unit_vec()
        count = semantic_matcher.batch_embed_jobs(jobs, "1.1", persist=False)
        self.assertEqual(count, 0)
        mock_embed.assert_not_called()

    @patch("core.semantic_matcher._embed")
    def test_persist_true_calls_upsert_for_each_job(self, mock_embed):
        from core import semantic_matcher
        n = 3
        mock_embed.return_value = np.stack([_unit_vec(seed=i) for i in range(n)])
        jobs = [{"id": i + 10, "title": f"T{i}", "description": f"D{i}"} for i in range(n)]
        with patch("core.database.upsert_jd_embedding") as mock_upsert:
            semantic_matcher.batch_embed_jobs(jobs, "1.1", persist=True)
            self.assertEqual(mock_upsert.call_count, n)


# ---------------------------------------------------------------------------
# 17. Migration safety — Issue #8 PRAGMA-based idempotency
# ---------------------------------------------------------------------------

class TestMigrationIdempotent(unittest.TestCase):

    def _make_base_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT,
                job_board TEXT,
                url TEXT UNIQUE,
                description TEXT,
                posted_date DATE,
                fetched_date DATE DEFAULT (DATE('now')),
                is_expired BOOLEAN DEFAULT 0,
                raw_data TEXT,
                track TEXT,
                language TEXT,
                role_category TEXT,
                is_excluded BOOLEAN DEFAULT 0
            );
            CREATE TABLE scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                role_category TEXT,
                total_score INTEGER,
                role_fit INTEGER,
                skills_match INTEGER,
                location_fit INTEGER,
                seniority_fit INTEGER,
                explanation TEXT,
                matched_categories TEXT,
                scored_at DATETIME DEFAULT (DATETIME('now')),
                prompt_version TEXT,
                dictionary_version TEXT,
                role_dictionary_version TEXT
            );
            CREATE TABLE company_eligibility_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL UNIQUE,
                visa_friendliness_score INTEGER NOT NULL DEFAULT 5,
                english_environment_score INTEGER NOT NULL DEFAULT 5,
                international_student_score INTEGER NOT NULL DEFAULT 5,
                mba_friendliness_score INTEGER NOT NULL DEFAULT 5,
                last_updated DATETIME
            );
        """)
        return conn

    def test_migrate_twice_does_not_raise(self):
        from core.database import _migrate
        conn = self._make_base_db()
        _migrate(conn)   # first run — adds all columns
        _migrate(conn)   # second run — must not raise or alter existing schema

    def test_safe_add_column_idempotent(self):
        from core.database import _safe_add_column
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        _safe_add_column(conn, "t", "new_col", "TEXT")
        _safe_add_column(conn, "t", "new_col", "TEXT")   # second call — no error
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(t)").fetchall()]
        self.assertIn("new_col", cols)

    def test_column_exists_returns_true_for_existing(self):
        from core.database import _column_exists
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        self.assertTrue(_column_exists(conn, "t", "name"))

    def test_column_exists_returns_false_for_missing(self):
        from core.database import _column_exists
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        self.assertFalse(_column_exists(conn, "t", "ghost"))


# ---------------------------------------------------------------------------
# 18. clear_cache and invalidate_resume_cache (updated for Issue #3 API)
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestCacheManagement(unittest.TestCase):

    def test_clear_cache_empties_jd_mem_cache(self):
        from core import semantic_matcher
        semantic_matcher._jd_mem_cache[(1, "1.1")] = _unit_vec()
        semantic_matcher.clear_cache()
        self.assertEqual(len(semantic_matcher._jd_mem_cache), 0)

    def test_clear_cache_resets_model_to_none(self):
        from core import semantic_matcher
        semantic_matcher._model = MagicMock()
        semantic_matcher.clear_cache()
        self.assertIsNone(semantic_matcher._model)

    def test_invalidate_resume_cache_deletes_file(self):
        import tempfile
        from pathlib import Path
        from core import semantic_matcher
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resume_abc123.npy"
            np.save(str(path), _unit_vec())
            self.assertTrue(path.exists())
            with patch.object(semantic_matcher, "_resume_disk_path", return_value=path):
                semantic_matcher.invalidate_resume_cache(_make_profile())
            self.assertFalse(path.exists())

    def test_invalidate_nonexistent_file_is_safe(self):
        import tempfile
        from pathlib import Path
        from core import semantic_matcher
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resume_missing.npy"
            with patch.object(semantic_matcher, "_resume_disk_path", return_value=path):
                semantic_matcher.invalidate_resume_cache(_make_profile())   # no error


# ---------------------------------------------------------------------------
# 19. JD embedding retention — Issue A
# ---------------------------------------------------------------------------

class TestJdEmbeddingRetention(unittest.TestCase):
    """Tests for purge_* functions using an isolated in-memory SQLite database."""

    def _make_jd_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE jd_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                profile_version TEXT NOT NULL,
                model_name TEXT NOT NULL DEFAULT 'all-MiniLM-L6-v2',
                embedding BLOB NOT NULL,
                computed_at DATETIME DEFAULT (DATETIME('now')),
                UNIQUE(job_id, profile_version)
            );
        """)
        return conn

    def _seed_row(self, conn, job_id, profile_version, days_ago=0):
        conn.execute(
            """INSERT INTO jd_embeddings (job_id, profile_version, embedding, computed_at)
               VALUES (?, ?, ?, DATETIME('now', ?))""",
            (job_id, profile_version, b"\x00\x00", f"-{days_ago} days"),
        )
        conn.commit()

    def test_purge_old_profile_version_removes_stale_rows(self):
        from core.database import purge_old_profile_version_embeddings
        conn = self._make_jd_conn()
        self._seed_row(conn, 1, "v1")
        self._seed_row(conn, 2, "v1")
        self._seed_row(conn, 3, "v2")
        with patch("core.database.get_connection", return_value=conn):
            deleted = purge_old_profile_version_embeddings("v2")
        self.assertEqual(deleted, 2)
        remaining = conn.execute("SELECT COUNT(*) FROM jd_embeddings").fetchone()[0]
        self.assertEqual(remaining, 1)

    def test_purge_old_profile_version_keeps_current(self):
        from core.database import purge_old_profile_version_embeddings
        conn = self._make_jd_conn()
        self._seed_row(conn, 1, "v2")
        self._seed_row(conn, 2, "v2")
        with patch("core.database.get_connection", return_value=conn):
            deleted = purge_old_profile_version_embeddings("v2")
        self.assertEqual(deleted, 0)
        remaining = conn.execute("SELECT COUNT(*) FROM jd_embeddings").fetchone()[0]
        self.assertEqual(remaining, 2)

    def test_purge_aged_removes_rows_older_than_max_days(self):
        from core.database import purge_aged_jd_embeddings
        conn = self._make_jd_conn()
        self._seed_row(conn, 1, "v1", days_ago=100)
        self._seed_row(conn, 2, "v1", days_ago=30)
        with patch("core.database.get_connection", return_value=conn):
            deleted = purge_aged_jd_embeddings(max_age_days=90)
        self.assertEqual(deleted, 1)
        remaining = conn.execute("SELECT COUNT(*) FROM jd_embeddings").fetchone()[0]
        self.assertEqual(remaining, 1)

    def test_purge_aged_keeps_recent_rows(self):
        from core.database import purge_aged_jd_embeddings
        conn = self._make_jd_conn()
        self._seed_row(conn, 1, "v1", days_ago=10)
        self._seed_row(conn, 2, "v1", days_ago=50)
        with patch("core.database.get_connection", return_value=conn):
            deleted = purge_aged_jd_embeddings(max_age_days=90)
        self.assertEqual(deleted, 0)

    def test_purge_stale_combines_version_and_age_purge(self):
        from core.database import purge_stale_jd_embeddings
        conn = self._make_jd_conn()
        self._seed_row(conn, 1, "v1", days_ago=5)    # wrong version, fresh
        self._seed_row(conn, 2, "v2", days_ago=100)  # current version, aged out
        self._seed_row(conn, 3, "v2", days_ago=10)   # current version, fresh
        with patch("core.database.get_connection", return_value=conn):
            n_ver, n_age = purge_stale_jd_embeddings("v2", max_age_days=90)
        self.assertEqual(n_ver, 1)
        self.assertEqual(n_age, 1)
        remaining = conn.execute("SELECT COUNT(*) FROM jd_embeddings").fetchone()[0]
        self.assertEqual(remaining, 1)

    def test_purge_stale_returns_zero_when_already_clean(self):
        from core.database import purge_stale_jd_embeddings
        conn = self._make_jd_conn()
        self._seed_row(conn, 1, "v2", days_ago=5)
        with patch("core.database.get_connection", return_value=conn):
            n_ver, n_age = purge_stale_jd_embeddings("v2", max_age_days=90)
        self.assertEqual((n_ver, n_age), (0, 0))


# ---------------------------------------------------------------------------
# 20. Calibration wired into compute_semantic_match — Issue B
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestCalibrationWired(unittest.TestCase):
    """Prove that set_calibration() actually propagates into compute_semantic_match."""

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    def tearDown(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    def test_set_get_calibration_roundtrip(self):
        from core.semantic_matcher import set_calibration, get_calibration
        set_calibration(0.40, 0.80)
        self.assertEqual(get_calibration(), (0.40, 0.80))

    def test_clear_cache_resets_bounds_to_static_defaults(self):
        from core import semantic_matcher
        from core.semantic_matcher import _SIM_FLOOR, _SIM_CEIL
        semantic_matcher.set_calibration(0.40, 0.80)
        semantic_matcher.clear_cache()
        self.assertEqual(semantic_matcher.get_calibration(), (_SIM_FLOOR, _SIM_CEIL))

    @patch("core.semantic_matcher.get_resume_embedding")
    @patch("core.semantic_matcher.get_jd_embedding")
    def test_calibration_changes_score_for_same_similarity(self, mock_jd, mock_resume):
        from core import semantic_matcher
        # Unit vectors with cosine similarity = 0.55
        a = np.zeros(384, dtype=np.float32); a[0] = 1.0
        b = np.zeros(384, dtype=np.float32); b[0] = 0.55; b[1] = float(np.sqrt(1 - 0.55 ** 2))
        mock_resume.return_value = a
        mock_jd.return_value = b

        semantic_matcher.clear_cache()
        score_default, _, _ = semantic_matcher.compute_semantic_match(
            _make_job(), _make_profile(), persist=False
        )
        semantic_matcher.set_calibration(0.40, 0.80)
        score_tight, _, _ = semantic_matcher.compute_semantic_match(
            _make_job(), _make_profile(), persist=False
        )
        # Default [0.30,0.82] → ~12; tight [0.40,0.80] → ~9
        self.assertNotEqual(score_default, score_tight)

    @patch("core.semantic_matcher.get_resume_embedding")
    @patch("core.semantic_matcher.get_jd_embedding")
    def test_score_matches_formula_with_calibrated_bounds(self, mock_jd, mock_resume):
        from core import semantic_matcher
        sim = 0.55
        a = np.zeros(384, dtype=np.float32); a[0] = 1.0
        b = np.zeros(384, dtype=np.float32); b[0] = sim; b[1] = float(np.sqrt(1 - sim ** 2))
        mock_resume.return_value = a
        mock_jd.return_value = b

        floor, ceil = 0.40, 0.80
        semantic_matcher.set_calibration(floor, ceil)
        score, _, _ = semantic_matcher.compute_semantic_match(
            _make_job(), _make_profile(), persist=False
        )
        expected = round((sim - floor) / (ceil - floor) * 25)
        self.assertEqual(score, expected)


# ---------------------------------------------------------------------------
# 21. Resume cache file cleanup — Issue C
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestResumeCacheCleanup(unittest.TestCase):
    """Tests for _cleanup_stale_resume_caches and its integration with get_resume_embedding."""

    def test_cleanup_removes_stale_files_keeps_active(self):
        import tempfile
        from pathlib import Path
        from core import semantic_matcher
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "resume_stale1.npy").write_bytes(b"\x00")
            (tmp_path / "resume_stale2.npy").write_bytes(b"\x00")
            (tmp_path / "resume_active.npy").write_bytes(b"\x00")
            with patch.object(semantic_matcher, "_EMBED_DIR", tmp_path):
                deleted = semantic_matcher._cleanup_stale_resume_caches("active")
            self.assertEqual(deleted, 2)
            self.assertFalse((tmp_path / "resume_stale1.npy").exists())
            self.assertFalse((tmp_path / "resume_stale2.npy").exists())
            self.assertTrue((tmp_path / "resume_active.npy").exists())

    def test_cleanup_returns_zero_when_only_active_file_present(self):
        import tempfile
        from pathlib import Path
        from core import semantic_matcher
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "resume_abc123.npy").write_bytes(b"\x00")
            with patch.object(semantic_matcher, "_EMBED_DIR", tmp_path):
                deleted = semantic_matcher._cleanup_stale_resume_caches("abc123")
            self.assertEqual(deleted, 0)
            self.assertTrue((tmp_path / "resume_abc123.npy").exists())

    def test_cleanup_returns_zero_when_embed_dir_empty(self):
        import tempfile
        from pathlib import Path
        from core import semantic_matcher
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(semantic_matcher, "_EMBED_DIR", tmp_path):
                deleted = semantic_matcher._cleanup_stale_resume_caches("anything")
        self.assertEqual(deleted, 0)

    @patch("core.semantic_matcher._embed")
    def test_get_resume_embedding_triggers_cleanup_of_stale_files(self, mock_embed):
        import tempfile
        from pathlib import Path
        from core import semantic_matcher
        mock_embed.return_value = np.stack([_unit_vec()])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "resume_oldstale.npy").write_bytes(b"\x00")
            with patch.object(semantic_matcher, "_EMBED_DIR", tmp_path), \
                 patch("core.semantic_matcher._extract_resume_phrases", return_value=["test"]):
                semantic_matcher.get_resume_embedding(_make_profile())
        self.assertFalse((tmp_path / "resume_oldstale.npy").exists())


# ---------------------------------------------------------------------------
# 22. Batch embedding verification — Issue D
# ---------------------------------------------------------------------------

@_skip_no_numpy
class TestBatchEmbedVerification(unittest.TestCase):
    """Prove batch_embed_jobs + get_jd_embedding together call _embed exactly once."""

    def setUp(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    def tearDown(self):
        from core import semantic_matcher
        semantic_matcher.clear_cache()

    @patch("core.semantic_matcher._embed")
    def test_batch_then_individual_get_calls_embed_once_total(self, mock_embed):
        from core import semantic_matcher
        n = 5
        mock_embed.return_value = np.stack([_unit_vec(seed=i) for i in range(n)])
        jobs = [{"id": i, "title": f"Job {i}", "description": f"Desc {i}"} for i in range(n)]

        semantic_matcher.batch_embed_jobs(jobs, "v1", persist=False)
        # All jobs are now in _jd_mem_cache; get_jd_embedding should be pure cache hits
        for job in jobs:
            semantic_matcher.get_jd_embedding(job, "v1", persist=False)

        mock_embed.assert_called_once()

    @patch("core.semantic_matcher._embed")
    def test_all_jobs_in_cache_after_batch(self, mock_embed):
        from core import semantic_matcher
        n = 10
        mock_embed.return_value = np.stack([_unit_vec(seed=i) for i in range(n)])
        jobs = [{"id": 100 + i, "title": f"T{i}", "description": f"D{i}"} for i in range(n)]
        semantic_matcher.batch_embed_jobs(jobs, "v1", persist=False)
        for job in jobs:
            self.assertIn((job["id"], "v1"), semantic_matcher._jd_mem_cache)

    @patch("core.semantic_matcher._embed")
    def test_embed_call_count_does_not_grow_with_individual_gets(self, mock_embed):
        from core import semantic_matcher
        n = 3
        mock_embed.return_value = np.stack([_unit_vec(seed=i) for i in range(n)])
        jobs = [{"id": i + 200, "title": f"T{i}", "description": f"D{i}"} for i in range(n)]

        semantic_matcher.batch_embed_jobs(jobs, "v1", persist=False)
        count_after_batch = mock_embed.call_count   # should be 1

        for job in jobs:
            semantic_matcher.get_jd_embedding(job, "v1", persist=False)

        self.assertEqual(mock_embed.call_count, count_after_batch)   # unchanged


if __name__ == "__main__":
    unittest.main(verbosity=2)
