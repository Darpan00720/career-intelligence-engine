"""Tests for the v5 enterprise foundation:

  authentication, RBAC, multi-tenancy, event bus, workflow recovery, distributed
  workers, caching, LLM gateway, tracing/metrics, API versioning, cost tracking,
  learning engine, security, load, chaos, and backward compatibility.
"""
import os
import random
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from core import (
    auth,
    cache,
    cost_intelligence,
    learning_engine,
    metrics,
    security,
    tenancy,
    tracing,
)
from core import concurrency
from core.workflow_engine import (
    COMPLETED,
    FAILED,
    PARTIAL_SUCCESS,
    StepDef,
    WorkflowDefinition,
    WorkflowEngine,
)
from events import Event, EventBus, JOB_DISCOVERED
from llm_gateway import LLMGateway
from llm_gateway.providers import EchoProvider
from workers import LocalWorkerPool


# ── Shared in-memory DB for tenant/event/workflow/cost tables ───────────────────

def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE tenants (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT UNIQUE,
            name TEXT, mode TEXT, config TEXT, created_at DATETIME DEFAULT (DATETIME('now')));
        CREATE TABLE platform_users (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT UNIQUE,
            tenant_id TEXT, email TEXT, role TEXT, api_key_hash TEXT,
            created_at DATETIME DEFAULT (DATETIME('now')));
        CREATE TABLE workspaces (id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT UNIQUE,
            tenant_id TEXT, name TEXT, created_at DATETIME DEFAULT (DATETIME('now')));
        CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, actor TEXT,
            action TEXT, entity TEXT, detail TEXT, created_at DATETIME DEFAULT (DATETIME('now')));
        CREATE TABLE usage_records (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT,
            metric TEXT, amount REAL, created_at DATETIME DEFAULT (DATETIME('now')));
        CREATE TABLE events_log (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE,
            type TEXT, version INTEGER, tenant_id TEXT, idempotency_key TEXT, payload TEXT,
            status TEXT, error TEXT, created_at DATETIME DEFAULT (DATETIME('now')));
        CREATE UNIQUE INDEX idx_events_idem ON events_log(type, idempotency_key)
            WHERE idempotency_key IS NOT NULL;
        CREATE TABLE dead_letter_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT,
            type TEXT, tenant_id TEXT, payload TEXT, error TEXT,
            created_at DATETIME DEFAULT (DATETIME('now')));
        CREATE TABLE workflow_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT UNIQUE,
            tenant_id TEXT, definition TEXT, status TEXT, detail TEXT,
            created_at DATETIME DEFAULT (DATETIME('now')), updated_at DATETIME);
        CREATE TABLE workflow_steps (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, name TEXT,
            status TEXT, attempts INTEGER, output TEXT, error TEXT, updated_at DATETIME);
        CREATE TABLE workflow_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
            event TEXT, detail TEXT, created_at DATETIME DEFAULT (DATETIME('now')));
        CREATE TABLE llm_costs (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT,
            tenant_id TEXT, workflow_id TEXT, agent TEXT, provider TEXT, model TEXT,
            prompt_tokens INTEGER, completion_tokens INTEGER, cost_usd REAL, latency_ms REAL,
            cached INTEGER, created_at DATETIME DEFAULT (DATETIME('now')));
        CREATE TABLE experiments (id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT UNIQUE,
            name TEXT, status TEXT, variants TEXT, winner TEXT, created_at DATETIME);
        CREATE TABLE applications (job_id INTEGER, status TEXT, outcome TEXT);
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, company TEXT, role_category TEXT,
            work_mode TEXT, visa_gate TEXT, location TEXT);
        CREATE TABLE scores (job_id INTEGER, total_score INTEGER);
    """)
    return conn


class MemDB(unittest.TestCase):
    def setUp(self):
        self.conn = _mem_conn()

        @contextmanager
        def _ctx():
            yield self.conn
            self.conn.commit()

        self._p = patch("core.database.get_connection", _ctx)
        self._p.start()
        tenancy._current_tenant.set(tenancy.DEFAULT_TENANT)

    def tearDown(self):
        self._p.stop()
        self.conn.close()


# ── Authentication ─────────────────────────────────────────────────────────────

class TestAuthentication(unittest.TestCase):
    def setUp(self):
        auth.clear_revocations()

    def test_issue_and_verify(self):
        token = auth.issue_token("u1", "acme", auth.USER)
        p = auth.verify_token(token)
        self.assertEqual(p.user_id, "u1")
        self.assertEqual(p.tenant_id, "acme")
        self.assertEqual(p.role, auth.USER)

    def test_tampered_token_rejected(self):
        token = auth.issue_token("u1", "acme", auth.USER)
        with self.assertRaises(auth.AuthError):
            auth.verify_token(token[:-3] + "xxx")

    def test_expired_token(self):
        token = auth.issue_token("u1", "acme", auth.USER, ttl_seconds=-1)
        with self.assertRaises(auth.AuthError):
            auth.verify_token(token)

    def test_refresh(self):
        token = auth.issue_token("u1", "acme", auth.ADMIN)
        new = auth.refresh_token(token)
        self.assertNotEqual(token, new)
        self.assertEqual(auth.verify_token(new).role, auth.ADMIN)

    def test_revocation(self):
        token = auth.issue_token("u1", "acme", auth.USER)
        jti = auth.verify_token(token).jti
        auth.revoke(jti)
        with self.assertRaises(auth.AuthError):
            auth.verify_token(token)


# ── RBAC ───────────────────────────────────────────────────────────────────────

class TestRBAC(unittest.TestCase):
    def test_permission_matrix(self):
        self.assertTrue(auth.has_permission(auth.ADMIN, "anything"))
        self.assertTrue(auth.has_permission(auth.USER, "jobs:read"))
        self.assertFalse(auth.has_permission(auth.USER, "workflows:run"))
        self.assertTrue(auth.has_permission(auth.RECRUITER, "workflows:run"))

    def test_require_permission_decorator(self):
        @auth.require_permission("workflows:run")
        def run_it(principal=None):
            return "ran"
        admin = auth.Principal("a", "t", auth.ADMIN, "j")
        user = auth.Principal("u", "t", auth.USER, "j")
        self.assertEqual(run_it(principal=admin), "ran")
        with self.assertRaises(auth.PermissionDenied):
            run_it(principal=user)


# ── Multi-tenancy ────────────────────────────────────────────────────────────

class TestMultiTenancy(MemDB):
    def test_create_and_get_tenant(self):
        tenancy.create_tenant("acme", "Acme", config={"features": {"beta": True},
                                                       "quotas": {"jobs": 5}})
        t = tenancy.get_tenant("acme")
        self.assertEqual(t.name, "Acme")
        self.assertTrue(t.feature("beta"))
        self.assertEqual(t.quota("jobs"), 5)

    def test_tenant_context_isolation(self):
        with tenancy.use_tenant("acme"):
            self.assertEqual(tenancy.current_tenant(), "acme")
        self.assertEqual(tenancy.current_tenant(), tenancy.DEFAULT_TENANT)

    def test_feature_flags_default(self):
        self.assertFalse(tenancy.feature_enabled("missing", "acme", default=False))

    def test_usage_and_quota(self):
        tenancy.create_tenant("acme", config={"quotas": {"llm_calls": 2}})
        with tenancy.use_tenant("acme"):
            tenancy.record_usage("llm_calls")
            self.assertTrue(tenancy.check_quota("llm_calls")["allowed"])
            tenancy.record_usage("llm_calls")
            status = tenancy.check_quota("llm_calls")
            self.assertFalse(status["allowed"])
            with self.assertRaises(tenancy.QuotaExceeded):
                tenancy.enforce_quota("llm_calls")

    def test_audit_trail(self):
        with tenancy.use_tenant("acme"):
            tenancy.audit("login", "user", "u1")
            trail = tenancy.audit_trail()
        self.assertTrue(any(e["action"] == "login" for e in trail))


# ── Event bus ────────────────────────────────────────────────────────────────

class TestEventBus(MemDB):
    def test_publish_subscribe(self):
        bus = EventBus()
        received = []
        bus.subscribe(JOB_DISCOVERED, lambda e: received.append(e.payload["id"]))
        bus.publish(Event(JOB_DISCOVERED, {"id": 7}))
        self.assertEqual(received, [7])

    def test_idempotency(self):
        bus = EventBus()
        received = []
        bus.subscribe(JOB_DISCOVERED, lambda e: received.append(1))
        bus.emit(JOB_DISCOVERED, {"id": 1}, idempotency_key="k")
        r2 = bus.emit(JOB_DISCOVERED, {"id": 1}, idempotency_key="k")
        self.assertTrue(r2["duplicate"])
        self.assertEqual(len(received), 1)

    def test_dead_letter_on_handler_failure(self):
        bus = EventBus(max_delivery_attempts=2)
        bus.subscribe(JOB_DISCOVERED, lambda e: (_ for _ in ()).throw(RuntimeError("x")))
        bus.publish(Event(JOB_DISCOVERED, {"id": 1}))
        self.assertEqual(len(bus.dlq.list()), 1)

    def test_replay(self):
        bus = EventBus()
        bus.publish(Event(JOB_DISCOVERED, {"id": 1}))
        bus.publish(Event(JOB_DISCOVERED, {"id": 2}))
        replayed = []
        bus.subscribe(JOB_DISCOVERED, lambda e: replayed.append(e.payload["id"]))
        count = bus.replay(JOB_DISCOVERED)
        self.assertEqual(count, 2)
        self.assertEqual(sorted(replayed), [1, 2])


# ── Workflow recovery ────────────────────────────────────────────────────────

class TestWorkflowEngine(MemDB):
    def test_run_completes(self):
        log = []
        defn = WorkflowDefinition("wf_ok", [
            StepDef("a", lambda ctx: log.append("a") or {"a": 1}),
            StepDef("b", lambda ctx: log.append("b") or {"b": 2}),
        ])
        status = WorkflowEngine().start(defn)
        self.assertEqual(status, COMPLETED)
        self.assertEqual(log, ["a", "b"])

    def test_partial_success_on_step_failure(self):
        defn = WorkflowDefinition("wf_partial", [
            StepDef("a", lambda ctx: {"a": 1}),
            StepDef("b", lambda ctx: (_ for _ in ()).throw(RuntimeError("x")), max_retries=0),
        ])
        self.assertEqual(WorkflowEngine().start(defn), PARTIAL_SUCCESS)

    def test_critical_failure_aborts(self):
        defn = WorkflowDefinition("wf_crit", [
            StepDef("a", lambda ctx: (_ for _ in ()).throw(RuntimeError("x")),
                    max_retries=0, critical=True),
            StepDef("b", lambda ctx: {"reached": True}),
        ])
        self.assertEqual(WorkflowEngine().start(defn), FAILED)

    def test_resume_skips_completed_steps(self):
        runs = {"a": 0, "b": 0}
        # First run: step b fails so it is not COMPLETED.
        state = {"fail_b": True}
        def step_b(ctx):
            runs["b"] += 1
            if state["fail_b"]:
                raise RuntimeError("not yet")
            return {"b": 1}
        defn = WorkflowDefinition("wf_resume", [
            StepDef("a", lambda ctx: runs.__setitem__("a", runs["a"] + 1) or {"a": 1}, max_retries=0),
            StepDef("b", step_b, max_retries=0),
        ])
        eng = WorkflowEngine()
        run_id = None
        # capture the run_id by starting then querying latest
        eng.start(defn)
        with self.conn:
            run_id = self.conn.execute("SELECT run_id FROM workflow_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertEqual(runs["a"], 1)
        # Now fix b and resume: step a should be skipped (already COMPLETED).
        state["fail_b"] = False
        status = eng.resume(run_id, defn)
        self.assertEqual(status, COMPLETED)
        self.assertEqual(runs["a"], 1)   # not re-run
        self.assertEqual(runs["b"], 2)   # retried on resume

    def test_cancel(self):
        eng = WorkflowEngine()
        defn = WorkflowDefinition("wf_cancel", [StepDef("a", lambda ctx: {"a": 1})])
        eng.start(defn)
        with self.conn:
            run_id = self.conn.execute("SELECT run_id FROM workflow_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
        eng.cancel(run_id)
        self.assertEqual(eng.get_run(run_id)["status"], "CANCELLED")


# ── Distributed workers ──────────────────────────────────────────────────────

class TestWorkers(unittest.TestCase):
    def test_pool_processes_tasks(self):
        pool = LocalWorkerPool(num_workers=4)
        results = []
        for i in range(10):
            pool.submit(lambda i=i: results.append(i))
        report = pool.run()
        self.assertEqual(len(report.completed), 10)
        self.assertEqual(sorted(results), list(range(10)))

    def test_backpressure(self):
        pool = LocalWorkerPool(num_workers=1, maxsize=2)
        self.assertTrue(pool.submit(lambda: None))
        self.assertTrue(pool.submit(lambda: None))
        self.assertFalse(pool.submit(lambda: None))   # queue full → backpressure

    def test_retry_then_dead_letter(self):
        pool = LocalWorkerPool(num_workers=2)
        state = {"n": 0}
        def flaky():
            state["n"] += 1
            raise RuntimeError("always")
        pool.submit(flaky, max_retries=2)
        report = pool.run()
        self.assertEqual(len(report.failed), 1)
        self.assertGreaterEqual(state["n"], 3)   # initial + retries


# ── Caching ──────────────────────────────────────────────────────────────────

class TestCaching(unittest.TestCase):
    def test_ttl_and_get_set(self):
        c = cache.CacheManager()
        c.set("k", 1, ttl=0.01)
        self.assertEqual(c.get("k"), 1)
        import time
        time.sleep(0.02)
        self.assertIsNone(c.get("k"))

    def test_invalidate_prefix(self):
        c = cache.CacheManager()
        c.set("a:1", 1); c.set("a:2", 2); c.set("b:1", 3)
        self.assertEqual(c.invalidate("a:"), 2)
        self.assertIsNone(c.get("a:1"))
        self.assertEqual(c.get("b:1"), 3)

    def test_get_or_set_single_flight(self):
        c = cache.CacheManager()
        calls = {"n": 0}
        def produce():
            calls["n"] += 1
            return 42
        self.assertEqual(c.get_or_set("k", produce), 42)
        self.assertEqual(c.get_or_set("k", produce), 42)
        self.assertEqual(calls["n"], 1)   # produced once

    def test_hit_ratio(self):
        c = cache.CacheManager()
        c.set("k", 1)
        c.get("k"); c.get("missing")
        self.assertAlmostEqual(c.hit_ratio, 0.5)


# ── LLM gateway ──────────────────────────────────────────────────────────────

class TestLLMGateway(MemDB):
    def test_complete_and_cost_tracked(self):
        cache.reset_cache()
        gw = LLMGateway(primary=EchoProvider())
        r = gw.complete("hello", model="echo", tenant_id="acme", agent="research")
        self.assertEqual(r.provider, "echo")
        self.assertFalse(r.cached)
        rows = self.conn.execute("SELECT * FROM llm_costs").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tenant_id"], "acme")

    def test_cache_hit_on_second_call(self):
        cache.reset_cache()
        gw = LLMGateway(primary=EchoProvider())
        gw.complete("same prompt", model="echo", tenant_id="t")
        r2 = gw.complete("same prompt", model="echo", tenant_id="t")
        self.assertTrue(r2.cached)
        self.assertEqual(r2.cost_usd, 0.0)

    def test_circuit_breaker_falls_back(self):
        cache.reset_cache()
        class FailingProvider(EchoProvider):
            name = "failing"
            def complete(self, *a, **k):
                raise RuntimeError("down")
        gw = LLMGateway(primary=FailingProvider(), fallback=EchoProvider(),
                        retries=0)
        # Trip the breaker (threshold 3) then confirm fallback still serves.
        for _ in range(4):
            r = gw.complete("x", model="echo", use_cache=False)
        self.assertEqual(r.provider, "echo")
        self.assertEqual(gw.breaker.state, gw.breaker.OPEN)


# ── Tracing / metrics ────────────────────────────────────────────────────────

class TestTracingMetrics(unittest.TestCase):
    def test_trace_and_span(self):
        ids = tracing.start_trace()
        self.assertNotEqual(ids["trace_id"], "-")
        with tracing.span("work") as s:
            self.assertEqual(s["trace_id"], ids["trace_id"])
            self.assertNotEqual(s["span_id"], ids["span_id"])

    def test_metrics_registry(self):
        reg = metrics.MetricsRegistry()
        reg.inc("c", 2); reg.inc("c")
        reg.set_gauge("g", 5)
        reg.observe("h", 1.0); reg.observe("h", 3.0)
        snap = reg.snapshot()
        self.assertEqual(snap["counters"]["c"], 3)
        self.assertEqual(snap["gauges"]["g"], 5)
        self.assertEqual(snap["histograms"]["h"]["avg"], 2.0)
        self.assertIn("c 3", reg.render_prometheus())


# ── API versioning ───────────────────────────────────────────────────────────

class TestApiVersioning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from api.app import app
        cls.client = TestClient(app)

    def test_v1_and_v2_coexist(self):
        with patch("core.database.get_all_scored_jobs_ranked", return_value=[]):
            v1 = self.client.get("/api/jobs")          # v1 surface
            v2 = self.client.get("/api/v2/jobs")       # v2 surface
        self.assertEqual(v1.status_code, 200)
        self.assertEqual(v2.status_code, 200)

    def test_v2_tenant_header(self):
        r = self.client.get("/api/v2/workflows", headers={"X-Tenant-ID": "acme"})
        self.assertEqual(r.status_code, 200)

    def test_v2_metrics(self):
        r = self.client.get("/api/v2/metrics")
        self.assertEqual(r.status_code, 200)
        self.assertIn("counters", r.json())


# ── Cost tracking ─────────────────────────────────────────────────────────────

class TestCostTracking(MemDB):
    def _seed_costs(self):
        gw = LLMGateway(primary=EchoProvider())
        cache.reset_cache()
        gw.complete("a", model="claude-sonnet-4-6", tenant_id="acme",
                    workflow_id="wf1", agent="research", use_cache=False)
        gw.complete("b", model="claude-sonnet-4-6", tenant_id="acme",
                    workflow_id="wf1", agent="documents", use_cache=False)

    def test_cost_breakdown(self):
        self._seed_costs()
        breakdown = cost_intelligence.cost_breakdown()
        self.assertIn("acme", breakdown["by_tenant"])
        self.assertIn("wf1", breakdown["by_workflow"])
        self.assertGreaterEqual(breakdown["total"]["calls"], 2)

    def test_recommendations_generated(self):
        self._seed_costs()
        recs = cost_intelligence.optimization_recommendations()
        self.assertTrue(recs)

    def test_daily_report(self):
        self._seed_costs()
        report = cost_intelligence.daily_cost_report()
        self.assertTrue(report)
        self.assertIn("cost_usd", report[0])


# ── Learning engine ───────────────────────────────────────────────────────────

class TestLearningEngine(MemDB):
    def _seed(self):
        data = [
            (1, "Acme", "ai_strategy", 95, "Offer"),
            (2, "Acme", "ai_strategy", 92, "Interview"),
            (3, "Beta", "hr_analytics", 72, "Applied"),
        ]
        for jid, co, role, score, status in data:
            self.conn.execute("INSERT INTO jobs (id,company,role_category) VALUES (?,?,?)",
                              (jid, co, role))
            self.conn.execute("INSERT INTO scores (job_id,total_score) VALUES (?,?)", (jid, score))
            self.conn.execute("INSERT INTO applications (job_id,status) VALUES (?,?)", (jid, status))
        self.conn.commit()

    def test_feature_importance(self):
        self._seed()
        fi = learning_engine.feature_importance()
        self.assertIn("company", fi)
        self.assertGreaterEqual(fi["score_band"]["rates"].get("90-100", 0), 1.0)

    def test_epsilon_greedy_bandit_learns(self):
        bandit = learning_engine.EpsilonGreedyBandit(["a", "b"], epsilon=0.0)
        for _ in range(20):
            bandit.update("b", 1.0)
            bandit.update("a", 0.0)
        self.assertEqual(bandit.best_arm(), "b")

    def test_thompson_bandit(self):
        bandit = learning_engine.ThompsonBandit(["a", "b"])
        for _ in range(30):
            bandit.update("b", 1.0)
            bandit.update("a", 0.0)
        self.assertEqual(bandit.best_arm(), "b")

    def test_calibration_metrics(self):
        self._seed()
        cal = learning_engine.calibration_metrics()
        self.assertIn("ece", cal)
        self.assertGreaterEqual(cal["samples"], 3)

    def test_learned_rank(self):
        jobs = [{"id": 1, "total_score": 60, "visa_gate": "FAIL"},
                {"id": 2, "total_score": 90, "visa_gate": "PASS"}]
        ranked = learning_engine.learned_rank(jobs, {"total_score": 1.0, "sponsorship_pass": 0.5})
        self.assertEqual(ranked[0]["id"], 2)


# ── Security ─────────────────────────────────────────────────────────────────

class TestSecurity(MemDB):
    def test_pii_masking(self):
        self.assertEqual(security.mask_email("darpan@example.com"), "d***@example.com")
        masked = security.mask_pii("call +39 344 409 8737 or me@x.com")
        self.assertNotIn("4098737", masked)
        self.assertIn("***", masked)

    def test_encryptor_round_trip(self):
        enc = security.KeyedEncryptor("secret")
        cipher = enc.encrypt("sensitive")
        self.assertNotEqual(cipher, "sensitive")
        self.assertEqual(enc.decrypt(cipher), "sensitive")

    def test_gdpr_delete(self):
        tenancy.create_tenant("acme")
        with tenancy.use_tenant("acme"):
            tenancy.audit("x", "y")
            tenancy.record_usage("m")
        deleted = security.delete_tenant_data("acme")
        self.assertGreaterEqual(deleted["audit_log"], 1)
        self.assertEqual(tenancy.audit_trail("acme"), [])

    def test_retention(self):
        # Insert an old audit row and confirm retention prunes it.
        self.conn.execute(
            "INSERT INTO audit_log (tenant_id, action, created_at) "
            "VALUES ('default','old', DATETIME('now','-400 days'))")
        self.conn.commit()
        deleted = security.apply_retention()
        self.assertGreaterEqual(deleted["audit_log"], 1)


# ── Load + chaos ──────────────────────────────────────────────────────────────

class TestLoadAndChaos(MemDB):
    def test_load_many_events(self):
        bus = EventBus()
        seen = []
        bus.subscribe(JOB_DISCOVERED, lambda e: seen.append(e.event_id))
        for i in range(500):
            bus.publish(Event(JOB_DISCOVERED, {"id": i}, idempotency_key=f"k{i}"))
        self.assertEqual(len(seen), 500)
        # Duplicate keys must be deduped under load.
        dupes = sum(bus.publish(Event(JOB_DISCOVERED, {"id": i}, idempotency_key=f"k{i}"))["duplicate"]
                    for i in range(500))
        self.assertEqual(dupes, 500)

    def test_chaos_workflow_recovers_from_random_failures(self):
        rng = random.Random(7)
        attempts = {"flaky": 0}
        def flaky(ctx):
            attempts["flaky"] += 1
            if attempts["flaky"] < 3 and rng.random() < 1.0:
                raise RuntimeError("chaos")
            return {"ok": True}
        defn = WorkflowDefinition("wf_chaos", [
            StepDef("stable", lambda ctx: {"s": 1}),
            StepDef("flaky", flaky, max_retries=5),
        ])
        self.assertEqual(WorkflowEngine().start(defn), COMPLETED)

    def test_parallel_load(self):
        results = concurrency.parallel_map(lambda x: x * x, range(200), max_workers=8)
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(results[10].value, 100)


# ── Backward compatibility ────────────────────────────────────────────────────

class TestBackwardCompatibility(unittest.TestCase):
    def test_default_tenant_unchanged(self):
        self.assertEqual(tenancy.current_tenant(), tenancy.DEFAULT_TENANT)

    def test_legacy_imports_still_work(self):
        # Core v1-v4 modules import and expose their public API unchanged.
        from core import ranking, scorer, tracker, pipeline  # noqa: F401
        from core.scorer import application_priority
        self.assertEqual(application_priority(95), "Apply Now")

    def test_single_tenant_mode_default(self):
        self.assertEqual(tenancy.mode(), tenancy.SINGLE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
