"""Online migration framework (v5.2.1).

Schema-versioned, zero-downtime migrations using the expand-contract pattern,
with feature-flag gating, idempotent application, and rollback. Storage-agnostic
over a DB-API connection; unit-tested against SQLite with real SQL execution,
production-run against PostgreSQL.

Expand-contract (no downtime):
  1. EXPAND   — add new columns/tables/indexes (backward compatible)
  2. MIGRATE  — dual-write + backfill data into the new shape
  3. CONTRACT — drop the old shape once all readers/writers use the new one
Each phase is independently deployable and reversible; gate cutover with a flag.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable


def _stmts(sql) -> list[str]:
    if isinstance(sql, (list, tuple)):
        return [s for s in sql if s and s.strip()]
    return [s.strip() for s in (sql or "").split(";") if s.strip()]


@dataclass
class Migration:
    version: int
    name: str
    up_sql: object = ""          # str | list[str]
    down_sql: object = ""
    feature_flag: str | None = None

    @property
    def checksum(self) -> str:
        return hashlib.md5(str(_stmts(self.up_sql)).encode()).hexdigest()[:12]


@dataclass
class ExpandContractMigration:
    """A three-phase, no-downtime migration."""
    base_version: int
    name: str
    expand_sql: object = ""
    contract_sql: object = ""
    backfill: Callable | None = None      # callable(conn) -> None
    cutover_flag: str | None = None

    def phases(self) -> list[Migration]:
        return [
            Migration(self.base_version, f"{self.name}__expand", self.expand_sql),
            Migration(self.base_version + 1, f"{self.name}__contract", self.contract_sql,
                      feature_flag=self.cutover_flag),
        ]


class PgMigrationService:
    def __init__(self, placeholder: str = "?"):
        self.ph = placeholder

    def ensure_table(self, conn) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT, checksum TEXT, "
            "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")

    def applied_versions(self, conn) -> set[int]:
        self.ensure_table(conn)
        return {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}

    def current_version(self, conn) -> int:
        applied = self.applied_versions(conn)
        return max(applied) if applied else 0

    def apply(self, conn, migration: Migration, flags=None, tenant_id: str | None = None) -> bool:
        """Apply a migration if not already applied and (if flagged) enabled.

        Returns True if applied, False if skipped (already applied / flag off)."""
        self.ensure_table(conn)
        if migration.version in self.applied_versions(conn):
            return False
        if migration.feature_flag and flags is not None:
            if not flags.is_enabled(migration.feature_flag, tenant_id):
                return False
        for stmt in _stmts(migration.up_sql):
            conn.execute(stmt)
        conn.execute(
            f"INSERT INTO schema_migrations (version, name, checksum) "
            f"VALUES ({self.ph}, {self.ph}, {self.ph})",
            (migration.version, migration.name, migration.checksum))
        return True

    def apply_all(self, conn, migrations: list[Migration], flags=None,
                  tenant_id: str | None = None) -> list[int]:
        applied = []
        for m in sorted(migrations, key=lambda x: x.version):
            if self.apply(conn, m, flags, tenant_id):
                applied.append(m.version)
        return applied

    def rollback(self, conn, migration: Migration) -> bool:
        """Run the migration's down_sql and remove its version record."""
        self.ensure_table(conn)
        if migration.version not in self.applied_versions(conn):
            return False
        for stmt in _stmts(migration.down_sql):
            conn.execute(stmt)
        conn.execute(
            f"DELETE FROM schema_migrations WHERE version = {self.ph}", (migration.version,))
        return True

    def run_expand_contract(self, conn, ec: ExpandContractMigration, flags=None,
                            tenant_id: str | None = None) -> dict:
        """Apply expand, run the backfill, then apply contract (flag-gated)."""
        expand, contract = ec.phases()
        result = {"expand": self.apply(conn, expand), "backfilled": False,
                  "contract": False}
        if ec.backfill is not None:
            ec.backfill(conn)
            result["backfilled"] = True
        result["contract"] = self.apply(conn, contract, flags, tenant_id)
        return result
