"""Persistence abstraction (v5.1) — DatabaseManager / Repository / UnitOfWork.

A hexagonal data-access layer that decouples business logic from the storage
engine. Today it runs on SQLite (the default dialect); the same Repository /
UnitOfWork API runs on PostgreSQL by swapping the DatabaseManager's connector
(asyncpg/psycopg) — call sites do not change. Provides transactional boundaries,
tenant-aware repositories, and optimistic-locking updates.

Backward compatible: the legacy `database.get_connection()` API is untouched;
this layer is additive and opt-in.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Callable

from core import config

SQLITE = "sqlite"
POSTGRES = "postgres"


class StaleDataError(Exception):
    """Raised when an optimistic-locked update loses a version race."""


class DatabaseManager:
    """Owns connection configuration and hands out raw connections.

    The default connector opens SQLite connections from config.DB_PATH. Inject a
    `connect_fn` for tests (e.g. an in-memory connection) or to provide a
    PostgreSQL pool. `dialect` lets adapters branch on JSONB vs TEXT, etc.
    """

    def __init__(self, dialect: str = SQLITE, connect_fn: Callable[[], Any] | None = None):
        self.dialect = dialect
        self._connect_fn = connect_fn or self._default_sqlite_connect

    @staticmethod
    def _default_sqlite_connect() -> sqlite3.Connection:
        from pathlib import Path
        Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def connect(self):
        return self._connect_fn()

    @property
    def json_type(self) -> str:
        return "JSONB" if self.dialect == POSTGRES else "TEXT"


_manager: DatabaseManager | None = None


def get_db_manager() -> DatabaseManager:
    global _manager
    if _manager is None:
        import os
        _manager = DatabaseManager(dialect=os.getenv("DB_DIALECT", SQLITE))
    return _manager


def set_db_manager(manager: DatabaseManager) -> None:
    global _manager
    _manager = manager


class UnitOfWork:
    """A transactional boundary: one connection, commit on success / rollback on error.

        with UnitOfWork() as uow:
            repo = uow.repository(TenantAwareRepository, "jobs")
            repo.add({...})
        # committed here
    """

    def __init__(self, manager: DatabaseManager | None = None):
        self.manager = manager or get_db_manager()
        self.conn = None

    def __enter__(self) -> "UnitOfWork":
        self.conn = self.manager.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()

    def repository(self, repo_cls, *args, **kwargs):
        return repo_cls(self.conn, *args, **kwargs)


class Repository:
    """Generic table repository over a live connection (within a UnitOfWork)."""

    def __init__(self, conn, table: str):
        self.conn = conn
        self.table = table

    def _row(self, row) -> dict | None:
        return dict(row) if row is not None else None

    def add(self, data: dict) -> int:
        cols = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        cur = self.conn.execute(
            f"INSERT INTO {self.table} ({cols}) VALUES ({placeholders})",
            tuple(data.values()),
        )
        return cur.lastrowid

    def get(self, entity_id: int, id_col: str = "id") -> dict | None:
        row = self.conn.execute(
            f"SELECT * FROM {self.table} WHERE {id_col} = ?", (entity_id,)
        ).fetchone()
        return self._row(row)

    def list(self, where: dict | None = None, limit: int = 1000) -> list[dict]:
        where = where or {}
        clause = " AND ".join(f"{k} = ?" for k in where)
        sql = f"SELECT * FROM {self.table}"
        if clause:
            sql += f" WHERE {clause}"
        sql += " LIMIT ?"
        rows = self.conn.execute(sql, (*where.values(), limit)).fetchall()
        return [dict(r) for r in rows]

    def update(self, entity_id: int, data: dict, *, expected_version: int | None = None,
               id_col: str = "id") -> None:
        """Update a row. With expected_version, performs an optimistic-locked
        write (bumps `version`) and raises StaleDataError on a version mismatch."""
        sets = ", ".join(f"{k} = ?" for k in data)
        params = list(data.values())
        if expected_version is not None:
            sets += ", version = version + 1"
            sql = f"UPDATE {self.table} SET {sets} WHERE {id_col} = ? AND version = ?"
            params += [entity_id, expected_version]
        else:
            sql = f"UPDATE {self.table} SET {sets} WHERE {id_col} = ?"
            params += [entity_id]
        cur = self.conn.execute(sql, tuple(params))
        if expected_version is not None and cur.rowcount == 0:
            raise StaleDataError(
                f"{self.table}#{entity_id} changed underneath version {expected_version}")

    def delete(self, entity_id: int, id_col: str = "id") -> None:
        self.conn.execute(f"DELETE FROM {self.table} WHERE {id_col} = ?", (entity_id,))


class TenantAwareRepository(Repository):
    """Repository that enforces row-level tenant isolation automatically.

    add() injects the current tenant_id; get()/list() filter by it. This is the
    enforcement point for multi-tenant data isolation.
    """

    def __init__(self, conn, table: str, tenant_id: str | None = None):
        super().__init__(conn, table)
        from core.tenancy import current_tenant
        self.tenant_id = tenant_id or current_tenant()

    def add(self, data: dict) -> int:
        data = {**data, "tenant_id": self.tenant_id}
        return super().add(data)

    def get(self, entity_id: int, id_col: str = "id") -> dict | None:
        row = self.conn.execute(
            f"SELECT * FROM {self.table} WHERE {id_col} = ? AND tenant_id = ?",
            (entity_id, self.tenant_id),
        ).fetchone()
        return self._row(row)

    def list(self, where: dict | None = None, limit: int = 1000) -> list[dict]:
        return super().list({**(where or {}), "tenant_id": self.tenant_id}, limit)


@contextmanager
def unit_of_work(manager: DatabaseManager | None = None):
    """Functional helper: `with unit_of_work() as uow: ...`."""
    uow = UnitOfWork(manager)
    uow.__enter__()
    try:
        yield uow
    except Exception:
        uow.__exit__(Exception, None, None)
        raise
    else:
        uow.__exit__(None, None, None)
