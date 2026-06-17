"""
Tests for the Company Eligibility Intelligence Layer (Phase 1.6).

Covers:
  - DB table creation and seeding
  - Exact and partial company name matching
  - Case-insensitive and legal-suffix-stripped lookup
  - Neutral fallback for unknown companies
  - Score field constraints (0–10)
  - Blend helpers: company profile fills silent JD dimensions
  - JD explicit signals override company profile
  - Eligibility score integration (check_eligibility with company param)
  - Career pivot score integration (company_quality + intl_friendliness)
  - Upsert / overwrite behaviour
  - Cache invalidation
"""
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_memory_db():
    """Return a fresh in-memory SQLite connection with the required tables seeded."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE company_eligibility_profiles (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name                TEXT    NOT NULL UNIQUE,
            visa_friendliness_score     INTEGER NOT NULL DEFAULT 5,
            english_environment_score   INTEGER NOT NULL DEFAULT 5,
            international_student_score INTEGER NOT NULL DEFAULT 5,
            mba_friendliness_score      INTEGER NOT NULL DEFAULT 5,
            last_updated                DATETIME DEFAULT (DATETIME('now'))
        );
        INSERT INTO company_eligibility_profiles
            (company_name, visa_friendliness_score, english_environment_score,
             international_student_score, mba_friendliness_score)
        VALUES
            ('Amazon',    10, 10, 9, 9),
            ('Google',    10, 10, 10, 10),
            ('McKinsey',  9, 10,  9, 10),
            ('EY',         7,  8,  7,  8),
            ('EY Italy',   2,  3,  3,  5),
            ('KPMG',       6,  7,  6,  8),
            ('SAP',        7,  7,  7,  8),
            ('Revolut',    8,  9,  9,  7);
    """)
    return conn


def _rows_from_conn(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM company_eligibility_profiles ORDER BY company_name"
    ).fetchall()
    return [dict(r) for r in rows]


# Patch database.get_all_company_eligibility_profiles to read from in-memory DB
def _patch_db(conn):
    return patch(
        "core.company_eligibility.database.get_all_company_eligibility_profiles",
        return_value=_rows_from_conn(conn),
    )


# ── 1. Database table and seeding ─────────────────────────────────────────────

class TestDatabaseSchema(unittest.TestCase):

    def test_table_created_and_seeded(self):
        conn = _make_memory_db()
        self.addCleanup(conn.close)
        rows = _rows_from_conn(conn)
        self.assertGreaterEqual(len(rows), 8)

    def test_all_columns_present(self):
        conn = _make_memory_db()
        self.addCleanup(conn.close)
        row = dict(conn.execute(
            "SELECT * FROM company_eligibility_profiles WHERE company_name = 'Amazon'"
        ).fetchone())
        for col in ("visa_friendliness_score", "english_environment_score",
                    "international_student_score", "mba_friendliness_score", "last_updated"):
            self.assertIn(col, row)

    def test_unique_constraint_on_company_name(self):
        conn = _make_memory_db()
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO company_eligibility_profiles (company_name) VALUES ('Amazon')"
            )

    def test_default_scores_are_five(self):
        conn = _make_memory_db()
        self.addCleanup(conn.close)
        conn.execute(
            "INSERT INTO company_eligibility_profiles (company_name) VALUES ('Neutral Co')"
        )
        row = dict(conn.execute(
            "SELECT * FROM company_eligibility_profiles WHERE company_name = 'Neutral Co'"
        ).fetchone())
        self.assertEqual(row["visa_friendliness_score"], 5)
        self.assertEqual(row["mba_friendliness_score"], 5)

    def test_upsert_overwrites_existing(self):
        conn = _make_memory_db()
        self.addCleanup(conn.close)
        conn.execute("""
            INSERT INTO company_eligibility_profiles
            (company_name, visa_friendliness_score, english_environment_score,
             international_student_score, mba_friendliness_score)
            VALUES ('TestCo', 3, 3, 3, 3)
            ON CONFLICT(company_name) DO UPDATE SET
                visa_friendliness_score = excluded.visa_friendliness_score
        """)
        conn.execute("""
            INSERT INTO company_eligibility_profiles
            (company_name, visa_friendliness_score, english_environment_score,
             international_student_score, mba_friendliness_score)
            VALUES ('TestCo', 9, 9, 9, 9)
            ON CONFLICT(company_name) DO UPDATE SET
                visa_friendliness_score     = excluded.visa_friendliness_score,
                english_environment_score   = excluded.english_environment_score,
                international_student_score = excluded.international_student_score,
                mba_friendliness_score      = excluded.mba_friendliness_score
        """)
        row = dict(conn.execute(
            "SELECT * FROM company_eligibility_profiles WHERE company_name = 'TestCo'"
        ).fetchone())
        self.assertEqual(row["visa_friendliness_score"], 9)


# ── 2. Lookup service — matching ──────────────────────────────────────────────

class TestLookupExactMatch(unittest.TestCase):

    def setUp(self):
        from core.company_eligibility import clear_cache
        clear_cache()
        self.conn = _make_memory_db()
        self.addCleanup(self.conn.close)

    def test_exact_match_amazon(self):
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            p = lookup_company("Amazon")
        self.assertEqual(p.company_name, "Amazon")
        self.assertFalse(p.is_fallback)
        self.assertEqual(p.visa_friendliness_score, 10)

    def test_exact_match_mckinsey(self):
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            p = lookup_company("McKinsey")
        self.assertEqual(p.mba_friendliness_score, 10)

    def test_case_insensitive(self):
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            p = lookup_company("amazon")
        self.assertFalse(p.is_fallback)

    def test_mixed_case(self):
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            p = lookup_company("GOOGLE")
        self.assertFalse(p.is_fallback)
        self.assertEqual(p.visa_friendliness_score, 10)

    def test_country_specific_ey_italy(self):
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            p = lookup_company("EY Italy")
        self.assertEqual(p.visa_friendliness_score, 2)
        self.assertEqual(p.english_environment_score, 3)


class TestLookupPartialMatch(unittest.TestCase):

    def setUp(self):
        from core.company_eligibility import clear_cache
        clear_cache()
        self.conn = _make_memory_db()
        self.addCleanup(self.conn.close)

    def test_amazon_with_suffix(self):
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            p = lookup_company("Amazon Inc")
        self.assertFalse(p.is_fallback)
        self.assertEqual(p.visa_friendliness_score, 10)

    def test_sap_se(self):
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            p = lookup_company("SAP SE")
        self.assertFalse(p.is_fallback)
        self.assertEqual(p.company_name, "SAP")

    def test_google_llc(self):
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            p = lookup_company("Google LLC")
        self.assertFalse(p.is_fallback)

    def test_partial_name_contained(self):
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            p = lookup_company("McKinsey & Company")
        self.assertFalse(p.is_fallback)


# ── 3. Neutral fallback ───────────────────────────────────────────────────────

class TestNeutralFallback(unittest.TestCase):

    def setUp(self):
        from core.company_eligibility import clear_cache
        clear_cache()
        self.conn = _make_memory_db()
        self.addCleanup(self.conn.close)

    def test_unknown_company_returns_fallback(self):
        from core.company_eligibility import lookup_company, NEUTRAL_PROFILE
        with _patch_db(self.conn):
            p = lookup_company("RandomStartup GmbH")
        self.assertTrue(p.is_fallback)

    def test_empty_string_returns_fallback(self):
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            p = lookup_company("")
        self.assertTrue(p.is_fallback)

    def test_none_name_returns_fallback(self):
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            p = lookup_company(None)
        self.assertTrue(p.is_fallback)

    def test_neutral_scores_all_five(self):
        from core.company_eligibility import NEUTRAL_PROFILE
        self.assertEqual(NEUTRAL_PROFILE.visa_friendliness_score, 5)
        self.assertEqual(NEUTRAL_PROFILE.english_environment_score, 5)
        self.assertEqual(NEUTRAL_PROFILE.international_student_score, 5)
        self.assertEqual(NEUTRAL_PROFILE.mba_friendliness_score, 5)


# ── 4. Profile dataclass invariants ───────────────────────────────────────────

class TestProfileInvariants(unittest.TestCase):

    def setUp(self):
        from core.company_eligibility import clear_cache
        clear_cache()
        self.conn = _make_memory_db()
        self.addCleanup(self.conn.close)

    def test_scores_within_0_10(self):
        from core.company_eligibility import lookup_company
        companies = ["Amazon", "Google", "McKinsey", "EY Italy", "KPMG", "Revolut"]
        with _patch_db(self.conn):
            for name in companies:
                p = lookup_company(name)
                for field in ("visa_friendliness_score", "english_environment_score",
                              "international_student_score", "mba_friendliness_score"):
                    score = getattr(p, field)
                    self.assertGreaterEqual(score, 0, f"{name}.{field} < 0")
                    self.assertLessEqual(score, 10, f"{name}.{field} > 10")

    def test_is_fallback_false_for_known(self):
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            p = lookup_company("Amazon")
        self.assertFalse(p.is_fallback)

    def test_profile_is_frozen(self):
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            p = lookup_company("Amazon")
        with self.assertRaises((AttributeError, TypeError)):
            p.visa_friendliness_score = 0


# ── 5. Cache invalidation ─────────────────────────────────────────────────────

class TestCacheInvalidation(unittest.TestCase):

    def test_clear_cache_causes_reload(self):
        from core import company_eligibility
        company_eligibility.clear_cache()
        conn1 = _make_memory_db()
        self.addCleanup(conn1.close)
        with _patch_db(conn1):
            p1 = company_eligibility.lookup_company("Amazon")
        self.assertEqual(p1.visa_friendliness_score, 10)

        # After cache clear, next call will re-fetch
        company_eligibility.clear_cache()
        conn2 = _make_memory_db()
        self.addCleanup(conn2.close)
        conn2.execute(
            "UPDATE company_eligibility_profiles SET visa_friendliness_score = 1 "
            "WHERE company_name = 'Amazon'"
        )
        with _patch_db(conn2):
            p2 = company_eligibility.lookup_company("Amazon")
        self.assertEqual(p2.visa_friendliness_score, 1)


# ── 6. Blend helpers: company fills silent JD dimensions ─────────────────────

class TestBlendHelpers(unittest.TestCase):

    def test_blend_visa_neutral_replaced_by_company(self):
        from core.eligibility import _blend_visa
        # Neutral = 28; Amazon visa=10 → 10*4 = 40
        self.assertEqual(_blend_visa(28, 10), 40)

    def test_blend_visa_neutral_low_company(self):
        from core.eligibility import _blend_visa
        # EY Italy visa=2 → 2*4 = 8
        self.assertEqual(_blend_visa(28, 2), 8)

    def test_blend_visa_jd_explicit_signal_wins(self):
        from core.eligibility import _blend_visa
        # JD says relocation support → 35; company score should not override
        self.assertEqual(_blend_visa(35, 10), 35)
        self.assertEqual(_blend_visa(40, 2), 40)
        self.assertEqual(_blend_visa(0, 10), 0)   # FAIL case

    def test_blend_env_zero_replaced_by_company(self):
        from core.eligibility import _blend_env
        # Amazon english=10; JD silent (0) → company fills
        self.assertEqual(_blend_env(0, 10), 10)
        self.assertEqual(_blend_env(0, 7), 7)

    def test_blend_env_jd_signal_wins(self):
        from core.eligibility import _blend_env
        # JD has signal (e.g. 7 for intl team) — company env=3 cannot reduce
        self.assertEqual(_blend_env(7, 3), 7)
        self.assertEqual(_blend_env(5, 10), 5)

    def test_blend_intl_zero_replaced(self):
        from core.eligibility import _blend_intl
        self.assertEqual(_blend_intl(0, 9), 9)
        self.assertEqual(_blend_intl(0, 5), 5)

    def test_blend_intl_jd_signal_wins(self):
        from core.eligibility import _blend_intl
        self.assertEqual(_blend_intl(8, 5), 8)


# ── 7. Eligibility score integration: check_eligibility(company=...) ─────────

class TestEligibilityScoreIntegration(unittest.TestCase):

    def setUp(self):
        from core.company_eligibility import clear_cache
        clear_cache()
        self.conn = _make_memory_db()
        self.addCleanup(self.conn.close)

    def _check(self, desc: str, company: str):
        from core.eligibility import check_eligibility
        with _patch_db(self.conn):
            return check_eligibility(desc, company=company)

    def test_amazon_boosts_neutral_visa_score(self):
        # JD with no visa signal → neutral would be 28; Amazon visa=10 → 40
        r = self._check("Join our product team. Great culture.", "Amazon")
        self.assertEqual(r.visa_accessibility, 40)

    def test_amazon_boosts_english_environment(self):
        r = self._check("Join our product team. Great culture.", "Amazon")
        # Amazon english=10, JD silent → env = 10
        self.assertEqual(r.english_environment, 10)

    def test_amazon_boosts_intl_signals(self):
        r = self._check("Join our product team. Great culture.", "Amazon")
        # Amazon international_student=9, JD silent → intl = 9
        self.assertEqual(r.international_signals, 9)

    def test_ey_italy_reduces_neutral_visa_score(self):
        r = self._check("Join our audit team.", "EY Italy")
        # EY Italy visa=2 → 2*4 = 8
        self.assertEqual(r.visa_accessibility, 8)

    def test_ey_italy_low_english_environment(self):
        r = self._check("Join our audit team.", "EY Italy")
        # EY Italy english=3, JD silent → env = 3
        self.assertEqual(r.english_environment, 3)

    def test_jd_sponsorship_beats_ey_italy_visa(self):
        # Explicit JD signal (40) must win over EY Italy's company profile (8)
        r = self._check(
            "Visa sponsorship is available and provided to all candidates.", "EY Italy"
        )
        self.assertEqual(r.visa_accessibility, 40)

    def test_jd_working_language_english_beats_company(self):
        # JD says "working language is English" → lang_acc=40, eng_env=10
        r = self._check(
            "The working language is English across all teams.", "EY Italy"
        )
        self.assertEqual(r.english_environment, 10)

    def test_unknown_company_neutral_scores_unchanged(self):
        # Unknown company → fallback, no blending applied
        r = self._check("Join our product team.", "UnknownStartup XYZ")
        # Neutral: visa=28, env=0, intl=0
        self.assertEqual(r.visa_accessibility, 28)
        self.assertEqual(r.english_environment, 0)
        self.assertEqual(r.international_signals, 0)

    def test_rejected_job_still_zero_with_amazon(self):
        # Hard reject must score 0 regardless of company
        r = self._check("Italian required. EU citizenship required.", "Amazon")
        self.assertEqual(r.eligibility_score, 0)
        self.assertEqual(r.visa_accessibility, 0)

    def test_mckinsey_high_total_score(self):
        # McKinsey neutral JD: visa=36, eng=10, intl=9 → total high
        r = self._check("Great consulting opportunity for top MBA graduates.", "McKinsey")
        self.assertGreater(r.eligibility_score, 70)

    def test_kpmg_mid_range_score(self):
        r = self._check("Join our tax advisory team.", "KPMG")
        # KPMG visa=6 → 24, eng=7, intl=6 → total = lang(30)+visa(24)+eng(7)+intl(6) = 67
        self.assertGreater(r.eligibility_score, 55)


# ── 8. Career pivot score integration ─────────────────────────────────────────

class TestCareerPivotIntegration(unittest.TestCase):

    def setUp(self):
        from core.company_eligibility import clear_cache
        clear_cache()
        self.conn = _make_memory_db()
        self.addCleanup(self.conn.close)

    def _score(self, company: str, desc: str = "", location: str = "London") -> object:
        from core.scorer import _score_company_quality, _score_intl_friendliness
        from core.company_eligibility import lookup_company
        with _patch_db(self.conn):
            profile = lookup_company(company)
        co_score, _ = _score_company_quality(company, profile)
        intl_score, _ = _score_intl_friendliness(desc, location, profile)
        return co_score, intl_score

    def test_mckinsey_mba_bonus_applied(self):
        co_score, _ = self._score("McKinsey")
        # McKinsey is Tier 1 (base=15) + mba=10 ≥8 (+2) = min(15, 17) = 15
        self.assertEqual(co_score, 15)

    def test_kpmg_mba_bonus_applied(self):
        # KPMG: tier depends on company_tiers.json. mba=8 ≥8 → +2 bonus applied.
        co_score, _ = self._score("KPMG")
        self.assertGreaterEqual(co_score, 4)   # at least floor, bonus applied

    def test_unknown_company_no_mba_bonus(self):
        from core.scorer import _score_company_quality
        from core.company_eligibility import NEUTRAL_PROFILE
        co_score, note = _score_company_quality("Unknown Corp", NEUTRAL_PROFILE)
        self.assertNotIn("mba_profile", note)

    def test_amazon_intl_floor_applied(self):
        # Amazon visa=10, intl=9 → company_intl = (10+9)/2 = 9.5 → 10
        _, intl_score = self._score("Amazon", desc="We offer a role.", location="Berlin")
        self.assertGreaterEqual(intl_score, 9)

    def test_ey_italy_intl_does_not_boost(self):
        # EY Italy visa=2, intl=3 → company_intl = (2+3)/2 = 2 → floor=2
        # Location Berlin gives +2 target_city → score=2; company floor is also ~2, no boost
        _, intl_score = self._score("EY Italy", desc="", location="Milan")
        # Should be at most the eu_default + city bonus, not boosted by EY Italy
        self.assertLessEqual(intl_score, 4)

    def test_revolut_intl_floor(self):
        # Revolut visa=8, intl=9 → company_intl = (8+9)/2 ≈ 9
        _, intl_score = self._score("Revolut", desc="", location="London")
        self.assertGreaterEqual(intl_score, 8)


# ── 9. Seed file data validation ──────────────────────────────────────────────

class TestSeedFileData(unittest.TestCase):

    def test_seed_file_exists(self):
        seed_path = Path(__file__).parent.parent / "data" / "company_eligibility_seed.json"
        self.assertTrue(seed_path.exists())

    def test_seed_has_required_companies(self):
        import json
        seed_path = Path(__file__).parent.parent / "data" / "company_eligibility_seed.json"
        with open(seed_path) as f:
            data = json.load(f)
        names = {c["company_name"] for c in data["companies"]}
        required = {"Amazon", "Google", "McKinsey", "BCG", "Bain", "EY", "PwC", "KPMG",
                    "Deloitte", "Accenture", "Microsoft", "Meta", "Revolut", "Klarna",
                    "Stripe", "SAP"}
        self.assertTrue(required.issubset(names), f"Missing: {required - names}")

    def test_all_seed_scores_in_range(self):
        import json
        seed_path = Path(__file__).parent.parent / "data" / "company_eligibility_seed.json"
        with open(seed_path) as f:
            data = json.load(f)
        for entry in data["companies"]:
            for field in ("visa_friendliness_score", "english_environment_score",
                          "international_student_score", "mba_friendliness_score"):
                val = entry[field]
                name = entry["company_name"]
                self.assertGreaterEqual(val, 0, f"{name}.{field} < 0")
                self.assertLessEqual(val, 10, f"{name}.{field} > 10")

    def test_seed_version_present(self):
        import json
        seed_path = Path(__file__).parent.parent / "data" / "company_eligibility_seed.json"
        with open(seed_path) as f:
            data = json.load(f)
        self.assertIn("seed_version", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
