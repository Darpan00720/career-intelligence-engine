"""Migration framework tests — real apply/rollback against SQLite."""
import sqlite3
import unittest
from unittest.mock import patch

from core.feature_flags import FeatureFlagService
from core.pg_migration_service import (
    ExpandContractMigration,
    Migration,
    PgMigrationService,
)


class TestMigrationService(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.svc = PgMigrationService(placeholder="?")

    def tearDown(self):
        self.conn.close()

    def test_apply_and_idempotent(self):
        m = Migration(1, "create_widgets", "CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
        self.assertTrue(self.svc.apply(self.conn, m))
        self.assertFalse(self.svc.apply(self.conn, m))   # already applied
        self.assertEqual(self.svc.current_version(self.conn), 1)

    def test_version_ordering_apply_all(self):
        migs = [
            Migration(2, "b", "CREATE TABLE b (id INTEGER)"),
            Migration(1, "a", "CREATE TABLE a (id INTEGER)"),
            Migration(3, "c", "ALTER TABLE a ADD COLUMN x TEXT"),
        ]
        applied = self.svc.apply_all(self.conn, migs)
        self.assertEqual(applied, [1, 2, 3])   # applied in version order

    def test_rollback(self):
        m = Migration(1, "create_t", "CREATE TABLE t (id INTEGER)", down_sql="DROP TABLE t")
        self.svc.apply(self.conn, m)
        self.assertTrue(self.svc.rollback(self.conn, m))
        self.assertEqual(self.svc.current_version(self.conn), 0)
        # Re-applying after rollback works (table was dropped).
        self.assertTrue(self.svc.apply(self.conn, m))

    def test_feature_flagged_migration(self):
        with patch("core.tenancy.audit"):
            flags = FeatureFlagService()
            flags.register("mig.feature_x", default=False)
            m = Migration(5, "feature_x", "CREATE TABLE fx (id INTEGER)",
                          feature_flag="mig.feature_x")
            # Flag off → skipped.
            self.assertFalse(self.svc.apply(self.conn, m, flags=flags))
            self.assertEqual(self.svc.current_version(self.conn), 0)
            # Flag on → applied.
            flags.set_tenant_override("mig.feature_x", "default", True)
            self.assertTrue(self.svc.apply(self.conn, m, flags=flags))

    def test_expand_contract(self):
        with patch("core.tenancy.audit"):
            self.conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, old_col TEXT)")
            backfilled = {"done": False}

            def backfill(conn):
                conn.execute("UPDATE jobs SET new_col = old_col")
                backfilled["done"] = True

            ec = ExpandContractMigration(
                base_version=10, name="rename_col",
                expand_sql="ALTER TABLE jobs ADD COLUMN new_col TEXT",
                contract_sql="ALTER TABLE jobs DROP COLUMN old_col",
                backfill=backfill,
            )
            self.conn.execute("INSERT INTO jobs (id, old_col) VALUES (1, 'hello')")
            result = self.svc.run_expand_contract(self.conn, ec)
            self.assertTrue(result["expand"])
            self.assertTrue(result["backfilled"])
            self.assertTrue(result["contract"])
            self.assertEqual(backfilled["done"], True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
