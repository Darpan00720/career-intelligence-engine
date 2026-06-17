"""Production validation: API endpoints, determinism, serialization (Phase 8).

Uses FastAPI TestClient (offline; no LLM hook -> deterministic degraded path).
"""
import hashlib
import unittest
import warnings

# R7 (Phase 8B): isolate a THIRD-PARTY warning before importing TestClient.
# starlette 1.3.1 emits StarletteDeprecationWarning ("install httpx2") on
# testclient import — entirely third-party, not our code. Filtered here (test
# bootstrap only) so strict `-W error::UserWarning` runs do not fail on it.
# unittest discover imports this module top-level, so the filter must live here
# (tests/__init__.py is not executed by `discover -s tests`).
try:
    from starlette.exceptions import StarletteDeprecationWarning
    warnings.filterwarnings("ignore", category=StarletteDeprecationWarning)
except Exception:
    warnings.filterwarnings(
        "ignore", message=r".*httpx.*starlette\.testclient.*deprecated.*")

from fastapi.testclient import TestClient

from api.app import app


class TestApiEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        # Close the singleton checkpointer connection (R3: no leaked sqlite conn).
        from services.career_service import shutdown
        shutdown()

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "healthy"})

    def test_version(self):
        r = self.client.get("/version")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["version"], "1.0.0")
        self.assertEqual(body["name"], "Career Intelligence Engine")
        self.assertTrue(body["system_certified"])

    def test_ready(self):
        r = self.client.get("/ready")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn(body["status"], ("ready", "not_ready"))
        self.assertTrue(body["database"])        # live DB present
        self.assertTrue(body["graph_compiled"])
        self.assertIn("checkpoint_storage", body)
        self.assertIn("embeddings_loaded", body)

    def test_openapi_available(self):
        r = self.client.get("/openapi.json")
        self.assertEqual(r.status_code, 200)
        paths = r.json()["paths"]
        for p in ("/analyze", "/resume/{thread_id}", "/runs/{thread_id}",
                  "/health", "/ready", "/version"):
            self.assertIn(p, paths)

    def test_analyze_returns_api_response(self):
        r = self.client.post("/analyze", json={"thread_id": "api_a", "limit": 3})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        # api_response.version is the (frozen) output-experience spec version,
        # distinct from the release version "1.0.0" served by /version.
        self.assertEqual(body["version"], "1.0")
        for key in ("summary", "dashboard", "export_payload"):
            self.assertIn(key, body)

    def test_request_validation(self):
        # missing required thread_id -> 422
        r = self.client.post("/analyze", json={"limit": 3})
        self.assertEqual(r.status_code, 422)

    def test_get_run_found_and_missing(self):
        self.client.post("/analyze", json={"thread_id": "api_run", "limit": 3})
        r = self.client.get("/runs/api_run")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["found"])
        r2 = self.client.get("/runs/does_not_exist")
        self.assertEqual(r2.status_code, 200)
        self.assertFalse(r2.json()["found"])

    def test_resume_zero_rerun(self):
        self.client.post("/analyze", json={"thread_id": "api_res", "limit": 3})
        r = self.client.post("/resume/api_res")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["success"])

    def test_determinism_10_requests(self):
        # 10 independent fresh runs -> identical api_response payloads
        hashes = set()
        for i in range(10):
            r = self.client.post("/analyze", json={"thread_id": f"det_{i}", "limit": 3})
            self.assertEqual(r.status_code, 200)
            hashes.add(hashlib.sha256(r.text.encode()).hexdigest())
        self.assertEqual(len(hashes), 1, "API responses must be byte-identical across runs")

    def test_correlation_id_header(self):
        r = self.client.get("/health", headers={"x-correlation-id": "abc-123"})
        self.assertEqual(r.headers.get("x-correlation-id"), "abc-123")

    def test_readiness_embeddings_truthful(self):
        # R4: embeddings_loaded reflects genuine availability, not import.
        from unittest.mock import patch
        from core import semantic_matcher
        from services.career_service import _check_embeddings
        self.assertTrue(_check_embeddings())  # model loadable in this env
        with patch.object(semantic_matcher, "_model", None), \
             patch.object(semantic_matcher, "warm_embedding_model", return_value=False):
            self.assertFalse(_check_embeddings())  # truthfully reports unavailable


if __name__ == "__main__":
    unittest.main()
