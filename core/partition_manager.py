"""Partitioned event platform (v5.2.1).

Declarative RANGE(created_at) monthly partitioning for the high-volume tables
(events_log, workflow_events, audit_log, llm_costs). Split into:

  * PartitionDDLGenerator   — pure SQL/string generation (no I/O; fully tested)
  * PartitionManager        — plan + create the partitions a date range needs
  * PartitionRetentionManager — compute + drop/detach partitions past retention

The planning/DDL logic is pure and unit-tested against SQLite; the actual
PARTITION-OF execution runs on PostgreSQL (Testcontainers integration tests).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# Tables converted to monthly RANGE partitioning.
PARTITIONED_TABLES = ("events_log", "workflow_events", "audit_log", "llm_costs")

_SUFFIX_RE = re.compile(r"_(\d{4})_(\d{2})$")


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    """Inclusive list of (year, month) from start's month to end's month."""
    if end < start:
        return []
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = _next_month(y, m)
    return out


class PartitionDDLGenerator:
    """Pure generator of partition DDL — no database access."""

    @staticmethod
    def partition_name(parent: str, year: int, month: int) -> str:
        return f"{parent}_{year:04d}_{month:02d}"

    @staticmethod
    def parse_partition_name(child: str) -> tuple[str, int, int] | None:
        m = _SUFFIX_RE.search(child)
        if not m:
            return None
        return child[: m.start()], int(m.group(1)), int(m.group(2))

    @staticmethod
    def create_partitioned_parent_sql(table: str, columns_sql: str,
                                      key: str = "created_at") -> str:
        return (f"CREATE TABLE IF NOT EXISTS {table} ({columns_sql}) "
                f"PARTITION BY RANGE ({key});")

    def create_partition_sql(self, parent: str, year: int, month: int) -> str:
        child = self.partition_name(parent, year, month)
        lo = _month_start(year, month)
        ny, nm = _next_month(year, month)
        hi = _month_start(ny, nm)
        return (f"CREATE TABLE IF NOT EXISTS {child} PARTITION OF {parent} "
                f"FOR VALUES FROM ('{lo.isoformat()}') TO ('{hi.isoformat()}');")

    @staticmethod
    def index_sql(parent: str, columns: list[str], method: str = "btree",
                  name: str | None = None) -> str:
        cols = ", ".join(columns)
        idx = name or f"idx_{parent}_{'_'.join(columns)}"
        using = f" USING {method}" if method and method != "btree" else ""
        return f"CREATE INDEX IF NOT EXISTS {idx} ON {parent}{using} ({cols});"

    @staticmethod
    def drop_partition_sql(child: str) -> str:
        return f"DROP TABLE IF EXISTS {child};"

    @staticmethod
    def detach_partition_sql(parent: str, child: str) -> str:
        # Detach for archival instead of destructive drop.
        return f"ALTER TABLE {parent} DETACH PARTITION {child};"


@dataclass
class PartitionManager:
    generator: PartitionDDLGenerator = field(default_factory=PartitionDDLGenerator)

    def required_partitions(self, start: date, end: date,
                            lookahead_months: int = 1) -> list[tuple[int, int]]:
        """Months that should have partitions for [start, end] + lookahead."""
        y, m = end.year, end.month
        for _ in range(max(0, lookahead_months)):
            y, m = _next_month(y, m)
        return months_between(start, _month_start(y, m))

    def ensure_sql(self, parent: str, start: date, end: date,
                   lookahead_months: int = 1) -> list[str]:
        """DDL (idempotent, IF NOT EXISTS) to create every needed partition."""
        return [self.generator.create_partition_sql(parent, y, m)
                for y, m in self.required_partitions(start, end, lookahead_months)]

    async def ensure(self, conn, parent: str, start: date, end: date,
                     lookahead_months: int = 1) -> int:  # pragma: no cover - needs PG
        """Execute partition creation on a live connection. Returns count."""
        statements = self.ensure_sql(parent, start, end, lookahead_months)
        for sql in statements:
            await conn.execute(sql)
        return len(statements)

    async def list_partitions(self, conn, parent: str) -> list[str]:  # pragma: no cover - needs PG
        rows = await conn.fetch(
            "SELECT inhrelid::regclass::text AS child FROM pg_inherits "
            "WHERE inhparent = $1::regclass ORDER BY child", parent)
        return [r["child"] for r in rows]


@dataclass
class PartitionRetentionManager:
    generator: PartitionDDLGenerator = field(default_factory=PartitionDDLGenerator)

    def expired_partitions(self, existing: list[str], retention_months: int,
                           now: date) -> list[str]:
        """Names of partitions whose month is older than the retention window."""
        # Cutoff = first day of the month `retention_months` before `now`.
        y, m = now.year, now.month
        for _ in range(retention_months):
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        cutoff = (y, m)
        expired = []
        for child in existing:
            parsed = self.generator.parse_partition_name(child)
            if parsed and (parsed[1], parsed[2]) < cutoff:
                expired.append(child)
        return expired

    def retention_sql(self, existing: list[str], retention_months: int, now: date,
                      archive: bool = False, parent: str | None = None) -> list[str]:
        stmts = []
        for child in self.expired_partitions(existing, retention_months, now):
            if archive and parent:
                stmts.append(self.generator.detach_partition_sql(parent, child))
            else:
                stmts.append(self.generator.drop_partition_sql(child))
        return stmts

    async def apply(self, conn, parent: str, retention_months: int, now: date,
                    archive: bool = False) -> int:  # pragma: no cover - needs PG
        existing = await PartitionManager().list_partitions(conn, parent)
        stmts = self.retention_sql(existing, retention_months, now, archive, parent)
        for sql in stmts:
            await conn.execute(sql)
        return len(stmts)
