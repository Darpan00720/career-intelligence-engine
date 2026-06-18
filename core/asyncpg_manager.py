"""Production asyncpg persistence (v5.2.1).

Real PostgreSQL integration behind the v5.x persistence ports:
AsyncPgDatabaseManager / AsyncPgConnection / AsyncPgUnitOfWork /
AsyncPgRepository / AsyncPgTenantAwareRepository.

Features: asyncpg connection pools (write primary + read replicas), statement
caching, command timeouts, connection recycling, read/write splitting, a circuit
breaker, batch/COPY ingestion, keyset (cursor) pagination, and streaming.
`asyncpg` is imported lazily so this module imports without it; the SQL-building
and pool-sizing/circuit-breaker logic is pure and unit-tested, while live
execution is covered by Testcontainers integration tests.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

# ── Pool sizing strategy ────────────────────────────────────────────────────────

def recommend_pool_sizing(deployment: str = "medium", *, cpu_cores: int = 4,
                          api_replicas: int = 3) -> dict:
    """Recommend pool + PgBouncer settings per deployment tier.

    Rule of thumb: app pool per replica small (PgBouncer multiplexes); Postgres
    max_connections ≈ (api_replicas * pool_max) + workers + headroom, kept well
    under what the box can serve (≈ 4 × cores for transaction-bound loads).
    """
    presets = {
        "small":      {"pool_min": 2,  "pool_max": 10,  "pgbouncer_pool": 20,  "pg_max_connections": 100},
        "medium":     {"pool_min": 5,  "pool_max": 20,  "pgbouncer_pool": 50,  "pg_max_connections": 200},
        "enterprise": {"pool_min": 10, "pool_max": 40,  "pgbouncer_pool": 200, "pg_max_connections": 500},
    }
    cfg = dict(presets.get(deployment, presets["medium"]))
    cfg["recommended_pg_max_connections"] = max(
        cfg["pg_max_connections"], api_replicas * cfg["pool_max"] + 4 * cpu_cores + 20)
    cfg["deployment"] = deployment
    return cfg


# ── Circuit breaker ───────────────────────────────────────────────────────────

class PgCircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, threshold: int = 5, cooldown: float = 10.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.state = self.CLOSED
        self.opened_at = 0.0

    def allow(self) -> bool:
        if self.state == self.OPEN:
            if time.time() - self.opened_at >= self.cooldown:
                self.state = self.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.state = self.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.state = self.OPEN
            self.opened_at = time.time()


class PoolExhausted(RuntimeError):
    pass


# ── SQL builders (pure — unit tested) ───────────────────────────────────────────

def _ph(n: int, start: int = 1) -> str:
    return ", ".join(f"${i}" for i in range(start, start + n))


class _SqlBuilder:
    def __init__(self, table: str):
        self.table = table

    def insert(self, data: dict) -> tuple[str, list]:
        cols = ", ".join(data)
        return (f"INSERT INTO {self.table} ({cols}) VALUES ({_ph(len(data))}) "
                f"RETURNING id", list(data.values()))

    def get(self, entity_id, id_col="id") -> tuple[str, list]:
        return f"SELECT * FROM {self.table} WHERE {id_col} = $1", [entity_id]

    def select(self, where: dict, limit: int) -> tuple[str, list]:
        params = list(where.values())
        sql = f"SELECT * FROM {self.table}"
        if where:
            clause = " AND ".join(f"{k} = ${i+1}" for i, k in enumerate(where))
            sql += f" WHERE {clause}"
        sql += f" LIMIT ${len(params) + 1}"
        return sql, params + [limit]

    def paginate(self, after_id: int, limit: int, id_col="id") -> tuple[str, list]:
        return (f"SELECT * FROM {self.table} WHERE {id_col} > $1 "
                f"ORDER BY {id_col} LIMIT $2", [after_id, limit])

    def update(self, entity_id, data: dict, expected_version=None, id_col="id") -> tuple[str, list]:
        sets = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(data))
        params = list(data.values())
        if expected_version is not None:
            sets += ", version = version + 1"
            sql = (f"UPDATE {self.table} SET {sets} WHERE {id_col} = ${len(params)+1} "
                   f"AND version = ${len(params)+2}")
            return sql, params + [entity_id, expected_version]
        return (f"UPDATE {self.table} SET {sets} WHERE {id_col} = ${len(params)+1}",
                params + [entity_id])


# ── Connection / repository / UoW (asyncpg execution) ───────────────────────────

class AsyncPgConnection:  # pragma: no cover - exercised via Testcontainers
    """Thin async facade over an asyncpg connection; rows returned as dicts."""

    def __init__(self, raw):
        self._raw = raw

    async def execute(self, sql: str, params: list | None = None):
        return await self._raw.execute(sql, *(params or []))

    async def fetch(self, sql: str, params: list | None = None) -> list[dict]:
        rows = await self._raw.fetch(sql, *(params or []))
        return [dict(r) for r in rows]

    async def fetchrow(self, sql: str, params: list | None = None) -> dict | None:
        row = await self._raw.fetchrow(sql, *(params or []))
        return dict(row) if row is not None else None

    async def fetchval(self, sql: str, params: list | None = None):
        return await self._raw.fetchval(sql, *(params or []))

    async def executemany(self, sql: str, args_list: list[list]):
        return await self._raw.executemany(sql, args_list)

    async def copy_records(self, table: str, columns: list[str], records: list[tuple]):
        return await self._raw.copy_records_to_table(
            table, records=records, columns=columns)


class AsyncPgRepository:
    """Repository over an AsyncPgConnection. SQL building is pure + testable."""

    def __init__(self, conn, table: str):
        self.conn = conn
        self.sql = _SqlBuilder(table)
        self.table = table

    async def add(self, data: dict):
        sql, params = self.sql.insert(data)
        return await self.conn.fetchval(sql, params)

    async def get(self, entity_id, id_col="id"):
        sql, params = self.sql.get(entity_id, id_col)
        return await self.conn.fetchrow(sql, params)

    async def list(self, where: dict | None = None, limit: int = 1000):
        sql, params = self.sql.select(where or {}, limit)
        return await self.conn.fetch(sql, params)

    async def update(self, entity_id, data: dict, *, expected_version=None, id_col="id"):
        sql, params = self.sql.update(entity_id, data, expected_version, id_col)
        result = await self.conn.execute(sql, params)
        if expected_version is not None and _rowcount(result) == 0:
            from core.persistence import StaleDataError
            raise StaleDataError(f"{self.table}#{entity_id} stale @ v{expected_version}")

    # ── High-throughput ops ─────────────────────────────────────────────────────
    async def batch_add(self, columns: list[str], rows: list[tuple]) -> int:
        sql = f"INSERT INTO {self.table} ({', '.join(columns)}) VALUES ({_ph(len(columns))})"
        await self.conn.executemany(sql, [list(r) for r in rows])
        return len(rows)

    async def copy_insert(self, columns: list[str], records: list[tuple]) -> int:
        """Fastest bulk path: COPY (server-side, minimal round-trips)."""
        await self.conn.copy_records(self.table, columns, records)
        return len(records)

    async def paginate(self, after_id: int = 0, limit: int = 1000, id_col="id") -> list[dict]:
        sql, params = self.sql.paginate(after_id, limit, id_col)
        return await self.conn.fetch(sql, params)

    async def stream(self, batch_size: int = 1000, id_col="id"):
        """Async generator yielding keyset batches (constant memory)."""
        last = 0
        while True:
            batch = await self.paginate(last, batch_size, id_col)
            if not batch:
                return
            yield batch
            last = batch[-1][id_col]


class AsyncPgTenantAwareRepository(AsyncPgRepository):
    def __init__(self, conn, table: str, tenant_id: str | None = None):
        super().__init__(conn, table)
        from core.tenancy import current_tenant
        self.tenant_id = tenant_id or current_tenant()

    async def add(self, data: dict):
        return await super().add({**data, "tenant_id": self.tenant_id})

    async def list(self, where: dict | None = None, limit: int = 1000):
        return await super().list({**(where or {}), "tenant_id": self.tenant_id}, limit)


def _rowcount(execute_result) -> int:
    # asyncpg returns e.g. "UPDATE 1"; parse the trailing integer.
    try:
        return int(str(execute_result).split()[-1])
    except (ValueError, IndexError):
        return 0


class AsyncPgUnitOfWork:  # pragma: no cover - exercised via Testcontainers
    def __init__(self, manager: "AsyncPgDatabaseManager", read_only: bool = False):
        self.manager = manager
        self.read_only = read_only
        self._raw = None
        self._tx = None
        self.conn: AsyncPgConnection | None = None

    async def __aenter__(self):
        self._raw = await self.manager.acquire(read=self.read_only)
        self.conn = AsyncPgConnection(self._raw)
        if not self.read_only:
            self._tx = self._raw.transaction()
            await self._tx.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._tx is not None:
                await (self._tx.rollback() if exc_type else self._tx.commit())
        finally:
            await self.manager.release(self._raw, read=self.read_only)

    def repository(self, repo_cls, *args, **kwargs):
        return repo_cls(self.conn, *args, **kwargs)


class AsyncPgDatabaseManager:  # pragma: no cover - requires asyncpg + live PG
    """Owns write (primary) and read (replica) asyncpg pools."""

    def __init__(self, dsn: str, replica_dsn: str | None = None, *,
                 min_size: int = 5, max_size: int = 20,
                 statement_cache_size: int = 100, command_timeout: float = 30.0,
                 max_inactive_connection_lifetime: float = 300.0,
                 breaker: PgCircuitBreaker | None = None):
        self.dsn = dsn
        self.replica_dsn = replica_dsn
        self.min_size, self.max_size = min_size, max_size
        self.statement_cache_size = statement_cache_size
        self.command_timeout = command_timeout
        self.max_inactive = max_inactive_connection_lifetime
        self.breaker = breaker or PgCircuitBreaker()
        self._write_pool = None
        self._read_pool = None

    async def start(self):
        import asyncpg
        kwargs = dict(min_size=self.min_size, max_size=self.max_size,
                      statement_cache_size=self.statement_cache_size,
                      command_timeout=self.command_timeout,
                      max_inactive_connection_lifetime=self.max_inactive)
        self._write_pool = await asyncpg.create_pool(self.dsn, **kwargs)
        self._read_pool = (await asyncpg.create_pool(self.replica_dsn, **kwargs)
                           if self.replica_dsn else self._write_pool)
        return self

    async def acquire(self, read: bool = False):
        if not self.breaker.allow():
            raise PoolExhausted("DB circuit open")
        pool = self._read_pool if read else self._write_pool
        try:
            conn = await pool.acquire()
            self.breaker.record_success()
            return conn
        except Exception:
            self.breaker.record_failure()
            raise

    async def release(self, conn, read: bool = False):
        pool = self._read_pool if read else self._write_pool
        await pool.release(conn)

    async def health_check(self) -> bool:
        try:
            conn = await self.acquire()
            try:
                return (await conn.fetchval("SELECT 1")) == 1
            finally:
                await self.release(conn)
        except Exception:
            return False

    def pool_stats(self) -> dict:
        from core.metrics import set_db_pool
        stats = {}
        for name, pool in (("primary", self._write_pool), ("replica", self._read_pool)):
            if pool is not None:
                size, free = pool.get_size(), pool.get_idle_size()
                stats[name] = {"size": size, "idle": free, "active": size - free}
                set_db_pool(size - free, free, pool=name)
        return stats

    async def close(self):
        if self._write_pool:
            await self._write_pool.close()
        if self._read_pool and self._read_pool is not self._write_pool:
            await self._read_pool.close()
