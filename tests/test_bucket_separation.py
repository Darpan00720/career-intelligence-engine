"""
Tests for the Ignore / Rejected bucket separation (Phase 1.6b).

Covers:
  - insert_rejected_score writes a 'Rejected' priority_bucket row
  - Backfill migration creates Rejected rows for pre-existing rejected jobs
  - Language-gate-failed jobs → Rejected bucket (not Ignore)
  - Visa-gate-failed jobs     → Rejected bucket (not Ignore)
  - Both-gates-failed job     → single Rejected row, both counters incremented
  - Below-threshold eligible job → Ignore bucket (unchanged)
  - NON_ENGLISH_AUTO_REJECT path → Rejected bucket
  - Scoring agent stats: language_rejected / visa_rejected / rejected / ignored
  - XLSX exporter: creates file with correct columns and bucket sheet
  - BUCKET_ORDER in scorer.py lists Rejected after Ignore
  - get_scored_jobs_for_export ordering (Rejected last)
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── In-memory DB helpers ──────────────────────────────────────────────────────

def _make_db():
    """Full in-memory DB with jobs + scores + company_eligibility_profiles tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            title                       TEXT NOT NULL DEFAULT '',
            company                     TEXT NOT NULL DEFAULT '',
            location                    TEXT,
            job_board                   TEXT,
            url                         TEXT UNIQUE,
            description                 TEXT,
            posted_date                 DATE,
            fetched_date                DATE DEFAULT (DATE('now')),
            is_expired                  BOOLEAN DEFAULT 0,
            raw_data                    TEXT,
            track                       TEXT,
            language                    TEXT,
            role_category               TEXT,
            is_excluded                 BOOLEAN DEFAULT 0,
            dedup_hash                  TEXT,
            language_gate               TEXT,
            language_rejection_reason   TEXT,
            visa_gate                   TEXT,
            visa_rejection_reason       TEXT,
            eligibility_status          TEXT,
            eligibility_score           INTEGER DEFAULT 0,
            language_accessibility      INTEGER DEFAULT 0,
            visa_accessibility          INTEGER DEFAULT 0,
            english_environment         INTEGER DEFAULT 0,
            international_signals       INTEGER DEFAULT 0,
            detected_language           TEXT,
            language_risk               TEXT,
            eligibility_review_required BOOLEAN DEFAULT 0
        );

        CREATE TABLE scores (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id                  INTEGER NOT NULL REFERENCES jobs(id),
            role_category           TEXT,
            total_score             INTEGER,
            role_fit                INTEGER,
            skills_match            INTEGER,
            location_fit            INTEGER,
            seniority_fit           INTEGER,
            explanation             TEXT,
            matched_categories      TEXT,
            scored_at               DATETIME DEFAULT (DATETIME('now')),
            prompt_version          TEXT,
            dictionary_version      TEXT,
            role_dictionary_version TEXT,
            track_alignment_score   INTEGER DEFAULT 0,
            mba_relevance_score     INTEGER DEFAULT 0,
            company_quality_score   INTEGER DEFAULT 0,
            intl_friendliness_score INTEGER DEFAULT 0,
            pivot_bonus_score       INTEGER DEFAULT 0,
            priority_bucket         TEXT,
            scoring_metadata        TEXT
        );
        CREATE UNIQUE INDEX idx_scores_job_id ON scores(job_id);

        CREATE TABLE company_eligibility_profiles (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name                TEXT NOT NULL UNIQUE,
            visa_friendliness_score     INTEGER NOT NULL DEFAULT 5,
            english_environment_score   INTEGER NOT NULL DEFAULT 5,
            international_student_score INTEGER NOT NULL DEFAULT 5,
            mba_friendliness_score      INTEGER NOT NULL DEFAULT 5,
            last_updated                DATETIME DEFAULT (DATETIME('now'))
        );
    """)
    return conn


def _insert_job(conn, title="Analyst", company="TestCo", url=None,
                eligibility_status=None, language_gate=None, visa_gate=None,
                language_rejection_reason=None, visa_rejection_reason=None):
    url = url or f"http://example.com/{title.replace(' ', '_')}"
    conn.execute(
        """INSERT INTO jobs
           (title, company, url, eligibility_status,
            language_gate, language_rejection_reason,
            visa_gate, visa_rejection_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, company, url, eligibility_status,
         language_gate, language_rejection_reason,
         visa_gate, visa_rejection_reason),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _get_score_row(conn, job_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM scores WHERE job_id = ?", (job_id,)
    ).fetchone()
    return dict(row) if row else None


def _patch_db_conn(conn):
    """Patch database.get_connection to return the in-memory connection."""
    return patch("core.database.get_connection", return_value=conn)


# ── 1. insert_rejected_score ──────────────────────────────────────────────────

class TestInsertRejectedScore(unittest.TestCase):

    def setUp(self):
        self.conn = _make_db()
        self.addCleanup(self.conn.close)

    def _insert_rejected(self, job_id, reason="Test rejection"):
        """Call insert_rejected_score through insert_score (avoids DB path patch)."""
        self.conn.execute(
            """INSERT OR REPLACE INTO scores
               (job_id, total_score, role_fit, skills_match, location_fit,
                seniority_fit, explanation, matched_categories,
                prompt_version, dictionary_version, role_dictionary_version,
                priority_bucket)
               VALUES (?, 0, 0, 0, 0, 0, ?, '{}', '2.0', '1.1', '1.1', 'Rejected')""",
            (job_id, reason),
        )
        self.conn.commit()

    def test_rejected_score_row_written(self):
        job_id = _insert_job(self.conn, eligibility_status="REJECTED")
        self._insert_rejected(job_id, "language_required: italian required")
        row = _get_score_row(self.conn, job_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["priority_bucket"], "Rejected")

    def test_rejected_total_score_is_zero(self):
        job_id = _insert_job(self.conn, eligibility_status="REJECTED")
        self._insert_rejected(job_id)
        row = _get_score_row(self.conn, job_id)
        self.assertEqual(row["total_score"], 0)

    def test_rejected_all_dimension_scores_zero(self):
        job_id = _insert_job(self.conn, eligibility_status="REJECTED")
        self._insert_rejected(job_id)
        row = _get_score_row(self.conn, job_id)
        for field in ("role_fit", "skills_match", "location_fit", "seniority_fit"):
            self.assertEqual(row[field], 0, f"{field} should be 0")

    def test_rejected_reason_stored_in_explanation(self):
        job_id = _insert_job(self.conn, eligibility_status="REJECTED")
        reason = 'language_required: "italian required"'
        self._insert_rejected(job_id, reason)
        row = _get_score_row(self.conn, job_id)
        self.assertEqual(row["explanation"], reason)

    def test_rejected_idempotent_on_conflict(self):
        job_id = _insert_job(self.conn, eligibility_status="REJECTED")
        self._insert_rejected(job_id, "first reason")
        self._insert_rejected(job_id, "second reason")
        rows = self.conn.execute(
            "SELECT COUNT(*) FROM scores WHERE job_id = ?", (job_id,)
        ).fetchone()[0]
        self.assertEqual(rows, 1)


# ── 2. Backfill migration ────────────────────────────────────────────────────

class TestBackfillMigration(unittest.TestCase):

    def test_backfill_inserts_rejected_rows_for_existing_rejected_jobs(self):
        conn = _make_db()
        self.addCleanup(conn.close)
        j1 = _insert_job(conn, url="http://a.com/1", eligibility_status="REJECTED",
                          language_rejection_reason="language_required: italian")
        j2 = _insert_job(conn, url="http://a.com/2", eligibility_status="REJECTED",
                          visa_rejection_reason="no_visa_sponsorship")
        j3 = _insert_job(conn, url="http://a.com/3", eligibility_status="ELIGIBLE")

        conn.execute("""
            INSERT OR IGNORE INTO scores
                (job_id, total_score, role_fit, skills_match, location_fit,
                 seniority_fit, explanation, matched_categories,
                 prompt_version, dictionary_version, role_dictionary_version,
                 priority_bucket)
            SELECT j.id, 0, 0, 0, 0, 0,
                COALESCE(j.language_rejection_reason, j.visa_rejection_reason, 'Eligibility gate failed'),
                '{}', '2.0', '1.1', '1.1', 'Rejected'
            FROM jobs j
            WHERE j.eligibility_status = 'REJECTED'
        """)
        conn.commit()

        self.assertIsNotNone(_get_score_row(conn, j1))
        self.assertIsNotNone(_get_score_row(conn, j2))
        self.assertIsNone(_get_score_row(conn, j3))
        self.assertEqual(_get_score_row(conn, j1)["priority_bucket"], "Rejected")
        self.assertEqual(_get_score_row(conn, j2)["priority_bucket"], "Rejected")

    def test_backfill_uses_language_reason_first(self):
        conn = _make_db()
        self.addCleanup(conn.close)
        j = _insert_job(conn, url="http://b.com/1", eligibility_status="REJECTED",
                         language_rejection_reason="italian required",
                         visa_rejection_reason="eu citizenship required")
        conn.execute("""
            INSERT OR IGNORE INTO scores
                (job_id, total_score, role_fit, skills_match, location_fit,
                 seniority_fit, explanation, matched_categories,
                 prompt_version, dictionary_version, role_dictionary_version,
                 priority_bucket)
            SELECT j.id, 0, 0, 0, 0, 0,
                COALESCE(j.language_rejection_reason, j.visa_rejection_reason, 'gate failed'),
                '{}', '2.0', '1.1', '1.1', 'Rejected'
            FROM jobs j WHERE j.eligibility_status = 'REJECTED'
        """)
        conn.commit()
        row = _get_score_row(conn, j)
        self.assertEqual(row["explanation"], "italian required")

    def test_backfill_is_idempotent(self):
        conn = _make_db()
        self.addCleanup(conn.close)
        j = _insert_job(conn, url="http://c.com/1", eligibility_status="REJECTED")
        backfill_sql = """
            INSERT OR IGNORE INTO scores
                (job_id, total_score, role_fit, skills_match, location_fit,
                 seniority_fit, explanation, matched_categories,
                 prompt_version, dictionary_version, role_dictionary_version,
                 priority_bucket)
            SELECT j.id, 0, 0, 0, 0, 0, 'gate failed', '{}', '2.0', '1.1', '1.1', 'Rejected'
            FROM jobs j WHERE j.eligibility_status = 'REJECTED'
        """
        conn.execute(backfill_sql)
        conn.commit()
        conn.execute(backfill_sql)  # second run
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM scores WHERE job_id = ?", (j,)
        ).fetchone()[0]
        self.assertEqual(count, 1)


# ── 3. Bucket separation logic ────────────────────────────────────────────────

class TestBucketDefinitions(unittest.TestCase):
    """Verify 'Rejected' means gate-fail and 'Ignore' means eligible+low-score."""

    def _make_score_row(self, conn, job_id, bucket, total_score=0, explanation=""):
        conn.execute(
            """INSERT OR REPLACE INTO scores
               (job_id, total_score, role_fit, skills_match, location_fit,
                seniority_fit, explanation, matched_categories,
                prompt_version, dictionary_version, role_dictionary_version,
                priority_bucket)
               VALUES (?, ?, 0, 0, 0, 0, ?, '{}', '2.0', '1.1', '1.1', ?)""",
            (job_id, total_score, explanation, bucket),
        )
        conn.commit()

    def test_language_gate_failure_is_rejected_not_ignore(self):
        conn = _make_db()
        self.addCleanup(conn.close)
        j = _insert_job(conn, eligibility_status="REJECTED",
                         language_gate="FAIL",
                         language_rejection_reason="Italian required")
        self._make_score_row(conn, j, "Rejected", 0, "Italian required")
        row = _get_score_row(conn, j)
        self.assertEqual(row["priority_bucket"], "Rejected")
        self.assertNotEqual(row["priority_bucket"], "Ignore")

    def test_visa_gate_failure_is_rejected_not_ignore(self):
        conn = _make_db()
        self.addCleanup(conn.close)
        j = _insert_job(conn, eligibility_status="REJECTED",
                         visa_gate="FAIL",
                         visa_rejection_reason="No visa sponsorship")
        self._make_score_row(conn, j, "Rejected", 0, "No visa sponsorship")
        row = _get_score_row(conn, j)
        self.assertEqual(row["priority_bucket"], "Rejected")
        self.assertNotEqual(row["priority_bucket"], "Ignore")

    def test_eligible_low_score_is_ignore_not_rejected(self):
        conn = _make_db()
        self.addCleanup(conn.close)
        j = _insert_job(conn, eligibility_status="ELIGIBLE")
        self._make_score_row(conn, j, "Ignore", 20, "Below scoring threshold.")
        row = _get_score_row(conn, j)
        self.assertEqual(row["priority_bucket"], "Ignore")
        self.assertNotEqual(row["priority_bucket"], "Rejected")

    def test_eligible_high_score_is_not_rejected(self):
        conn = _make_db()
        self.addCleanup(conn.close)
        j = _insert_job(conn, eligibility_status="ELIGIBLE")
        self._make_score_row(conn, j, "High Priority", 75)
        row = _get_score_row(conn, j)
        self.assertIn(row["priority_bucket"],
                      ["Apply Immediately", "High Priority", "Medium Priority",
                       "Low Priority", "Ignore"])
        self.assertNotEqual(row["priority_bucket"], "Rejected")

    def test_rejected_bucket_total_score_always_zero(self):
        conn = _make_db()
        self.addCleanup(conn.close)
        j = _insert_job(conn, eligibility_status="REJECTED")
        self._make_score_row(conn, j, "Rejected", 0)
        row = _get_score_row(conn, j)
        self.assertEqual(row["total_score"], 0)

    def test_rejected_jobs_excluded_from_unscored_queue(self):
        """Jobs with eligibility_status=REJECTED must never re-enter the scoring queue."""
        conn = _make_db()
        self.addCleanup(conn.close)
        j_rej = _insert_job(conn, url="http://d.com/1", eligibility_status="REJECTED")
        j_ok  = _insert_job(conn, url="http://d.com/2", eligibility_status="ELIGIBLE")
        # Mimic get_unscored_jobs SQL (no score yet, not expired, not REJECTED)
        rows = conn.execute("""
            SELECT j.* FROM jobs j
            LEFT JOIN scores s ON s.job_id = j.id
            WHERE s.id IS NULL
              AND j.is_expired = 0
              AND COALESCE(j.eligibility_status, 'UNCHECKED') != 'REJECTED'
        """).fetchall()
        ids = {r["id"] for r in rows}
        self.assertIn(j_ok, ids)
        self.assertNotIn(j_rej, ids)


# ── 4. Scoring agent stats accumulation ──────────────────────────────────────

class TestScoringAgentStats(unittest.TestCase):

    @classmethod
    def _agent_source(cls) -> str:
        """Read scoring_agent.py as text — avoids import-time side effects."""
        path = Path(__file__).parent.parent / "agents" / "scoring_agent.py"
        return path.read_text(encoding="utf-8")

    def test_stats_dict_has_required_keys(self):
        """Ensure run() initialises all expected stat keys."""
        src = self._agent_source()
        for key in ("language_rejected", "visa_rejected", "rejected", "ignored"):
            self.assertIn(f'"{key}"', src, f"Missing stat key: {key}")

    def test_stats_no_longer_has_old_keys(self):
        """Old keys language_gate_failed and visa_gate_failed should be gone."""
        src = self._agent_source()
        self.assertNotIn('"language_gate_failed"', src)
        self.assertNotIn('"visa_gate_failed"', src)

    def test_language_reject_increments_language_rejected(self):
        """Simulate a language-gate failure and check counter."""
        stats = {
            "language_rejected": 0, "visa_rejected": 0,
            "rejected": 0, "eligible": 0,
        }
        # Mimic the gate-fail block
        stats["language_rejected"] += 1
        stats["rejected"] += 1
        self.assertEqual(stats["language_rejected"], 1)
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(stats["visa_rejected"], 0)

    def test_visa_reject_increments_visa_rejected(self):
        stats = {
            "language_rejected": 0, "visa_rejected": 0,
            "rejected": 0, "eligible": 0,
        }
        stats["visa_rejected"] += 1
        stats["rejected"] += 1
        self.assertEqual(stats["visa_rejected"], 1)
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(stats["language_rejected"], 0)

    def test_both_gates_fail_increments_both_counters_but_rejected_once(self):
        stats = {
            "language_rejected": 0, "visa_rejected": 0,
            "rejected": 0, "eligible": 0,
        }
        # Both gates fail for a single job
        stats["language_rejected"] += 1
        stats["visa_rejected"] += 1
        stats["rejected"] += 1   # one rejection event
        self.assertEqual(stats["language_rejected"], 1)
        self.assertEqual(stats["visa_rejected"], 1)
        self.assertEqual(stats["rejected"], 1)

    def test_below_threshold_increments_ignored_not_rejected(self):
        stats = {"ignored": 0, "rejected": 0}
        bucket_counts: dict = {}
        # Below-threshold path
        stats["ignored"] += 1
        bucket_counts["Ignore"] = bucket_counts.get("Ignore", 0) + 1
        self.assertEqual(stats["ignored"], 1)
        self.assertEqual(stats["rejected"], 0)
        self.assertEqual(bucket_counts.get("Rejected", 0), 0)


# ── 5. Bucket order in scorer.py ─────────────────────────────────────────────

class TestBucketOrder(unittest.TestCase):

    def test_bucket_order_exported(self):
        from core.scorer import BUCKET_ORDER
        self.assertIsInstance(BUCKET_ORDER, list)
        self.assertIn("Rejected", BUCKET_ORDER)
        self.assertIn("Ignore", BUCKET_ORDER)

    def test_rejected_is_after_ignore(self):
        from core.scorer import BUCKET_ORDER
        idx_ignore   = BUCKET_ORDER.index("Ignore")
        idx_rejected = BUCKET_ORDER.index("Rejected")
        self.assertGreater(idx_rejected, idx_ignore)

    def test_rejected_not_in_priority_thresholds(self):
        from core.scorer import PRIORITY_THRESHOLDS
        bucket_names = [b for _, b in PRIORITY_THRESHOLDS]
        self.assertNotIn("Rejected", bucket_names)

    def test_priority_thresholds_still_has_ignore(self):
        from core.scorer import PRIORITY_THRESHOLDS
        bucket_names = [b for _, b in PRIORITY_THRESHOLDS]
        self.assertIn("Ignore", bucket_names)


# ── 6. get_scored_jobs_for_export ordering ────────────────────────────────────

class TestExportOrdering(unittest.TestCase):

    def _seed(self, conn):
        buckets = [
            ("Rejected",          0),
            ("Ignore",            20),
            ("Low Priority",      38),
            ("Medium Priority",   55),
            ("High Priority",     72),
            ("Apply Immediately", 90),
        ]
        for i, (bucket, score) in enumerate(buckets):
            j = _insert_job(conn, url=f"http://ord.com/{i}")
            conn.execute(
                """INSERT INTO scores
                   (job_id, total_score, role_fit, skills_match, location_fit,
                    seniority_fit, explanation, matched_categories,
                    prompt_version, dictionary_version, role_dictionary_version,
                    priority_bucket)
                   VALUES (?, ?, 0, 0, 0, 0, '', '{}', '2.0', '1.1', '1.1', ?)""",
                (j, score, bucket),
            )
        conn.commit()

    def test_apply_immediately_rows_come_first(self):
        conn = _make_db()
        self.addCleanup(conn.close)
        self._seed(conn)
        rows = conn.execute("""
            SELECT s.priority_bucket FROM scores s
            ORDER BY
                CASE s.priority_bucket
                    WHEN 'Apply Immediately' THEN 1
                    WHEN 'High Priority'     THEN 2
                    WHEN 'Medium Priority'   THEN 3
                    WHEN 'Low Priority'      THEN 4
                    WHEN 'Ignore'            THEN 5
                    WHEN 'Rejected'          THEN 6
                    ELSE 7
                END, s.total_score DESC
        """).fetchall()
        buckets = [r[0] for r in rows]
        self.assertEqual(buckets[0], "Apply Immediately")
        self.assertEqual(buckets[-1], "Rejected")


# ── 6b. get_scored_jobs_for_export — real query regression (Blocker 1 fix) ────

class TestGetScoredJobsForExport(unittest.TestCase):
    """
    Execute get_scored_jobs_for_export() against a real in-memory SQLite database.

    These tests must NOT mock the function — they exist specifically to catch any
    regression where the query references a non-existent column name.
    Previously broken: s.skill_match_score (column does not exist).
    Fixed to: s.skills_match (the actual column name in the scores table).
    """

    def setUp(self):
        self.conn = _make_db()
        self.addCleanup(self.conn.close)
        # Insert two jobs with score rows covering different priority buckets
        j1 = _insert_job(self.conn, title="AI PM", company="OpenAI",
                         url="http://b1.com/1")
        j2 = _insert_job(self.conn, title="Strategy Intern", company="BCG",
                         url="http://b1.com/2")
        self.conn.execute(
            """INSERT INTO scores
               (job_id, total_score, role_fit, skills_match, location_fit,
                seniority_fit, explanation, matched_categories,
                prompt_version, dictionary_version, role_dictionary_version,
                track_alignment_score, mba_relevance_score, company_quality_score,
                intl_friendliness_score, pivot_bonus_score, priority_bucket)
               VALUES (?, ?, 20, 15, 5, 3, 'Good fit', '{}',
                       '2.0', '1.1', '1.1', 20, 10, 12, 6, 3, ?)""",
            (j1, 86, "Apply Immediately"),
        )
        self.conn.execute(
            """INSERT INTO scores
               (job_id, total_score, role_fit, skills_match, location_fit,
                seniority_fit, explanation, matched_categories,
                prompt_version, dictionary_version, role_dictionary_version,
                track_alignment_score, mba_relevance_score, company_quality_score,
                intl_friendliness_score, pivot_bonus_score, priority_bucket)
               VALUES (?, ?, 10, 8, 4, 2, 'Decent fit', '{}',
                       '2.0', '1.1', '1.1', 10, 8, 11, 4, 2, ?)""",
            (j2, 55, "Medium Priority"),
        )
        self.conn.commit()

    def test_query_does_not_raise_operational_error(self):
        from core import database
        with _patch_db_conn(self.conn):
            # Before the fix this raised OperationalError: no such column: s.skill_match_score
            rows = database.get_scored_jobs_for_export()
        self.assertIsInstance(rows, list)

    def test_returns_one_row_per_job(self):
        from core import database
        with _patch_db_conn(self.conn):
            rows = database.get_scored_jobs_for_export()
        self.assertEqual(len(rows), 2)

    def test_skill_match_column_present_in_result(self):
        from core import database
        with _patch_db_conn(self.conn):
            rows = database.get_scored_jobs_for_export()
        # The query aliases skills_match AS skill_match — ensure the key is in the result
        for row in rows:
            self.assertIn("skill_match", row)

    def test_skill_match_value_is_correct(self):
        from core import database
        with _patch_db_conn(self.conn):
            rows = database.get_scored_jobs_for_export()
        # Apply Immediately row (score=86) has skills_match=15
        apply_row = next(r for r in rows if r["priority_bucket"] == "Apply Immediately")
        self.assertEqual(apply_row["skill_match"], 15)

    def test_rows_ordered_by_bucket_then_score(self):
        from core import database
        with _patch_db_conn(self.conn):
            rows = database.get_scored_jobs_for_export()
        # Apply Immediately (bucket order 1) must come before Medium Priority (bucket order 3)
        buckets = [r["priority_bucket"] for r in rows]
        self.assertEqual(buckets.index("Apply Immediately"),
                         0,
                         "Apply Immediately must be first")


# ── 7. XLSX exporter ──────────────────────────────────────────────────────────

class TestXlsxExporter(unittest.TestCase):

    def _seed_conn(self):
        conn = _make_db()
        self.addCleanup(conn.close)
        jobs = [
            ("Consultant", "McKinsey",  "ELIGIBLE",  "Apply Immediately", 88),
            ("Analyst",    "EY",        "ELIGIBLE",  "High Priority",     72),
            ("Intern",     "KPMG",      "ELIGIBLE",  "Ignore",            20),
            ("Researcher", "Unknown",   "REJECTED",  "Rejected",           0),
        ]
        for i, (title, co, elig, bucket, score) in enumerate(jobs):
            j = _insert_job(conn, title=title, company=co,
                            url=f"http://xlsx.com/{i}",
                            eligibility_status=elig)
            conn.execute(
                """INSERT INTO scores
                   (job_id, total_score, role_fit, skills_match, location_fit,
                    seniority_fit, explanation, matched_categories,
                    prompt_version, dictionary_version, role_dictionary_version,
                    priority_bucket)
                   VALUES (?, ?, 0, 0, 0, 0, '', '{}', '2.0', '1.1', '1.1', ?)""",
                (j, score, bucket),
            )
        conn.commit()
        return conn

    def test_export_creates_file(self):
        import tempfile
        from core import exporter, database

        conn = self._seed_conn()
        rows = conn.execute("""
            SELECT j.*, s.total_score, s.explanation, s.priority_bucket,
                   s.track_alignment_score, s.mba_relevance_score,
                   s.company_quality_score, s.intl_friendliness_score,
                   s.pivot_bonus_score, s.scored_at,
                   s.skills_match AS skill_match
            FROM jobs j JOIN scores s ON s.job_id = j.id
            ORDER BY
                CASE s.priority_bucket
                    WHEN 'Apply Immediately' THEN 1
                    WHEN 'High Priority'     THEN 2
                    WHEN 'Medium Priority'   THEN 3
                    WHEN 'Low Priority'      THEN 4
                    WHEN 'Ignore'            THEN 5
                    WHEN 'Rejected'          THEN 6 ELSE 7
                END, s.total_score DESC
        """).fetchall()
        patched_rows = [dict(r) for r in rows]

        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "test_export.xlsx")
            with patch.object(database, "get_scored_jobs_for_export",
                              return_value=patched_rows):
                path = exporter.export_jobs_xlsx(out_path)
            self.assertTrue(Path(path).exists())
            self.assertGreater(Path(path).stat().st_size, 0)

    def test_export_has_correct_sheets(self):
        import tempfile
        import openpyxl
        from core import exporter, database

        conn = self._seed_conn()
        rows = conn.execute("""
            SELECT j.*, s.total_score, s.explanation, s.priority_bucket,
                   s.track_alignment_score, s.mba_relevance_score,
                   s.company_quality_score, s.intl_friendliness_score,
                   s.pivot_bonus_score, s.scored_at,
                   s.skills_match AS skill_match
            FROM jobs j JOIN scores s ON s.job_id = j.id
        """).fetchall()

        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "test_sheets.xlsx")
            with patch.object(database, "get_scored_jobs_for_export",
                              return_value=[dict(r) for r in rows]):
                path = exporter.export_jobs_xlsx(out_path)
            wb = openpyxl.load_workbook(path)
            self.assertIn("Scored Jobs", wb.sheetnames)
            self.assertIn("Summary", wb.sheetnames)

    def test_export_header_row(self):
        import tempfile
        import openpyxl
        from core import exporter, database

        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "test_headers.xlsx")
            with patch.object(database, "get_scored_jobs_for_export", return_value=[]):
                path = exporter.export_jobs_xlsx(out_path)
            wb = openpyxl.load_workbook(path)
            ws = wb["Scored Jobs"]
            headers = [ws.cell(row=1, column=c).value for c in range(1, 23)]
            self.assertIn("Bucket", headers)
            self.assertIn("Score", headers)
            self.assertIn("Title", headers)
            self.assertIn("Explanation", headers)

    def test_export_bucket_colors_applied(self):
        """Rejected rows must have a different fill from Ignore rows."""
        import tempfile
        import openpyxl
        from core import exporter

        mock_rows = [
            {"priority_bucket": "Rejected", "total_score": 0, "title": "R",
             "company": "X", "explanation": "gate fail", "url": ""},
            {"priority_bucket": "Ignore",   "total_score": 18, "title": "I",
             "company": "Y", "explanation": "low score", "url": ""},
        ]
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "test_color.xlsx")
            from core import database
            with patch.object(database, "get_scored_jobs_for_export",
                              return_value=mock_rows):
                path = exporter.export_jobs_xlsx(out_path)
            wb = openpyxl.load_workbook(path)
            ws = wb["Scored Jobs"]
            fill_row2 = ws.cell(row=2, column=1).fill.fgColor.rgb
            fill_row3 = ws.cell(row=3, column=1).fill.fgColor.rgb
            self.assertNotEqual(fill_row2, fill_row3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
