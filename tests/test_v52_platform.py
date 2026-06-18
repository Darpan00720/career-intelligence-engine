"""Tests for v5.2 upgrades:

  async runtime (repo/UoW/optimistic), async DAG workflow (parallel, timeout,
  cancellation, compensation, retries), async worker runtime, feature flags &
  experiments, LLM v3 (budgets, adaptive routing, embedding cache, provider
  stats), Temporal local runtime, and backward compatibility.
"""
import asyncio
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from core.async_runtime import (
    AsyncConnection,
    AsyncDatabaseManager,
    AsyncRepository,
    AsyncTenantAwareRepository,
    AsyncUnitOfWork,
)
from core.async_workflow import AsyncWorkerRuntime, AsyncWorkflowEngine
from core.feature_flags import FeatureFlagService
from core.persistence import StaleDataError
from core.workflow_dag import DependencyGraph, WorkflowNode
from core.workflow_engine import CANCELLED, COMPLETED, FAILED, PARTIAL_SUCCESS
from integrations.temporal_adapter import (
    InMemoryPersistence,
    LocalTemporalRuntime,
)
from llm_gateway.budget import (
    AdaptiveRouter,
    BudgetExceeded,
    BudgetManager,
    EmbeddingCache,
    ProviderStats,
)


# ── Async runtime ──────────────────────────────────────────────────────────────

class TestAsyncRuntime(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        c = sqlite3.connect(self.path)
        c.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, tenant_id TEXT, "
                  "name TEXT, version INTEGER DEFAULT 0)")
        c.commit(); c.close()

        async def _connect():
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return AsyncConnection(conn)

        self.mgr = AsyncDatabaseManager(connect_fn=_connect)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    async def test_async_crud(self):
        async with AsyncUnitOfWork(self.mgr) as uow:
            repo = uow.repository(AsyncRepository, "widgets")
            wid = await repo.add({"name": "w", "tenant_id": "default"})
        async with AsyncUnitOfWork(self.mgr) as uow:
            got = await uow.repository(AsyncRepository, "widgets").get(wid)
        self.assertEqual(got["name"], "w")

    async def test_async_rollback(self):
        try:
            async with AsyncUnitOfWork(self.mgr) as uow:
                await uow.repository(AsyncRepository, "widgets").add(
                    {"name": "x", "tenant_id": "default"})
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        async with AsyncUnitOfWork(self.mgr) as uow:
            rows = await uow.repository(AsyncRepository, "widgets").list()
        self.assertEqual(rows, [])

    async def test_async_tenant_isolation(self):
        from core import tenancy
        async with AsyncUnitOfWork(self.mgr) as uow:
            await uow.repository(AsyncTenantAwareRepository, "widgets", "acme").add({"name": "a"})
            await uow.repository(AsyncTenantAwareRepository, "widgets", "beta").add({"name": "b"})
        async with AsyncUnitOfWork(self.mgr) as uow:
            rows = await uow.repository(AsyncTenantAwareRepository, "widgets", "acme").list()
        self.assertEqual([r["name"] for r in rows], ["a"])

    async def test_async_optimistic_locking(self):
        async with AsyncUnitOfWork(self.mgr) as uow:
            wid = await uow.repository(AsyncRepository, "widgets").add(
                {"name": "w", "tenant_id": "default", "version": 0})
        async with AsyncUnitOfWork(self.mgr) as uow:
            await uow.repository(AsyncRepository, "widgets").update(
                wid, {"name": "w2"}, expected_version=0)
        with self.assertRaises(StaleDataError):
            async with AsyncUnitOfWork(self.mgr) as uow:
                await uow.repository(AsyncRepository, "widgets").update(
                    wid, {"name": "w3"}, expected_version=0)


# ── Async workflow ─────────────────────────────────────────────────────────────

class TestAsyncWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_dag(self):
        g = DependencyGraph([
            WorkflowNode("root", lambda c: {"seed": 1}),
            WorkflowNode("x", lambda c: {"x": c["seed"]}, depends_on=["root"]),
            WorkflowNode("y", lambda c: {"y": c["seed"]}, depends_on=["root"]),
            WorkflowNode("join", lambda c: {"sum": c["x"] + c["y"]}, depends_on=["x", "y"]),
        ])
        self.assertEqual(await AsyncWorkflowEngine().run(g, mode="parallel"), COMPLETED)

    async def test_async_node(self):
        async def aio_node(ctx):
            await asyncio.sleep(0)
            return {"done": True}
        g = DependencyGraph([WorkflowNode("a", aio_node)])
        self.assertEqual(await AsyncWorkflowEngine().run(g), COMPLETED)

    async def test_timeout(self):
        async def slow(ctx):
            await asyncio.sleep(0.5)
            return {}
        g = DependencyGraph([WorkflowNode("slow", slow, timeout=0.05, retries=0)])
        self.assertEqual(await AsyncWorkflowEngine().run(g), PARTIAL_SUCCESS)

    async def test_cancellation(self):
        eng = AsyncWorkflowEngine()
        eng.cancel()
        g = DependencyGraph([WorkflowNode("a", lambda c: {"a": 1})])
        self.assertEqual(await eng.run(g), CANCELLED)

    async def test_compensation_saga(self):
        compensated = []
        g = DependencyGraph([
            WorkflowNode("reserve", lambda c: {"r": 1},
                         compensation=lambda c: compensated.append("reserve")),
            WorkflowNode("charge", lambda c: (_ for _ in ()).throw(RuntimeError("x")),
                         depends_on=["reserve"], retries=0, critical=True),
        ])
        self.assertEqual(await AsyncWorkflowEngine().run(g, mode="sequential"), FAILED)
        self.assertEqual(compensated, ["reserve"])

    async def test_retries_recover(self):
        state = {"n": 0}
        def flaky(c):
            state["n"] += 1
            if state["n"] < 3:
                raise RuntimeError("again")
            return {"ok": True}
        g = DependencyGraph([WorkflowNode("f", flaky, retries=5)])
        self.assertEqual(await AsyncWorkflowEngine().run(g), COMPLETED)


# ── Async worker runtime ─────────────────────────────────────────────────────

class TestAsyncWorkerRuntime(unittest.IsolatedAsyncioTestCase):
    async def test_processes_all(self):
        rt = AsyncWorkerRuntime(concurrency=4)
        results = []
        for i in range(10):
            await rt.submit(lambda i=i: results.append(i))
        report = await rt.run_until_empty()
        self.assertEqual(report["completed"], 10)
        self.assertEqual(sorted(results), list(range(10)))

    async def test_async_tasks(self):
        rt = AsyncWorkerRuntime(concurrency=3)
        hits = []
        async def work():
            await asyncio.sleep(0)
            hits.append(1)
        for _ in range(5):
            await rt.submit(work)
        await rt.run_until_empty()
        self.assertEqual(len(hits), 5)

    async def test_backpressure(self):
        rt = AsyncWorkerRuntime(concurrency=1, maxsize=2)
        self.assertTrue(await rt.submit(lambda: None, block=False))
        self.assertTrue(await rt.submit(lambda: None, block=False))
        self.assertFalse(await rt.submit(lambda: None, block=False))

    async def test_retry_then_fail(self):
        rt = AsyncWorkerRuntime(concurrency=2, max_retries=2)
        state = {"n": 0}
        def boom():
            state["n"] += 1
            raise RuntimeError("nope")
        await rt.submit(boom)
        report = await rt.run_until_empty()
        self.assertEqual(report["failed"], 1)
        self.assertGreaterEqual(state["n"], 3)


# ── Feature flags ──────────────────────────────────────────────────────────────

class TestFeatureFlags(unittest.TestCase):
    def setUp(self):
        self._audit = patch("core.tenancy.audit")
        self._audit.start()
        self.ff = FeatureFlagService()

    def tearDown(self):
        self._audit.stop()

    def test_default_and_override(self):
        self.ff.register("semantic_cache", default=False)
        self.assertFalse(self.ff.is_enabled("semantic_cache", "other"))
        self.ff.set_tenant_override("semantic_cache", "acme", True)
        self.assertTrue(self.ff.is_enabled("semantic_cache", "acme"))

    def test_kill_switch_overrides_everything(self):
        self.ff.register("x", default=True)
        self.ff.set_tenant_override("x", "acme", True)
        self.ff.set_kill_switch("x", True)
        self.assertFalse(self.ff.is_enabled("x", "acme"))

    def test_percentage_rollout_is_stable(self):
        self.ff.register("y", default=False)
        self.ff.set_rollout("y", 50)
        first = [self.ff.is_enabled("y", unit=f"u{i}") for i in range(100)]
        second = [self.ff.is_enabled("y", unit=f"u{i}") for i in range(100)]
        self.assertEqual(first, second)              # deterministic
        self.assertTrue(10 <= sum(first) <= 90)      # roughly partial

    def test_reload(self):
        n = self.ff.reload({"a": {"default": True}, "b": {"rollout_percentage": 100}})
        self.assertEqual(n, 2)
        self.assertTrue(self.ff.is_enabled("a", "t"))
        self.assertTrue(self.ff.is_enabled("b", unit="anyone"))

    def test_config_value_override(self):
        self.ff.register("llm.provider", default="anthropic")
        self.ff.set_tenant_override("llm.provider", "acme", "openai")
        self.assertEqual(self.ff.value("llm.provider", "acme"), "openai")
        self.assertEqual(self.ff.value("llm.provider", "other"), "anthropic")


# ── LLM v3 ─────────────────────────────────────────────────────────────────────

class TestLLMv3(unittest.TestCase):
    def test_budget_enforcement(self):
        bm = BudgetManager()
        bm.set_budget("acme", max_cost_usd=1.0)
        bm.consume("acme", 100, 0.6)
        self.assertAlmostEqual(bm.utilization("acme"), 0.6)
        with self.assertRaises(BudgetExceeded):
            bm.consume("acme", 100, 0.6)   # would exceed 1.0

    def test_adaptive_routing_downgrades(self):
        bm = BudgetManager()
        bm.set_budget("acme", max_cost_usd=1.0)
        ar = AdaptiveRouter(budgets=bm, downgrade_at=0.8)
        self.assertEqual(ar.route("research", "acme"), "claude-sonnet-4-6")
        bm.consume("acme", 0, 0.85)
        self.assertEqual(ar.route("research", "acme"), ar.cheap_model)

    def test_embedding_cache_single_flight(self):
        calls = {"n": 0}
        def embed(t):
            calls["n"] += 1
            return [1.0]
        ec = EmbeddingCache(embed_fn=embed)
        ec.get("hello world")
        ec.get("hello world")
        self.assertEqual(calls["n"], 1)
        self.assertGreater(ec.hit_ratio, 0.0)

    def test_provider_stats_failover(self):
        ps = ProviderStats()
        for _ in range(3):
            ps.record("anthropic", ok=False, latency_ms=10)
        ps.record("anthropic", ok=True, latency_ms=10)
        self.assertEqual(ps.error_rate("anthropic"), 0.75)
        self.assertTrue(ps.should_failover("anthropic", threshold=0.5))
        self.assertEqual(ps.throughput("anthropic"), 4)


# ── Temporal ─────────────────────────────────────────────────────────────────

class TestTemporal(unittest.TestCase):
    def test_local_workflow_success(self):
        def double(x):
            return x * 2
        def wf(ctx, n):
            return ctx.execute_activity(double, n) + ctx.execute_activity(double, n + 1)
        ex = LocalTemporalRuntime().start(wf, 5)
        self.assertEqual(ex.status, "COMPLETED")
        self.assertEqual(ex.result, 10 + 12)
        self.assertEqual(len(ex.history), 2)

    def test_local_workflow_failure_records_history(self):
        def boom(_):
            raise RuntimeError("activity down")
        def wf(ctx):
            return ctx.execute_activity(boom, 1, retries=0)
        ex = LocalTemporalRuntime().start(wf)
        self.assertEqual(ex.status, "FAILED")
        self.assertIn("activity down", ex.error)
        self.assertEqual(ex.history[-1]["status"], "failed")

    def test_activity_retry(self):
        state = {"n": 0}
        def flaky(_):
            state["n"] += 1
            if state["n"] < 2:
                raise RuntimeError("retry me")
            return "ok"
        def wf(ctx):
            return ctx.execute_activity(flaky, 1, retries=3)
        ex = LocalTemporalRuntime().start(wf)
        self.assertEqual(ex.status, "COMPLETED")
        self.assertEqual(ex.result, "ok")

    def test_persistence(self):
        p = InMemoryPersistence()
        rt = LocalTemporalRuntime(persistence=p)
        ex = rt.start(lambda ctx: 42)
        self.assertEqual(p.load(ex.run_id)["status"], "COMPLETED")


# ── Backward compatibility ─────────────────────────────────────────────────────

class TestBackwardCompat(unittest.TestCase):
    def test_v5x_imports(self):
        from core import persistence, workflow_dag, workflow_engine  # noqa: F401
        from events import EventBus  # noqa: F401
        from llm_gateway import LLMGateway, ModelRouter  # noqa: F401
        from ml_platform import FeatureStore  # noqa: F401
        self.assertTrue(True)

    def test_sync_dag_still_works(self):
        from core.workflow_dag import DAGWorkflowEngine
        self.assertTrue(hasattr(DAGWorkflowEngine, "run"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
