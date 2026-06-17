"""Foundation tests: SQLite + checkpoint DB initialization.

Per project constraint, write-tests use temp files only — never the live
career_agent.db.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from graph.checkpoint import init_checkpoint_db, langgraph_available


class TestSqliteInit(unittest.TestCase):
    def test_plain_sqlite_initializes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "foundation.db"
            conn = sqlite3.connect(str(db))
            try:
                ok = conn.execute("PRAGMA integrity_check;").fetchone()
                self.assertEqual(ok[0], "ok")
            finally:
                conn.close()
            self.assertTrue(db.exists())


class TestCheckpointInit(unittest.TestCase):
    def test_checkpoint_db_initializes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "nested" / "checkpoints.db"
            resolved = init_checkpoint_db(db)
            self.assertTrue(resolved.exists())
            # Verify it is a usable sqlite file.
            conn = sqlite3.connect(str(resolved))
            try:
                ok = conn.execute("PRAGMA integrity_check;").fetchone()
                self.assertEqual(ok[0], "ok")
            finally:
                conn.close()

    @unittest.skipUnless(langgraph_available(),
                         "langgraph checkpoint backend not installed")
    def test_checkpointer_factory_when_available(self):
        from graph.checkpoint import get_checkpointer
        with tempfile.TemporaryDirectory() as tmp:
            saver = get_checkpointer(Path(tmp) / "cp.db")
            self.assertIsNotNone(saver)
            saver.conn.close()  # R3: close the persistent connection


if __name__ == "__main__":
    unittest.main()
