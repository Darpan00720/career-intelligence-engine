"""Tests for the security/correctness hardening (review fixes).

Covers: API-key auth enforcement, key->tenant binding (cross-tenant IDOR closed),
per-tenant rate limiting, LLM cache tenant isolation, and worker-pool retry
draining (no early-exit dropped retries).
"""
import os
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

from api import auth
from api.app import app
from core import cache, tenancy
from llm_gateway import LLMGateway
from llm_gateway.providers import EchoProvider
from workers import LocalWorkerPool


def _mem_conn() -> sqlite3.Connection:
    # check_same_thread=False: FastAPI runs sync handlers in a threadpool, so the
    # connection is touched from a different thread than the test created it on.
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE workflow_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT UNIQUE,
            tenant_id TEXT, definition TEXT, status TEXT, detail TEXT,
            created_at DATETIME, updated_at DATETIME);
        CREATE TABLE workflow_steps (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, name TEXT,
            status TEXT, attempts INTEGER, output TEXT, error TEXT, updated_at DATETIME);
        CREATE TABLE llm_costs (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT,
            tenant_id TEXT, workflow_id TEXT, agent TEXT, provider TEXT, model TEXT,
            prompt_tokens INTEGER, completion_tokens INTEGER, cost_usd REAL, latency_ms REAL,
            cached INTEGER, created_at DATETIME DEFAULT (DATETIME('now')));
        CREATE TABLE usage_records (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT,
            metric TEXT, amount REAL, created_at DATETIME DEFAULT (DATETIME('now')));
    """)
    return conn


@contextmanager
def _patched_db(conn):
    @contextmanager
    def _ctx():
        yield conn
        conn.commit()

    p = patch("core.database.get_connection", _ctx)
    p.start()
    try:
        yield
    finally:
        p.stop()


def _no_keys_env():
    e = dict(os.environ)
    e.pop("API_KEYS", None)
    return e


# ── API-key auth ────────────────────────────────────────────────────────────────

class TestApiAuth(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        auth._rl_state.clear()

    def tearDown(self):
        auth._rl_state.clear()
        tenancy._current_tenant.set(tenancy.DEFAULT_TENANT)

    def test_open_mode_allows_without_key(self):
        with patch.dict(os.environ, _no_keys_env(), clear=True):
            r = self.client.get("/api/v2/metrics")  # no DB, no auth configured
        self.assertEqual(r.status_code, 200)

    def test_enforced_rejects_missing_key(self):
        with patch.dict(os.environ, {"API_KEYS": "k1:acme"}):
            r = self.client.get("/api/v2/metrics")
        self.assertEqual(r.status_code, 401)

    def test_enforced_rejects_wrong_key(self):
        with patch.dict(os.environ, {"API_KEYS": "k1:acme"}):
            r = self.client.get("/api/v2/metrics", headers={"X-API-Key": "nope"})
        self.assertEqual(r.status_code, 401)

    def test_enforced_accepts_valid_key(self):
        with patch.dict(os.environ, {"API_KEYS": "k1:acme"}):
            r = self.client.get("/api/v2/metrics", headers={"X-API-Key": "k1"})
        self.assertEqual(r.status_code, 200)

    def test_crm_post_requires_key_when_configured(self):
        with patch.dict(os.environ, {"API_KEYS": "k1:acme"}):
            r = self.client.post("/api/applications/status",
                                 json={"job_id": 1, "status": "applied"})
        self.assertEqual(r.status_code, 401)


# ── Tenant isolation (key->tenant binding closes the IDOR) ──────────────────────

class TestTenantIsolation(unittest.TestCase):
    def setUp(self):
        self.conn = _mem_conn()
        self.conn.execute(
            "INSERT INTO workflow_runs (run_id, tenant_id, definition, status) "
            "VALUES (?, ?, ?, ?)", ("run_acme", "acme", "demo", "DONE"))
        self.conn.commit()
        self.client = TestClient(app)
        auth._rl_state.clear()

    def tearDown(self):
        self.conn.close()
        auth._rl_state.clear()
        tenancy._current_tenant.set(tenancy.DEFAULT_TENANT)

    def test_owner_reads_own_run(self):
        with _patched_db(self.conn), patch.dict(os.environ, {"API_KEYS": "ka:acme"}):
            r = self.client.get("/api/v2/workflows/run_acme", headers={"X-API-Key": "ka"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["tenant_id"], "acme")

    def test_other_tenant_cannot_read_run(self):
        with _patched_db(self.conn), patch.dict(os.environ, {"API_KEYS": "kg:globex"}):
            r = self.client.get("/api/v2/workflows/run_acme", headers={"X-API-Key": "kg"})
        self.assertEqual(r.status_code, 404)

    def test_tenant_header_cannot_override_key_binding(self):
        # globex key but spoofed X-Tenant-ID: acme -> still scoped to globex -> 404.
        with _patched_db(self.conn), patch.dict(os.environ, {"API_KEYS": "kg:globex"}):
            r = self.client.get("/api/v2/workflows/run_acme",
                                headers={"X-API-Key": "kg", "X-Tenant-ID": "acme"})
        self.assertEqual(r.status_code, 404)

    def test_other_tenant_cannot_cancel_run(self):
        with _patched_db(self.conn), patch.dict(os.environ, {"API_KEYS": "kg:globex"}):
            r = self.client.post("/api/v2/workflows/run_acme/cancel",
                                 headers={"X-API-Key": "kg"})
        self.assertEqual(r.status_code, 404)


# ── Rate limiting ───────────────────────────────────────────────────────────────

class TestRateLimit(unittest.TestCase):
    def setUp(self):
        self.conn = _mem_conn()
        self.client = TestClient(app)
        auth._rl_state.clear()

    def tearDown(self):
        self.conn.close()
        auth._rl_state.clear()
        tenancy._current_tenant.set(tenancy.DEFAULT_TENANT)

    def test_429_over_budget(self):
        with _patched_db(self.conn), \
                patch.object(auth, "_RL_MAX", 2), \
                patch.dict(os.environ, {"API_KEYS": "k1:acme"}):
            h = {"X-API-Key": "k1"}
            r1 = self.client.get("/api/v2/workflows", headers=h)
            r2 = self.client.get("/api/v2/workflows", headers=h)
            r3 = self.client.get("/api/v2/workflows", headers=h)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r3.status_code, 429)


# ── LLM cache tenant isolation ──────────────────────────────────────────────────

class TestCacheTenantIsolation(unittest.TestCase):
    def setUp(self):
        self.conn = _mem_conn()
        cache.reset_cache()

    def tearDown(self):
        self.conn.close()
        cache.reset_cache()
        tenancy._current_tenant.set(tenancy.DEFAULT_TENANT)

    def test_no_cross_tenant_cache_hit(self):
        with _patched_db(self.conn):
            gw = LLMGateway(primary=EchoProvider())
            r_acme = gw.complete("confidential prompt", model="m", tenant_id="acme")
            r_globex = gw.complete("confidential prompt", model="m", tenant_id="globex")
            r_acme2 = gw.complete("confidential prompt", model="m", tenant_id="acme")
        self.assertFalse(r_acme.cached)       # first call, miss
        self.assertFalse(r_globex.cached)     # different tenant -> NOT served acme's cache
        self.assertTrue(r_acme2.cached)       # same tenant repeat -> cache hit


# ── Worker pool: retries are never dropped by early termination ─────────────────

class TestWorkerPoolTermination(unittest.TestCase):
    def test_all_flaky_tasks_reach_dead_letter(self):
        pool = LocalWorkerPool(num_workers=4)
        for _ in range(20):
            pool.submit(lambda: (_ for _ in ()).throw(RuntimeError("boom")), max_retries=2)
        report = pool.run()
        self.assertEqual(len(report.failed), 20)

    def test_late_retries_complete(self):
        # Every task fails once (re-queued) then succeeds. The old early-exit
        # behaviour could drop retries enqueued after peers had idled out.
        pool = LocalWorkerPool(num_workers=4)
        state: dict[int, int] = {}

        def make(i: int):
            def f():
                state[i] = state.get(i, 0) + 1
                if state[i] < 2:
                    raise RuntimeError("retry once")
                return i
            return f

        for i in range(30):
            pool.submit(make(i), max_retries=3)
        report = pool.run()
        self.assertEqual(len(report.completed), 30)
        self.assertEqual(len(report.failed), 0)


if __name__ == "__main__":
    unittest.main()
