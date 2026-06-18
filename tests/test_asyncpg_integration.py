"""asyncpg manager tests.

Two layers:
  * TestAsyncPgRepository — unit tests of the repository against a fake async
    connection; verifies generated SQL ($N placeholders), params, and result
    mapping WITHOUT a live database (always run).
  * TestAsyncPgLive — real PostgreSQL via Testcontainers; self-skips when
    Docker / asyncpg are unavailable.
"""
import unittest

from core.asyncpg_manager import (
    AsyncPgRepository,
    AsyncPgTenantAwareRepository,
    _rowcount,
    recommend_pool_sizing,
)
from core.persistence import StaleDataError

try:
    import asyncpg  # noqa: F401
    from testcontainers.postgres import PostgresContainer  # type: ignore
    _HAS_PG = True
except Exception:
    _HAS_PG = False


class FakeConn:
    """Records calls and returns canned results — stands in for AsyncPgConnection."""

    def __init__(self, rows=None, val=None, execute_result="UPDATE 1"):
        self.calls = []
        self._rows = rows or []
        self._val = val
        self._execute_result = execute_result

    async def fetch(self, sql, params=None):
        self.calls.append(("fetch", sql, params)); return self._rows

    async def fetchrow(self, sql, params=None):
        self.calls.append(("fetchrow", sql, params))
        return self._rows[0] if self._rows else None

    async def fetchval(self, sql, params=None):
        self.calls.append(("fetchval", sql, params)); return self._val

    async def execute(self, sql, params=None):
        self.calls.append(("execute", sql, params)); return self._execute_result

    async def executemany(self, sql, args):
        self.calls.append(("executemany", sql, args))

    async def copy_records(self, table, columns, records):
        self.calls.append(("copy", table, columns, records))


class TestPoolSizing(unittest.TestCase):
    def test_tiers(self):
        small = recommend_pool_sizing("small")
        ent = recommend_pool_sizing("enterprise", cpu_cores=16, api_replicas=10)
        self.assertLess(small["pool_max"], ent["pool_max"])
        self.assertGreaterEqual(ent["recommended_pg_max_connections"],
                                10 * ent["pool_max"])

    def test_rowcount_parse(self):
        self.assertEqual(_rowcount("UPDATE 3"), 3)
        self.assertEqual(_rowcount("DELETE 0"), 0)
        self.assertEqual(_rowcount("garbage"), 0)


class TestAsyncPgRepository(unittest.IsolatedAsyncioTestCase):
    async def test_add_uses_returning(self):
        conn = FakeConn(val=42)
        repo = AsyncPgRepository(conn, "jobs")
        new_id = await repo.add({"title": "PM", "company": "Acme"})
        self.assertEqual(new_id, 42)
        kind, sql, params = conn.calls[0]
        self.assertEqual(kind, "fetchval")
        self.assertIn("INSERT INTO jobs", sql)
        self.assertIn("RETURNING id", sql)
        self.assertIn("$1, $2", sql)
        self.assertEqual(params, ["PM", "Acme"])

    async def test_get_and_list(self):
        conn = FakeConn(rows=[{"id": 1, "title": "X"}])
        repo = AsyncPgRepository(conn, "jobs")
        self.assertEqual((await repo.get(1))["id"], 1)
        await repo.list({"company": "Acme"}, limit=10)
        _, sql, params = conn.calls[-1]
        self.assertIn("WHERE company = $1", sql)
        self.assertEqual(params, ["Acme", 10])

    async def test_optimistic_update_raises_on_stale(self):
        conn = FakeConn(execute_result="UPDATE 0")
        repo = AsyncPgRepository(conn, "jobs")
        with self.assertRaises(StaleDataError):
            await repo.update(1, {"title": "Y"}, expected_version=3)

    async def test_batch_and_copy(self):
        conn = FakeConn()
        repo = AsyncPgRepository(conn, "events_log")
        n = await repo.batch_add(["tenant_id", "type"], [("acme", "A"), ("acme", "B")])
        self.assertEqual(n, 2)
        self.assertEqual(conn.calls[-1][0], "executemany")
        m = await repo.copy_insert(["tenant_id", "type"], [("acme", "A")])
        self.assertEqual(m, 1)
        self.assertEqual(conn.calls[-1][0], "copy")

    async def test_keyset_pagination_and_stream(self):
        # Two batches then empty → stream stops.
        batches = [[{"id": 1}, {"id": 2}], [{"id": 3}], []]
        class PagingConn(FakeConn):
            async def fetch(self, sql, params=None):
                self.calls.append(("fetch", sql, params))
                return batches.pop(0)
        repo = AsyncPgRepository(PagingConn(), "events_log")
        seen = []
        async for batch in repo.stream(batch_size=2):
            seen.extend(r["id"] for r in batch)
        self.assertEqual(seen, [1, 2, 3])

    async def test_tenant_aware_injects_tenant(self):
        from core import tenancy
        conn = FakeConn(val=1)
        with tenancy.use_tenant("acme"):
            repo = AsyncPgTenantAwareRepository(conn, "jobs")
            await repo.add({"title": "PM"})
        _, sql, params = conn.calls[0]
        self.assertIn("tenant_id", sql)
        self.assertIn("acme", params)


@unittest.skipUnless(_HAS_PG, "asyncpg/testcontainers/Docker not available")
class TestAsyncPgLive(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.container = PostgresContainer("postgres:16")
        cls.container.start()
        cls.dsn = cls.container.get_connection_url().replace(
            "postgresql+psycopg2", "postgresql")

    @classmethod
    def tearDownClass(cls):
        cls.container.stop()

    async def test_real_roundtrip(self):
        from core.asyncpg_manager import AsyncPgDatabaseManager, AsyncPgUnitOfWork
        mgr = await AsyncPgDatabaseManager(self.dsn, min_size=1, max_size=4).start()
        try:
            async with AsyncPgUnitOfWork(mgr) as uow:
                await uow.conn.execute(
                    "CREATE TABLE IF NOT EXISTS t (id SERIAL PRIMARY KEY, "
                    "tenant_id TEXT, name TEXT)")
            async with AsyncPgUnitOfWork(mgr) as uow:
                repo = uow.repository(AsyncPgRepository, "t")
                rid = await repo.add({"tenant_id": "acme", "name": "w"})
                row = await repo.get(rid)
                self.assertEqual(row["name"], "w")
            self.assertTrue(await mgr.health_check())
        finally:
            await mgr.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
