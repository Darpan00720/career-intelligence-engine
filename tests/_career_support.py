"""Shared helpers for the v5.5 Career Intelligence test suites.

Not a test module (no Test* classes) so unittest discovery ignores it.

Uses a real temp-file SQLite DB initialized via ``database.initialize()`` so the
exact production schema (including the v5.5 additive tables + indexes) is
exercised, and connections work across threads (parallel workflow steps).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import config, database, tenancy
from core.llm_router import LLMRouter, ProviderManager
from llm_gateway.providers import LocalProvider


def local_router() -> LLMRouter:
    return LLMRouter(providers=ProviderManager(providers={"local": LocalProvider()},
                                               order=["local"]))


class CareerDB(unittest.TestCase):
    """Initializes a fresh temp-file DB with the full production schema."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmp.name) / "career.db")
        self._p = patch.object(config, "DB_PATH", self._db_path)
        self._p.start()
        database.initialize()
        tenancy._current_tenant.set(tenancy.DEFAULT_TENANT)

    def tearDown(self):
        self._p.stop()
        tenancy._current_tenant.set(tenancy.DEFAULT_TENANT)
        self._tmp.cleanup()
