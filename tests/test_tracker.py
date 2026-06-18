"""Tests for the application tracker (core/tracker.py) — the job-application CRM."""
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from core import database, tracker


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, company TEXT, location TEXT, url TEXT,
            fetched_date DATE DEFAULT (DATE('now'))
        );
        CREATE TABLE scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER, total_score INTEGER, priority_bucket TEXT, explanation TEXT
        );
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER, status TEXT DEFAULT 'Found', notes TEXT,
            next_action TEXT, next_action_date DATE, contact_name TEXT,
            outcome TEXT, last_updated DATETIME DEFAULT (DATETIME('now'))
        );
    """)
    for i, (title, co, score) in enumerate([
        ("AI PM", "Acme", 92), ("Strategy", "Beta", 78), ("HR", "Gamma", 55),
    ], start=1):
        conn.execute("INSERT INTO jobs (id,title,company,location,url) VALUES (?,?,?,?,?)",
                     (i, title, co, "Milan", f"http://x/{i}"))
        conn.execute("INSERT INTO scores (job_id,total_score,priority_bucket) VALUES (?,?,?)",
                     (i, score, "High Priority"))
    conn.commit()
    return conn


class TrackerTestBase(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()

        @contextmanager
        def _ctx():
            yield self.conn
            self.conn.commit()

        self._patch = patch("core.database.get_connection", _ctx)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.conn.close()


class TestStatuses(TrackerTestBase):
    def test_status_list_and_default(self):
        self.assertEqual(tracker.STATUSES[0], "Not Started")
        self.assertIn("Offer", tracker.STATUSES)
        self.assertEqual(len(tracker.STATUSES), 8)
        self.assertEqual(tracker.DEFAULT_STATUS, "Not Started")

    def test_is_valid_status(self):
        self.assertTrue(tracker.is_valid_status("Interview"))
        self.assertFalse(tracker.is_valid_status("Ghosted"))

    def test_invalid_status_raises(self):
        with self.assertRaises(tracker.InvalidStatusError):
            tracker.set_status(1, "Ghosted")


class TestTrackerOps(TrackerTestBase):
    def test_ensure_tracked_creates_then_idempotent(self):
        self.assertTrue(tracker.ensure_tracked(1))
        self.assertFalse(tracker.ensure_tracked(1))  # already exists
        app = database.get_application(1)
        self.assertEqual(app["status"], "Not Started")

    def test_set_status_creates_and_updates(self):
        tracker.set_status(1, "Applied", notes="submitted via portal")
        app = database.get_application(1)
        self.assertEqual(app["status"], "Applied")
        self.assertEqual(app["notes"], "submitted via portal")

    def test_set_status_progression(self):
        tracker.set_status(2, "Saved")
        tracker.set_status(2, "Interview")
        self.assertEqual(database.get_application(2)["status"], "Interview")

    def test_ensure_rows_for_scored(self):
        created = tracker.ensure_rows_for_scored()
        self.assertEqual(created, 3)        # one per scored job
        self.assertEqual(tracker.ensure_rows_for_scored(), 0)  # idempotent

    def test_list_and_counts(self):
        tracker.set_status(1, "Applied")
        tracker.ensure_tracked(2)
        apps = tracker.list_applications()
        self.assertGreaterEqual(len(apps), 2)
        # Ordered by score desc → job 1 (92) before job 2 (78)
        self.assertEqual(apps[0]["job_id"], 1)
        counts = tracker.status_counts()
        self.assertEqual(counts["Applied"], 1)
        self.assertEqual(counts["Not Started"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
