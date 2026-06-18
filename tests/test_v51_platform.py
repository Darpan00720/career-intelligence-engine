"""Tests for v5.1 cloud-native upgrades:

  persistence (Repository/UnitOfWork/TenantAware/optimistic locking), DAG
  workflows (topology, parallel, conditional, fan-out/in, sub-workflow,
  compensation, resume), event envelope v2, event streams (consumer groups,
  at-least-once, replay, DLQ, backpressure, ordering), LLM platform v2
  (prompt registry, model router, semantic cache), ML platform (feature store,
  model registry, training + inference), plus load/chaos and backward compat.
"""
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from core import tenancy
from core.persistence import (
    DatabaseManager,
    Repository,
    StaleDataError,
    TenantAwareRepository,
    UnitOfWork,
)
from core.workflow_dag import (
    CycleError,
    DAGWorkflowEngine,
    DependencyGraph,
    ExecutionPlanner,
    WorkflowNode,
    make_subworkflow_node,
)
from core.workflow_engine import COMPLETED, FAILED, PARTIAL_SUCCESS
from events.streams import LocalStreamBroker, StreamConsumer
from events.types import Event
from llm_gateway import ModelRouter, PromptRegistry, SemanticCache
from ml_platform import FeatureStore, ModelRegistry, TrainingPipeline, InferenceService
from ml_platform.registry import PRODUCTION


# ── In-memory DB with v5.1 tables ───────────────────────────────────────────────

def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE workflow_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT UNIQUE,
            tenant_id TEXT, definition TEXT, status TEXT, detail TEXT,
            created_at DATETIME, updated_at DATETIME);
        CREATE TABLE workflow_steps (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, name TEXT,
            status TEXT, attempts INTEGER, output TEXT, error TEXT, updated_at DATETIME);
        CREATE TABLE workflow_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
            event TEXT, detail TEXT, created_at DATETIME DEFAULT (DATETIME('now')));
        CREATE TABLE events_log (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE,
            type TEXT, version INTEGER, tenant_id TEXT, idempotency_key TEXT, payload TEXT,
            status TEXT, error TEXT, created_at DATETIME);
        CREATE UNIQUE INDEX idx_ev ON events_log(type, idempotency_key) WHERE idempotency_key IS NOT NULL;
        CREATE TABLE ml_feature_sets (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, version INTEGER,
            features TEXT, data TEXT, created_at DATETIME, UNIQUE(name, version));
        CREATE TABLE ml_models (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, version INTEGER,
            stage TEXT, metadata TEXT, lineage TEXT, artifact TEXT, created_at DATETIME,
            UNIQUE(name, version));
    """)
    return conn


_SCHEMA = None


class MemDB(unittest.TestCase):
    """File-backed test DB that opens a fresh connection per call — mirrors
    production `database.get_connection()` so parallel (multi-thread) code works."""

    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        seed = _mem_conn()  # build schema in memory then copy DDL to the file
        with sqlite3.connect(self.path) as f:
            for stmt in seed.iterdump():
                if stmt.strip().upper().startswith(("CREATE", "BEGIN", "COMMIT")):
                    try:
                        f.execute(stmt)
                    except sqlite3.OperationalError:
                        pass
            f.commit()
        seed.close()

        path = self.path

        @contextmanager
        def _ctx():
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

        self._p = patch("core.database.get_connection", _ctx)
        self._p.start()
        tenancy._current_tenant.set("default")

    def tearDown(self):
        self._p.stop()
        if os.path.exists(self.path):
            os.remove(self.path)

    def _latest_run(self) -> str:
        conn = sqlite3.connect(self.path)
        try:
            return conn.execute(
                "SELECT run_id FROM workflow_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
        finally:
            conn.close()


# ── Persistence ────────────────────────────────────────────────────────────────

class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        self.mgr = DatabaseManager(connect_fn=self._connect)
        with self._connect() as c:
            c.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, tenant_id TEXT, "
                      "name TEXT, version INTEGER DEFAULT 0)")
            c.commit()
        tenancy._current_tenant.set("default")

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def test_unit_of_work_commits(self):
        with UnitOfWork(self.mgr) as uow:
            repo = uow.repository(Repository, "widgets")
            wid = repo.add({"name": "w1", "tenant_id": "default"})
        with UnitOfWork(self.mgr) as uow:
            got = uow.repository(Repository, "widgets").get(wid)
        self.assertEqual(got["name"], "w1")

    def test_unit_of_work_rollback(self):
        try:
            with UnitOfWork(self.mgr) as uow:
                uow.repository(Repository, "widgets").add({"name": "x", "tenant_id": "default"})
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        with UnitOfWork(self.mgr) as uow:
            self.assertEqual(uow.repository(Repository, "widgets").list(), [])

    def test_tenant_isolation(self):
        with UnitOfWork(self.mgr) as uow:
            with tenancy.use_tenant("acme"):
                uow.repository(TenantAwareRepository, "widgets").add({"name": "acme-w"})
            with tenancy.use_tenant("beta"):
                uow.repository(TenantAwareRepository, "widgets").add({"name": "beta-w"})
        with UnitOfWork(self.mgr) as uow:
            with tenancy.use_tenant("acme"):
                rows = uow.repository(TenantAwareRepository, "widgets").list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "acme-w")

    def test_optimistic_locking(self):
        with UnitOfWork(self.mgr) as uow:
            wid = uow.repository(Repository, "widgets").add(
                {"name": "w", "tenant_id": "default", "version": 0})
        # Successful versioned update bumps version 0 → 1.
        with UnitOfWork(self.mgr) as uow:
            uow.repository(Repository, "widgets").update(wid, {"name": "w2"}, expected_version=0)
        # A stale writer using version 0 again must fail.
        with self.assertRaises(StaleDataError):
            with UnitOfWork(self.mgr) as uow:
                uow.repository(Repository, "widgets").update(wid, {"name": "w3"}, expected_version=0)


# ── DAG workflows ──────────────────────────────────────────────────────────────

class TestDependencyGraph(unittest.TestCase):
    def test_topological_levels(self):
        g = DependencyGraph([
            WorkflowNode("a", lambda c: {}),
            WorkflowNode("b", lambda c: {}, depends_on=["a"]),
            WorkflowNode("c", lambda c: {}, depends_on=["a"]),
            WorkflowNode("d", lambda c: {}, depends_on=["b", "c"]),
        ])
        self.assertEqual(ExecutionPlanner.plan(g), [["a"], ["b", "c"], ["d"]])

    def test_cycle_detected(self):
        with self.assertRaises(CycleError):
            DependencyGraph([
                WorkflowNode("a", lambda c: {}, depends_on=["b"]),
                WorkflowNode("b", lambda c: {}, depends_on=["a"]),
            ])

    def test_unknown_dependency(self):
        with self.assertRaises(ValueError):
            DependencyGraph([WorkflowNode("a", lambda c: {}, depends_on=["ghost"])])


class TestDAGEngine(MemDB):
    def test_parallel_fan_out_in(self):
        order = []
        g = DependencyGraph([
            WorkflowNode("root", lambda c: order.append("root") or {"seed": 1}),
            WorkflowNode("x", lambda c: order.append("x") or {"x": c["seed"] + 1}, depends_on=["root"]),
            WorkflowNode("y", lambda c: order.append("y") or {"y": c["seed"] + 2}, depends_on=["root"]),
            WorkflowNode("join", lambda c: order.append("join") or {"sum": c["x"] + c["y"]},
                         depends_on=["x", "y"]),
        ])
        status = DAGWorkflowEngine().run("dag1", g, mode="parallel")
        self.assertEqual(status, COMPLETED)
        self.assertEqual(order[0], "root")
        self.assertEqual(order[-1], "join")

    def test_conditional_branch_skips(self):
        ran = []
        g = DependencyGraph([
            WorkflowNode("a", lambda c: {"go": False}),
            WorkflowNode("b", lambda c: ran.append("b") or {}, depends_on=["a"],
                         condition=lambda c: c.get("go", False)),
        ])
        self.assertEqual(DAGWorkflowEngine().run("dag2", g), COMPLETED)
        self.assertEqual(ran, [])   # b skipped because condition False

    def test_subworkflow(self):
        child = DependencyGraph([WorkflowNode("inner", lambda c: {"inner_done": True})])
        g = DependencyGraph([
            WorkflowNode("pre", lambda c: {"pre": 1}),
            make_subworkflow_node("childflow", child, depends_on=["pre"]),
        ])
        self.assertEqual(DAGWorkflowEngine().run("dag_sub", g), COMPLETED)

    def test_compensation_on_critical_failure(self):
        compensated = []
        g = DependencyGraph([
            WorkflowNode("reserve", lambda c: {"reserved": True},
                         compensation=lambda c: compensated.append("reserve")),
            WorkflowNode("charge", lambda c: (_ for _ in ()).throw(RuntimeError("declined")),
                         depends_on=["reserve"], retries=0, critical=True),
        ])
        status = DAGWorkflowEngine().run("dag_saga", g, mode="sequential")
        self.assertEqual(status, FAILED)
        self.assertEqual(compensated, ["reserve"])   # saga rolled back

    def test_resume_skips_completed(self):
        runs = {"a": 0, "b": 0}
        state = {"fail": True}
        def b(c):
            runs["b"] += 1
            if state["fail"]:
                raise RuntimeError("later")
            return {"b": 1}
        g = DependencyGraph([
            WorkflowNode("a", lambda c: runs.__setitem__("a", runs["a"] + 1) or {"a": 1}, retries=0),
            WorkflowNode("b", b, depends_on=["a"], retries=0),
        ])
        eng = DAGWorkflowEngine()
        self.assertEqual(eng.run("dag_resume", g, mode="sequential"), PARTIAL_SUCCESS)
        run_id = self._latest_run()
        state["fail"] = False
        self.assertEqual(eng.resume(run_id, g, mode="sequential"), COMPLETED)
        self.assertEqual(runs["a"], 1)   # not re-run
        self.assertEqual(runs["b"], 2)   # retried on resume


# ── Event envelope v2 ──────────────────────────────────────────────────────────

class TestEventEnvelope(unittest.TestCase):
    def test_envelope_has_distributed_ids(self):
        e = Event("JobScored", {"id": 1}, tenant_id="acme", workflow_id="wf1",
                  causation_id="cause-1")
        env = e.envelope()
        for key in ("trace_id", "correlation_id", "causation_id", "workflow_id", "tenant_id"):
            self.assertIn(key, env)
        self.assertEqual(env["workflow_id"], "wf1")
        self.assertEqual(env["tenant_id"], "acme")

    def test_trace_autopopulates(self):
        from core import tracing
        tracing.start_trace("corr-123")
        e = Event("JobScored", {})
        self.assertEqual(e.correlation_id, "corr-123")
        self.assertNotEqual(e.trace_id, "-")


# ── Event streams ──────────────────────────────────────────────────────────────

class TestEventStreams(unittest.TestCase):
    def test_consumer_group_at_least_once(self):
        broker = LocalStreamBroker(num_partitions=2)
        for i in range(6):
            broker.publish("jobs", Event("JobDiscovered", {"id": i}), key=str(i))
        seen = []
        c = StreamConsumer(broker, "jobs", "group-1", lambda e: seen.append(e.payload["id"]))
        stats = c.run_once()
        self.assertEqual(stats["processed"], 6)
        self.assertEqual(sorted(seen), list(range(6)))
        # After ack, re-poll yields nothing.
        self.assertEqual(c.run_once()["processed"], 0)

    def test_partition_ordering(self):
        broker = LocalStreamBroker(num_partitions=3)
        # Same key → same partition → FIFO order preserved within that partition.
        for i in range(5):
            broker.publish("ord", Event("E", {"n": i}), key="same")
        part = next(p for p in range(3) if len(broker.replay("ord", p)) == 5)
        order = [m.event.payload["n"] for m in broker.replay("ord", part)]
        self.assertEqual(order, [0, 1, 2, 3, 4])

    def test_dead_letter_on_poison(self):
        broker = LocalStreamBroker(num_partitions=1)
        broker.publish("t", Event("E", {"id": 1}))
        c = StreamConsumer(broker, "t", "g", lambda e: (_ for _ in ()).throw(RuntimeError("x")),
                           max_retries=1)
        stats = c.run_once()
        self.assertEqual(stats["dead_lettered"], 1)
        self.assertEqual(len(broker.replay("t.DLQ", 0)), 1)

    def test_backpressure_limits_inflight(self):
        broker = LocalStreamBroker(num_partitions=1, max_inflight=3)
        for i in range(10):
            broker.publish("t", Event("E", {"id": i}))
        batch = broker.poll("t", "g", max_messages=100)
        self.assertLessEqual(len(batch), 3)   # capped by max_inflight

    def test_replay(self):
        broker = LocalStreamBroker(num_partitions=1)
        for i in range(4):
            broker.publish("t", Event("E", {"id": i}))
        self.assertEqual(len(broker.replay("t", 0)), 4)


# ── LLM platform v2 ────────────────────────────────────────────────────────────

class TestLLMPlatformV2(unittest.TestCase):
    def test_prompt_registry_versioning(self):
        reg = PromptRegistry()
        v1 = reg.register("p", "Hello {x}")
        v1b = reg.register("p", "Hello {x}")   # identical → same version
        v2 = reg.register("p", "Hi {x}")       # changed → new version
        self.assertEqual(v1.version, 1)
        self.assertEqual(v1b.version, 1)
        self.assertEqual(v2.version, 2)
        self.assertEqual(reg.render("p", x="World"), "Hi World")
        self.assertEqual(reg.render("p", version=1, x="World"), "Hello World")

    def test_model_router(self):
        r = ModelRouter()
        self.assertEqual(r.route("research"), "claude-sonnet-4-6")
        self.assertEqual(r.route("strategy"), "claude-opus-4-8")
        self.assertEqual(r.route("unknown_task"), "claude-sonnet-4-6")
        r.record_benchmark("m1", latency_ms=100, cost_usd=0.01, quality=0.9)
        r.record_benchmark("m2", latency_ms=50, cost_usd=0.02, quality=0.7)
        self.assertEqual(r.best_model("latency"), "m2")
        self.assertEqual(r.best_model("quality"), "m1")

    def test_semantic_cache(self):
        sc = SemanticCache(threshold=0.6)
        sc.put("research the acme company background", "RESULT")
        self.assertEqual(sc.get("research acme company background details"), "RESULT")
        self.assertIsNone(sc.get("completely unrelated weather forecast"))
        self.assertGreater(sc.hit_ratio, 0.0)


# ── ML platform ────────────────────────────────────────────────────────────────

class TestMLPlatform(MemDB):
    def _seed_features(self):
        fs = FeatureStore()
        data = [{"score": 0.9, "spons": 1, "label": 1},
                {"score": 0.5, "spons": 0, "label": 0},
                {"score": 0.85, "spons": 1, "label": 1},
                {"score": 0.2, "spons": 0, "label": 0}]
        return fs, fs.register("jobfeats", ["score", "spons"], data)

    def test_feature_store_versioning(self):
        fs, v1 = self._seed_features()
        v2 = fs.register("jobfeats", ["score"], [{"score": 1.0, "label": 1}])
        self.assertEqual(v1, 1)
        self.assertEqual(v2, 2)
        self.assertEqual(fs.versions("jobfeats"), [1, 2])
        self.assertEqual(fs.get("jobfeats")["version"], 2)   # latest
        self.assertEqual(len(fs.get("jobfeats", 1)["data"]), 4)

    def test_train_evaluate_infer(self):
        self._seed_features()
        tp = TrainingPipeline()
        art = tp.train_ltr("ltr", "jobfeats", ["score", "spons"])
        self.assertIn("score", art["weights"])
        ev = tp.offline_evaluate("ltr", "jobfeats")
        self.assertEqual(ev["pairwise_accuracy"], 1.0)
        svc = InferenceService(ModelRegistry().get("ltr"))
        self.assertGreater(svc.predict({"score": 0.9, "spons": 1}),
                           svc.predict({"score": 0.1, "spons": 0}))

    def test_model_registry_stages(self):
        reg = ModelRegistry()
        v1 = reg.register("m", {"weights": {}}, metadata={"type": "x"},
                          lineage={"src": "fs1"})
        v2 = reg.register("m", {"weights": {}})
        reg.promote("m", v1, PRODUCTION)
        self.assertEqual(reg.production_model("m")["version"], v1)
        reg.promote("m", v2, PRODUCTION)   # promoting v2 archives v1
        self.assertEqual(reg.production_model("m")["version"], v2)
        self.assertEqual(len(reg.list_models("m")), 2)


# ── Load + chaos ──────────────────────────────────────────────────────────────

class TestLoadChaos(MemDB):
    def test_stream_load(self):
        broker = LocalStreamBroker(num_partitions=8)
        for i in range(2000):
            broker.publish("load", Event("E", {"id": i}), key=str(i))
        seen = []
        c = StreamConsumer(broker, "load", "g", lambda e: seen.append(1))
        # Drain in batches.
        while c.run_once(max_messages=500)["processed"]:
            pass
        self.assertEqual(len(seen), 2000)

    def test_chaos_dag_recovers_via_retries(self):
        import random
        rng = random.Random(11)
        attempts = {"n": 0}
        def flaky(c):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("chaos")
            return {"ok": True}
        g = DependencyGraph([
            WorkflowNode("stable", lambda c: {"s": 1}),
            WorkflowNode("flaky", flaky, depends_on=["stable"], retries=5),
        ])
        self.assertEqual(DAGWorkflowEngine().run("chaos", g), COMPLETED)


# ── Backward compatibility ─────────────────────────────────────────────────────

class TestBackwardCompat(unittest.TestCase):
    def test_v5_and_earlier_imports(self):
        from core import workflow_engine, orchestrator, tenancy, auth  # noqa: F401
        from events import EventBus, get_bus  # noqa: F401
        from llm_gateway import LLMGateway  # noqa: F401
        self.assertTrue(True)

    def test_event_to_row_backward_compatible(self):
        # to_row still emits exactly the v5 columns (no new persistence columns).
        e = Event("JobScored", {"id": 1})
        self.assertEqual(set(e.to_row()), {"event_id", "type", "version",
                                           "tenant_id", "idempotency_key", "payload"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
