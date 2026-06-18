"""LLM orchestration tests — routing, failover, budgeting, streaming, context."""
import unittest

from tests._agent_support import local_router
from core.llm_router import AllProvidersFailed, ContextBuilder, LLMRouter, ProviderManager
from llm_gateway.budget import BudgetManager
from llm_gateway.providers import LocalProvider, Provider


class TestLLMRouter(unittest.TestCase):
    def test_basic_completion(self):
        res = local_router().complete("hello", task="research", tenant_id="acme")
        self.assertEqual(res.provider, "local")
        self.assertGreaterEqual(res.cost_usd, 0.0)
        self.assertGreater(res.total_tokens, 0)

    def test_provider_failover(self):
        class Boom(Provider):
            name = "boom"
            def complete(self, p, m, mt=1024, **k):
                raise RuntimeError("down")
        pm = ProviderManager(providers={"boom": Boom(), "local": LocalProvider()},
                             order=["boom", "local"])
        r = LLMRouter(providers=pm)
        res = r.complete("hi", tenant_id="acme")
        self.assertEqual(res.provider, "local")
        self.assertEqual(r.failover_count, 1)

    def test_all_providers_fail(self):
        class Boom(Provider):
            name = "boom"
            def complete(self, p, m, mt=1024, **k):
                raise RuntimeError("down")
        r = LLMRouter(providers=ProviderManager(providers={"boom": Boom()}, order=["boom"]))
        with self.assertRaises(AllProvidersFailed):
            r.complete("hi")

    def test_budget_downgrades_model(self):
        budgets = BudgetManager()
        budgets.set_budget("acme", max_cost_usd=1.0)
        budgets.consume("acme", 0, 0.85)
        r = LLMRouter(providers=ProviderManager(providers={"local": LocalProvider()},
                                                order=["local"]), budgets=budgets)
        self.assertEqual(r.select_model("research", "acme"), "claude-haiku-4-5-20251001")

    def test_streaming(self):
        chunks = list(local_router().stream("stream this please", tenant_id="acme"))
        self.assertTrue(chunks)

    def test_context_builder_compression(self):
        cb = ContextBuilder(max_tokens=10)
        text = cb.build(system="S" * 100, user="U" * 100)
        self.assertIn("context compressed", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
