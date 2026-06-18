"""Agent runtime tests — turns, delegation/handoff, plans, checkpoint/recovery,
interruption, plus chaos and backward compatibility."""
import unittest

from tests._agent_support import MemDB, local_router
from core.agent_registry import AgentRegistry, AgentSpec
from core.agent_runtime import AgentCoordinator, AgentExecutor, AgentRuntime
from core.llm_router import LLMRouter, ProviderManager
from llm_gateway.providers import Completion, LocalProvider, Provider


class TestAgentRuntime(MemDB):
    def _registry(self):
        reg = AgentRegistry()
        reg.register(AgentSpec("planner", capabilities=["plan"]))
        reg.register(AgentSpec("researcher", capabilities=["research"]))
        return reg

    def _runtime(self, registry=None):
        coord = AgentCoordinator(AgentExecutor(local_router()), registry or self._registry())
        return AgentRuntime(coord)

    def test_single_turn(self):
        res = AgentExecutor(local_router()).run_turn(
            AgentSpec("planner", system_prompt="plan"), user_input="find roles",
            tenant_id="default")
        self.assertEqual(res.agent, "planner")
        self.assertTrue(res.text)

    def test_delegation_and_handoff(self):
        coord = AgentCoordinator(AgentExecutor(local_router()), self._registry())
        self.assertEqual(coord.delegate("researcher", task="research", tenant_id="default").agent,
                         "researcher")
        self.assertEqual(coord.handoff("plan", user_input="plan it", tenant_id="default").agent,
                         "planner")

    def test_plan_runs_all_agents(self):
        rt = self._runtime()
        rid = rt.start(["planner", "researcher"], user_input="go", tenant_id="default")
        run = rt.get_run(rid)
        self.assertEqual(run["status"], "COMPLETED")
        self.assertEqual(run["state"]["step"], 2)
        self.assertEqual(len(run["state"]["results"]), 2)

    def test_checkpoint_recovery_after_failure(self):
        state = {"fail": True}
        def researcher_handler(ctx):
            if state["fail"]:
                raise RuntimeError("transient outage")
            return {"text": "done"}
        reg = AgentRegistry()
        reg.register(AgentSpec("planner", capabilities=["plan"]))
        reg.register(AgentSpec("researcher", handler=researcher_handler))
        rt = self._runtime(reg)
        rid = rt.start(["planner", "researcher"], user_input="go", tenant_id="default")
        self.assertEqual(rt.get_run(rid)["status"], "FAILED")
        self.assertEqual(rt.get_run(rid)["state"]["step"], 1)
        state["fail"] = False
        rt.resume(rid)
        run = rt.get_run(rid)
        self.assertEqual(run["status"], "COMPLETED")
        self.assertEqual(run["state"]["step"], 2)

    def test_interruption(self):
        rt = self._runtime()
        rid = rt.start(["planner"], user_input="go", tenant_id="default")
        rt._save(rid, "default", "RUNNING", {**rt.get_run(rid)["state"], "step": 0})
        rt.interrupt(rid)
        rt.resume(rid)
        self.assertEqual(rt.get_run(rid)["status"], "CANCELLED")


class TestChaos(MemDB):
    def test_chaos_provider_recovers(self):
        calls = {"n": 0}
        class Flaky(Provider):
            name = "flaky"
            def complete(self, p, m, mt=1024, **k):
                calls["n"] += 1
                if calls["n"] <= 2:
                    raise RuntimeError("blip")
                return Completion("ok", 1, 1, m)
        pm = ProviderManager(providers={"flaky": Flaky(), "local": LocalProvider()},
                             order=["flaky", "local"])
        r = LLMRouter(providers=pm)
        for _ in range(3):
            self.assertIsNotNone(r.complete("x", tenant_id="t"))


class TestBackwardCompat(unittest.TestCase):
    def test_v5x_imports(self):
        from llm_gateway import LLMGateway, ModelRouter, get_gateway  # noqa: F401
        from core import workflow_dag, async_workflow, tenancy  # noqa: F401
        from agents.base_agent import BaseAgent  # noqa: F401
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
