"""HA / failover tests — circuit breaker transitions + read/write routing.

The breaker and routing logic are unit-tested with fakes (no infra). A real
primary-loss / replica-promotion simulation requires a live cluster and is
documented in docs/V521_POSTGRESQL_PLATFORM.md (Testcontainers-gated).
"""
import time
import unittest

from core.asyncpg_manager import PgCircuitBreaker, PoolExhausted


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_after_threshold(self):
        b = PgCircuitBreaker(threshold=3, cooldown=999)
        for _ in range(3):
            b.record_failure()
        self.assertEqual(b.state, b.OPEN)
        self.assertFalse(b.allow())

    def test_half_open_after_cooldown_then_close(self):
        b = PgCircuitBreaker(threshold=1, cooldown=0.05)
        b.record_failure()
        self.assertFalse(b.allow())          # open
        time.sleep(0.06)
        self.assertTrue(b.allow())           # half-open probe permitted
        self.assertEqual(b.state, b.HALF_OPEN)
        b.record_success()
        self.assertEqual(b.state, b.CLOSED)

    def test_success_resets_failures(self):
        b = PgCircuitBreaker(threshold=3)
        b.record_failure(); b.record_failure()
        b.record_success()
        self.assertEqual(b.failures, 0)
        self.assertEqual(b.state, b.CLOSED)


class _FakePool:
    def __init__(self, label):
        self.label = label
        self.acquired = 0
        self.released = 0

    async def acquire(self):
        self.acquired += 1
        return f"{self.label}-conn"

    async def release(self, conn):
        self.released += 1


class _RoutingManager:
    """Minimal stand-in exercising acquire(read=...) routing + breaker."""

    def __init__(self):
        self.breaker = PgCircuitBreaker()
        self._write_pool = _FakePool("primary")
        self._read_pool = _FakePool("replica")

    async def acquire(self, read=False):
        if not self.breaker.allow():
            raise PoolExhausted("circuit open")
        pool = self._read_pool if read else self._write_pool
        try:
            conn = await pool.acquire()
            self.breaker.record_success()
            return conn
        except Exception:
            self.breaker.record_failure()
            raise


class TestReadWriteRouting(unittest.IsolatedAsyncioTestCase):
    async def test_reads_hit_replica_writes_hit_primary(self):
        mgr = _RoutingManager()
        self.assertEqual(await mgr.acquire(read=False), "primary-conn")
        self.assertEqual(await mgr.acquire(read=True), "replica-conn")
        self.assertEqual(mgr._write_pool.acquired, 1)
        self.assertEqual(mgr._read_pool.acquired, 1)

    async def test_open_circuit_rejects_acquire(self):
        mgr = _RoutingManager()
        for _ in range(mgr.breaker.threshold):
            mgr.breaker.record_failure()
        with self.assertRaises(PoolExhausted):
            await mgr.acquire()


if __name__ == "__main__":
    unittest.main(verbosity=2)
