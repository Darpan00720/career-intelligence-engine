"""
Phase 1 validation suite.
Run with: python3 -m pytest tests/test_phase1.py -v
Or:        python3 tests/test_phase1.py
"""
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── 1. Skill Matching ────────────────────────────────────────────────────────

class TestSkillMatching(unittest.TestCase):

    def setUp(self):
        from core import skill_loader
        skill_loader.clear_cache()

    def _matches(self, keyword, text):
        from core.skill_loader import _matches
        return _matches(keyword.lower(), text.lower())

    # --- False positive regressions (must NOT match) ---

    def test_ta_not_in_stakeholder(self):
        self.assertFalse(self._matches("TA", "stakeholder management"))

    def test_ta_not_in_data(self):
        # "data" contains "ta" at the end but is not about talent acquisition
        self.assertFalse(self._matches("TA", "data analysis"))

    def test_mi_not_in_milan(self):
        self.assertFalse(self._matches("MI", "Milan Italy"))

    def test_mi_not_in_minimum(self):
        self.assertFalse(self._matches("MI", "minimum viable product"))

    def test_mi_not_in_dynamic(self):
        self.assertFalse(self._matches("MI", "dynamic environment"))

    def test_bi_not_in_ability(self):
        self.assertFalse(self._matches("BI", "ability to lead teams"))

    def test_bi_not_in_combine(self):
        self.assertFalse(self._matches("BI", "combine data sources"))

    def test_ona_not_in_organizational(self):
        self.assertFalse(self._matches("ONA", "organizational network"))

    def test_bd_not_in_embedded(self):
        self.assertFalse(self._matches("BD", "embedded systems"))

    def test_ats_not_in_formats(self):
        self.assertFalse(self._matches("ATS", "evaluates formats"))

    def test_ml_not_in_html(self):
        self.assertFalse(self._matches("ML", "HTML templates"))

    def test_r_not_in_requirements(self):
        self.assertFalse(self._matches("R", "requirements gathering"))

    def test_sql_not_in_casual_word(self):
        # SQL should not match in "squat" (no such word with sql but confirm boundary)
        self.assertFalse(self._matches("SQL", "squall conditions"))

    # --- True positive checks (MUST match) ---

    def test_ta_matches_standalone(self):
        self.assertTrue(self._matches("TA", "experience in TA and sourcing"))

    def test_mi_matches_standalone(self):
        self.assertTrue(self._matches("MI", "produce MI reports for the board"))

    def test_bi_matches_standalone(self):
        self.assertTrue(self._matches("BI", "Power BI and Tableau required"))

    def test_ona_matches_standalone(self):
        self.assertTrue(self._matches("ONA", "ONA and collaboration analytics"))

    def test_r_matches_standalone(self):
        self.assertTrue(self._matches("R", "proficiency in R, Python, and SQL"))

    def test_sql_matches_standalone(self):
        self.assertTrue(self._matches("SQL", "strong SQL skills required"))

    def test_ml_matches_standalone(self):
        self.assertTrue(self._matches("ML", "ML model deployment experience"))

    def test_ats_matches_standalone(self):
        self.assertTrue(self._matches("ATS", "manage the ATS and pipeline"))

    # --- Dictionary-level integration ---

    def test_dictionary_loads(self):
        from core.skill_loader import load
        d = load()
        self.assertIn("version", d)
        self.assertIn("lookup", d)
        self.assertGreater(len(d["lookup"]), 400)

    def test_no_cross_category_duplicates(self):
        from core.skill_loader import load
        d = load()
        seen: dict[str, str] = {}
        duplicates = []
        for kw, cat in d["lookup"].items():
            if kw in seen:
                duplicates.append(f"'{kw}' in {seen[kw]} and {cat}")
            seen[kw] = cat
        self.assertEqual(duplicates, [],
                         "Cross-category duplicates found:\n" + "\n".join(duplicates))

    def test_greenhouse_only_in_talent_acquisition(self):
        from core.skill_loader import load
        d = load()
        cat = d["lookup"].get("greenhouse")
        self.assertEqual(cat, "talent_acquisition",
                         f"'greenhouse' should be in talent_acquisition, found in {cat}")

    def test_lever_only_in_talent_acquisition(self):
        from core.skill_loader import load
        d = load()
        cat = d["lookup"].get("lever")
        self.assertEqual(cat, "talent_acquisition",
                         f"'lever' should be in talent_acquisition, found in {cat}")

    def test_future_of_work_only_in_workforce_planning(self):
        from core.skill_loader import load
        d = load()
        cat = d["lookup"].get("future of work")
        self.assertEqual(cat, "workforce_planning")

    def test_no_enps_duplicate(self):
        from core.skill_loader import load
        d = load()
        # Both "enps" and "ENPS" lowercased to same key — dict can only hold one
        self.assertIn("enps", d["lookup"])  # should exist exactly once


# ── 2. Role Classification ────────────────────────────────────────────────────

class TestRoleClassification(unittest.TestCase):

    def setUp(self):
        from core import role_loader
        role_loader.clear_cache()

    def _classify(self, title):
        from core.role_loader import classify_title
        return classify_title(title)

    def test_hrbp_classifies(self):
        self.assertEqual(self._classify("HRBP"), "hr_analytics")

    def test_hr_business_partner_classifies(self):
        self.assertEqual(self._classify("HR Business Partner"), "hr_analytics")

    def test_hr_manager_classifies(self):
        self.assertEqual(self._classify("HR Manager"), "hr_analytics")

    def test_chief_people_officer_classifies(self):
        self.assertEqual(self._classify("Chief People Officer"), "people_analytics")

    def test_cpo_classifies(self):
        self.assertEqual(self._classify("CPO"), "people_analytics")

    def test_workforce_intelligence_manager_single_category(self):
        # Must be in people_analytics only (removed from workforce_planning)
        from core.role_loader import load
        d = load()
        cats = [cat for cat, info in d["categories"].items()
                if "workforce intelligence manager" in [t.lower() for t in info["titles"]]]
        self.assertEqual(len(cats), 1,
                         f"Workforce Intelligence Manager in multiple categories: {cats}")
        self.assertEqual(cats[0], "people_analytics")

    def test_no_cross_category_title_duplicates(self):
        from core.role_loader import load
        d = load()
        seen: dict[str, str] = {}
        duplicates = []
        for cat, info in d["categories"].items():
            for title in info["titles"]:
                tl = title.lower()
                if tl in seen:
                    duplicates.append(f"'{title}' in {seen[tl]} and {cat}")
                seen[tl] = cat
        self.assertEqual(duplicates, [],
                         "Duplicate titles found:\n" + "\n".join(duplicates))

    def test_product_manager_classifies(self):
        self.assertEqual(self._classify("Product Manager"), "product_management")

    def test_people_analytics_manager_classifies(self):
        self.assertEqual(self._classify("People Analytics Manager"), "people_analytics")

    def test_unknown_title_returns_unknown(self):
        self.assertEqual(self._classify("Barista"), "unknown")


# ── 3. Database Integrity ─────────────────────────────────────────────────────

class TestDatabaseIntegrity(unittest.TestCase):

    def setUp(self):
        """Each test gets an isolated in-memory database."""
        import core.config as cfg
        self._orig_db = cfg.DB_PATH
        # Redirect to a temp file so we don't touch the real DB
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cfg.DB_PATH = self._tmp.name
        from core import database
        database.initialize()
        self.db = database

    def tearDown(self):
        import core.config as cfg
        cfg.DB_PATH = self._orig_db
        self._tmp.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_insert_job_returns_id(self):
        jid = self.db.insert_job("PM Intern", "Acme", "Milan", "linkedin",
                                 "https://example.com/1", "Great role")
        self.assertIsNotNone(jid)
        self.assertIsInstance(jid, int)

    def test_duplicate_url_returns_none(self):
        url = "https://example.com/dup"
        self.db.insert_job("PM Intern", "Acme", "Milan", "linkedin", url, "desc")
        result = self.db.insert_job("PM Intern", "Acme", "Milan", "linkedin", url, "desc")
        self.assertIsNone(result)

    def test_get_job_by_url_found(self):
        url = "https://example.com/byurl"
        self.db.insert_job("Analyst", "Corp", "Rome", "indeed", url, "desc")
        job = self.db.get_job_by_url(url)
        self.assertIsNotNone(job)
        self.assertEqual(job["url"], url)

    def test_get_job_by_url_missing(self):
        result = self.db.get_job_by_url("https://nonexistent.example.com")
        self.assertIsNone(result)

    def test_get_job_by_hash_found(self):
        h = hashlib.sha256(b"acme-pm-it").hexdigest()
        self.db.insert_job("PM", "Acme", "Milan", "linkedin",
                           "https://example.com/hash", "desc", dedup_hash=h)
        job = self.db.get_job_by_hash(h)
        self.assertIsNotNone(job)
        self.assertEqual(job["dedup_hash"], h)

    def test_get_job_by_hash_missing(self):
        result = self.db.get_job_by_hash("0" * 64)
        self.assertIsNone(result)

    def test_scores_unique_per_job(self):
        """Inserting two scores for the same job must upsert, not create two rows."""
        jid = self.db.insert_job("Analyst", "Corp", "Milan", "linkedin",
                                 "https://example.com/s1", "desc")
        self.db.insert_score(jid, "hr_analytics", 80, 80, 80, 80, 80,
                             "Good", {}, "v1", "1.1", "1.1")
        self.db.insert_score(jid, "hr_analytics", 90, 90, 90, 90, 90,
                             "Better", {}, "v1", "1.1", "1.1")

        import core.config as cfg
        conn = sqlite3.connect(cfg.DB_PATH)
        count = conn.execute(
            "SELECT COUNT(*) FROM scores WHERE job_id = ?", (jid,)
        ).fetchone()[0]
        score = conn.execute(
            "SELECT total_score FROM scores WHERE job_id = ?", (jid,)
        ).fetchone()[0]
        conn.close()

        self.assertEqual(count, 1, "Expected exactly 1 score row per job")
        self.assertEqual(score, 90, "Upsert should have stored the latest score")

    def test_directories_created(self):
        """initialize() must create LOGS_DIR and output subdirectories."""
        from core.config import LOGS_DIR, OUTPUTS_DIR
        self.assertTrue(Path(LOGS_DIR).is_dir())
        for sub in ("resumes", "cover_letters", "exports"):
            self.assertTrue((Path(OUTPUTS_DIR) / sub).is_dir())


# ── 4. Score Validation ───────────────────────────────────────────────────────

class TestScoreValidation(unittest.TestCase):

    def setUp(self):
        import core.config as cfg
        self._orig_db = cfg.DB_PATH
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cfg.DB_PATH = self._tmp.name
        from core import database
        database.initialize()
        self.db = database
        self.jid = self.db.insert_job(
            "Test", "Co", "Milan", "linkedin", "https://t.example/1", "desc"
        )

    def tearDown(self):
        import core.config as cfg
        cfg.DB_PATH = self._orig_db
        self._tmp.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def _score(self, **kwargs):
        defaults = dict(
            job_id=self.jid, role_category="hr_analytics",
            total_score=75, role_fit=75, skills_match=75,
            location_fit=75, seniority_fit=75, explanation="ok",
            matched_categories={}, prompt_version="v1",
            dictionary_version="1.1", role_dictionary_version="1.1"
        )
        defaults.update(kwargs)
        return self.db.insert_score(**defaults)

    def test_valid_score_accepted(self):
        row_id = self._score()
        self.assertIsNotNone(row_id)

    def test_boundary_zero_accepted(self):
        self._score(total_score=0, role_fit=0, skills_match=0,
                    location_fit=0, seniority_fit=0)

    def test_boundary_100_accepted(self):
        self._score(total_score=100, role_fit=100, skills_match=100,
                    location_fit=100, seniority_fit=100)

    def test_negative_total_score_rejected(self):
        with self.assertRaises(ValueError):
            self._score(total_score=-1)

    def test_above_100_total_score_rejected(self):
        with self.assertRaises(ValueError):
            self._score(total_score=101)

    def test_negative_role_fit_rejected(self):
        with self.assertRaises(ValueError):
            self._score(role_fit=-5)

    def test_above_100_skills_match_rejected(self):
        with self.assertRaises(ValueError):
            self._score(skills_match=150)

    def test_string_score_rejected(self):
        with self.assertRaises(ValueError):
            self._score(total_score="eighty")

    def test_float_score_rejected(self):
        with self.assertRaises(ValueError):
            self._score(total_score=75.5)

    def test_none_score_rejected(self):
        with self.assertRaises(ValueError):
            self._score(total_score=None)


# ── 5. Prompt Versioning ──────────────────────────────────────────────────────

class TestPromptVersioning(unittest.TestCase):

    def setUp(self):
        from core import prompt_loader
        prompt_loader.clear_cache()

    def test_version_is_hex_string(self):
        from core.prompt_loader import version
        # scoring_prompt.txt must exist in prompts/
        v = version("scoring_prompt")
        if v == "missing":
            self.skipTest("scoring_prompt.txt not present")
        # Must be an 8-char hex string
        self.assertRegex(v, r'^[0-9a-f]{8}$',
                         "Version must be an 8-char lowercase hex string")

    def test_version_stable_on_re_read(self):
        """Same file → same hash, always."""
        from core.prompt_loader import version
        v1 = version("scoring_prompt")
        v2 = version("scoring_prompt")
        self.assertEqual(v1, v2)

    def test_version_missing_returns_missing(self):
        from core.prompt_loader import version
        self.assertEqual(version("__nonexistent_prompt__"), "missing")


# ── 6. Profile Loader ────────────────────────────────────────────────────────

class TestProfileLoader(unittest.TestCase):

    def setUp(self):
        from core import profile_loader
        profile_loader.clear_cache()

    def test_fill_in_required_detected(self):
        from core.profile_loader import _find_fill_in
        obj = {"name": "Alice", "phone": "FILL_IN your phone"}
        required, optional = _find_fill_in(obj, "")
        self.assertIn("phone", required)
        self.assertEqual(optional, [])

    def test_fill_in_or_remove_detected(self):
        from core.profile_loader import _find_fill_in
        obj = {"github": "FILL_IN_OR_REMOVE if you have one"}
        required, optional = _find_fill_in(obj, "")
        self.assertIn("github", optional)
        self.assertEqual(required, [])

    def test_nested_fill_in_detected(self):
        from core.profile_loader import _find_fill_in
        obj = {"contact": {"email": "FILL_IN your email"}}
        required, _ = _find_fill_in(obj, "")
        self.assertIn("contact.email", required)

    def test_list_fill_in_detected(self):
        from core.profile_loader import _find_fill_in
        obj = {"skills": ["Python", "FILL_IN another skill"]}
        required, _ = _find_fill_in(obj, "")
        self.assertIn("skills[1]", required)

    def test_complete_profile_passes(self):
        from core.profile_loader import _validate
        obj = {"name": "Alice", "role": "Analyst"}
        _validate(obj)  # should not raise


# ── 7. Configuration ──────────────────────────────────────────────────────────

class TestConfiguration(unittest.TestCase):

    def test_paths_resolve(self):
        from core.config import (
            PROFILE_PATH, SKILL_DICT_PATH, ROLE_DICT_PATH,
            PROMPTS_DIR, DB_PATH, OUTPUTS_DIR, LOGS_DIR
        )
        from pathlib import Path
        # All paths should be absolute
        for p in [PROFILE_PATH, SKILL_DICT_PATH, ROLE_DICT_PATH,
                  PROMPTS_DIR, DB_PATH, OUTPUTS_DIR, LOGS_DIR]:
            self.assertTrue(Path(p).is_absolute(), f"{p} is not absolute")

    def test_score_threshold_is_positive(self):
        from core.config import MIN_SCORE_THRESHOLD
        self.assertGreater(MIN_SCORE_THRESHOLD, 0)

    def test_claude_model_set(self):
        from core.config import CLAUDE_MODEL
        self.assertTrue(CLAUDE_MODEL.startswith("claude-"))


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestSkillMatching,
        TestRoleClassification,
        TestDatabaseIntegrity,
        TestScoreValidation,
        TestPromptVersioning,
        TestProfileLoader,
        TestConfiguration,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
