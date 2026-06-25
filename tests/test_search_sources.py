"""Tests for Adzuna public search + ATS connectors.

All network is mocked — no live HTTP calls.
"""
import os
import unittest
from unittest.mock import patch

from agents import adzuna_search, search_agent


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise search_agent.requests.RequestException(f"HTTP {self.status_code}")


# ── Adzuna public search ────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
