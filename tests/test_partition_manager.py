"""Unit tests for the partition platform (pure DDL + planning logic)."""
import unittest
from datetime import date

from core.partition_manager import (
    PARTITIONED_TABLES,
    PartitionDDLGenerator,
    PartitionManager,
    PartitionRetentionManager,
    months_between,
)


class TestDDLGenerator(unittest.TestCase):
    def setUp(self):
        self.g = PartitionDDLGenerator()

    def test_partition_name_and_parse(self):
        name = self.g.partition_name("events_log", 2026, 1)
        self.assertEqual(name, "events_log_2026_01")
        self.assertEqual(self.g.parse_partition_name(name), ("events_log", 2026, 1))
        self.assertIsNone(self.g.parse_partition_name("no_suffix_table"))

    def test_create_partition_sql_month_bounds(self):
        sql = self.g.create_partition_sql("events_log", 2026, 1)
        self.assertIn("PARTITION OF events_log", sql)
        self.assertIn("FROM ('2026-01-01') TO ('2026-02-01')", sql)

    def test_create_partition_sql_december_rollover(self):
        sql = self.g.create_partition_sql("llm_costs", 2026, 12)
        self.assertIn("FROM ('2026-12-01') TO ('2027-01-01')", sql)

    def test_partitioned_parent_and_index(self):
        parent = self.g.create_partitioned_parent_sql(
            "events_log", "id BIGSERIAL, tenant_id TEXT, created_at TIMESTAMPTZ")
        self.assertIn("PARTITION BY RANGE (created_at)", parent)
        idx = self.g.index_sql("events_log", ["tenant_id", "created_at"])
        self.assertIn("(tenant_id, created_at)", idx)
        brin = self.g.index_sql("events_log", ["created_at"], method="brin")
        self.assertIn("USING brin", brin)


class TestMonthsBetween(unittest.TestCase):
    def test_inclusive_range(self):
        self.assertEqual(months_between(date(2026, 1, 15), date(2026, 3, 2)),
                         [(2026, 1), (2026, 2), (2026, 3)])

    def test_year_rollover(self):
        self.assertEqual(months_between(date(2025, 11, 1), date(2026, 1, 1)),
                         [(2025, 11), (2025, 12), (2026, 1)])

    def test_empty_when_reversed(self):
        self.assertEqual(months_between(date(2026, 3, 1), date(2026, 1, 1)), [])


class TestPartitionManager(unittest.TestCase):
    def setUp(self):
        self.pm = PartitionManager()

    def test_required_partitions_with_lookahead(self):
        req = self.pm.required_partitions(date(2026, 1, 1), date(2026, 2, 1), lookahead_months=1)
        self.assertEqual(req, [(2026, 1), (2026, 2), (2026, 3)])

    def test_ensure_sql_idempotent(self):
        stmts = self.pm.ensure_sql("audit_log", date(2026, 1, 1), date(2026, 1, 15))
        self.assertTrue(all("IF NOT EXISTS" in s for s in stmts))
        self.assertEqual(len(stmts), 2)   # Jan + 1 month lookahead


class TestRetention(unittest.TestCase):
    def setUp(self):
        self.rm = PartitionRetentionManager()
        self.existing = ["events_log_2025_09", "events_log_2025_10",
                         "events_log_2025_12", "events_log_2026_01"]

    def test_expired_partitions(self):
        # retention 3 months, now Feb 2026 → cutoff Nov 2025; older months expire.
        expired = self.rm.expired_partitions(self.existing, 3, date(2026, 2, 15))
        self.assertEqual(set(expired), {"events_log_2025_09", "events_log_2025_10"})

    def test_retention_sql_drop_vs_detach(self):
        drops = self.rm.retention_sql(self.existing, 3, date(2026, 2, 15))
        self.assertTrue(all(s.startswith("DROP TABLE") for s in drops))
        detaches = self.rm.retention_sql(self.existing, 3, date(2026, 2, 15),
                                         archive=True, parent="events_log")
        self.assertTrue(all("DETACH PARTITION" in s for s in detaches))

    def test_partitioned_tables_constant(self):
        self.assertIn("events_log", PARTITIONED_TABLES)
        self.assertIn("llm_costs", PARTITIONED_TABLES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
