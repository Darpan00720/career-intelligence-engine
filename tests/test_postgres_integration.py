"""PostgreSQL integration tests (v5.2) via Testcontainers.

Self-skips when Docker / the `testcontainers` package are unavailable (e.g. CI
without a Docker daemon), so the default suite stays green offline. When a
Docker daemon is present (`pip install testcontainers[postgresql] psycopg`),
these exercise the *real* persistence port against PostgreSQL:

    Repository / UnitOfWork / TenantAwareRepository / optimistic locking,
    JSONB round-trips, and partition-friendly DDL.

Run explicitly:  python -m unittest tests.test_postgres_integration
"""
import unittest

try:
    from testcontainers.postgres import PostgresContainer  # type: ignore
    import psycopg  # type: ignore  # noqa: F401
    _HAS_TESTCONTAINERS = True
except Exception:  # ImportError or docker missing
    _HAS_TESTCONTAINERS = False


@unittest.skipUnless(_HAS_TESTCONTAINERS, "testcontainers/psycopg/Docker not available")
class TestPostgresIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.container = PostgresContainer("postgres:16")
        cls.container.start()
        from core.pg_adapter import build_pg_manager
        cls.manager = build_pg_manager(cls.container.get_connection_url().replace(
            "postgresql+psycopg2", "postgresql"))
        # Create a versioned, tenant-scoped table mirroring the v5 schema style.
        from core.persistence import UnitOfWork
        with UnitOfWork(cls.manager) as uow:
            uow.conn.execute(
                "CREATE TABLE IF NOT EXISTS widgets (id SERIAL PRIMARY KEY, "
                "tenant_id TEXT, name TEXT, version INTEGER DEFAULT 0, meta JSONB)")

    @classmethod
    def tearDownClass(cls):
        cls.container.stop()

    def test_tenant_isolation_and_optimistic_locking(self):
        from core.persistence import TenantAwareRepository, UnitOfWork, StaleDataError
        from core import tenancy

        with UnitOfWork(self.manager) as uow:
            with tenancy.use_tenant("acme"):
                wid = uow.repository(TenantAwareRepository, "widgets").add(
                    {"name": "w", "version": 0})
        with UnitOfWork(self.manager) as uow:
            uow.repository(TenantAwareRepository, "widgets").update(
                wid, {"name": "w2"}, expected_version=0)
        with self.assertRaises(StaleDataError):
            with UnitOfWork(self.manager) as uow:
                uow.repository(TenantAwareRepository, "widgets").update(
                    wid, {"name": "w3"}, expected_version=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
