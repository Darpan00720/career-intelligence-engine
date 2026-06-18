"""Backfill service tests — real row copy / count / checksum against SQLite."""
import sqlite3
import unittest

from core.backfill_service import PartitionBackfillService


class TestBackfillService(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE src (id INTEGER PRIMARY KEY, tenant_id TEXT, val TEXT);
            CREATE TABLE dst (id INTEGER PRIMARY KEY, tenant_id TEXT, val TEXT);
        """)
        for i in range(1, 2501):   # 2500 rows → exercises multi-batch keyset copy
            self.conn.execute("INSERT INTO src (id, tenant_id, val) VALUES (?, ?, ?)",
                              (i, "acme" if i % 2 else "beta", f"v{i}"))
        self.conn.commit()
        self.svc = PartitionBackfillService(placeholder="?", batch_size=1000)

    def tearDown(self):
        self.conn.close()

    def test_backfill_copies_all_rows(self):
        copied = self.svc.backfill(self.conn, "src", "dst", ["tenant_id", "val"])
        self.assertEqual(copied, 2500)
        self.assertEqual(self.svc.row_count(self.conn, "dst"), 2500)

    def test_verify_counts_and_checksum_match(self):
        self.svc.backfill(self.conn, "src", "dst", ["tenant_id", "val"])
        result = self.svc.verify(self.conn, "src", "dst", ["tenant_id", "val"])
        self.assertTrue(result["counts"]["match"])
        self.assertTrue(result["checksum"]["match"])
        self.assertTrue(result["ok"])

    def test_checksum_detects_divergence(self):
        self.svc.backfill(self.conn, "src", "dst", ["tenant_id", "val"])
        self.conn.execute("UPDATE dst SET val = 'tampered' WHERE id = 1")
        self.conn.commit()
        checks = self.svc.verify_checksum(self.conn, "src", "dst", ["tenant_id", "val"])
        self.assertFalse(checks["match"])

    def test_count_mismatch_detected(self):
        self.svc.backfill(self.conn, "src", "dst", ["tenant_id", "val"])
        self.conn.execute("DELETE FROM dst WHERE id = 5")
        self.conn.commit()
        self.assertFalse(self.svc.verify_counts(self.conn, "src", "dst")["match"])

    def test_rollback_clears_target(self):
        self.svc.backfill(self.conn, "src", "dst", ["tenant_id", "val"])
        self.svc.rollback(self.conn, "dst")
        self.assertEqual(self.svc.row_count(self.conn, "dst"), 0)

    def test_filtered_backfill(self):
        copied = self.svc.backfill(self.conn, "src", "dst", ["tenant_id", "val"],
                                   where="tenant_id = 'acme'")
        self.assertEqual(copied, 1250)


if __name__ == "__main__":
    unittest.main(verbosity=2)
