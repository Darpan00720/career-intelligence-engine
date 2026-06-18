"""PostgreSQL adapter (v5.1) for the persistence port.

Builds a DatabaseManager whose connections come from a psycopg connection pool,
with the POSTGRES dialect (JSONB, RETURNING, etc.). `psycopg`/`psycopg_pool`
are optional and imported lazily — importing this module never requires them.

    from core.pg_adapter import build_pg_manager
    set_db_manager(build_pg_manager(os.environ["DATABASE_URL"]))

The Repository / UnitOfWork / TenantAwareRepository API is unchanged; only the
connection source differs. Partitioning, read replicas, and failover are
configured at the database/infra layer (see deploy/ and the runbook).
"""
from __future__ import annotations

from core.persistence import POSTGRES, DatabaseManager


def build_pg_pool(dsn: str, min_size: int = 2, max_size: int = 20):
    """Create a psycopg connection pool (lazy import)."""
    from psycopg_pool import ConnectionPool  # optional dependency
    return ConnectionPool(conninfo=dsn, min_size=min_size, max_size=max_size, open=True)


def build_pg_manager(dsn: str, *, min_size: int = 2, max_size: int = 20) -> DatabaseManager:
    pool = build_pg_pool(dsn, min_size, max_size)

    def _connect():
        # psycopg connections expose execute/commit/close like sqlite3; the
        # Repository SQL uses '?' placeholders, so wrap to translate to '%s'.
        conn = pool.getconn()
        return _PsycopgShim(conn, pool)

    return DatabaseManager(dialect=POSTGRES, connect_fn=_connect)


class _PsycopgShim:
    """Adapts a psycopg connection to the sqlite3-style API the repos expect,
    translating '?' placeholders to '%s' and returning dict-like rows."""

    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool

    def execute(self, sql: str, params: tuple = ()):  # pragma: no cover - needs PG
        cur = self._conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return _CursorShim(cur)

    def commit(self):  # pragma: no cover
        self._conn.commit()

    def rollback(self):  # pragma: no cover
        self._conn.rollback()

    def close(self):  # pragma: no cover
        self._pool.putconn(self._conn)


class _CursorShim:  # pragma: no cover - needs PG
    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = None
        self.rowcount = cur.rowcount

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._cur.description]
        return dict(zip(cols, row))

    def fetchall(self):
        cols = [d[0] for d in self._cur.description]
        return [dict(zip(cols, r)) for r in self._cur.fetchall()]
