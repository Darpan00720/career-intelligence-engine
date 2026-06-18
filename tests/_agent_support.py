"""Shared helpers for the v5.4 agent-platform test suites.

Not a test module (no Test* classes) so unittest discovery ignores it.
"""
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from core import tenancy
from core.llm_router import LLMRouter, ProviderManager
from llm_gateway.providers import LocalProvider


def local_router() -> LLMRouter:
    return LLMRouter(providers=ProviderManager(providers={"local": LocalProvider()},
                                               order=["local"]))


def mem_conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT UNIQUE, tenant_id TEXT, user_id TEXT, title TEXT,
            summary TEXT, created_at DATETIME, updated_at DATETIME);
        CREATE TABLE conversation_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT, tenant_id TEXT, role TEXT, content TEXT, agent TEXT,
            tokens INTEGER, created_at DATETIME);
        CREATE TABLE agent_memories (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT,
            scope TEXT, content TEXT, embedding TEXT, created_at DATETIME, expires_at DATETIME);
        CREATE TABLE agent_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT UNIQUE,
            tenant_id TEXT, status TEXT, state TEXT, updated_at DATETIME);
        CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT,
            actor TEXT, action TEXT, entity TEXT, detail TEXT, created_at DATETIME);
    """)
    return conn


class MemDB(unittest.TestCase):
    """Patches core.database.get_connection to a shared in-memory DB per test."""

    def setUp(self):
        self.conn = mem_conn()

        @contextmanager
        def _ctx():
            yield self.conn
            self.conn.commit()

        self._p = patch("core.database.get_connection", _ctx)
        self._p.start()
        tenancy._current_tenant.set("default")

    def tearDown(self):
        self._p.stop()
        self.conn.close()
