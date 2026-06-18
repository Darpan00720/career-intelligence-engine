"""Multi-agent career workflow tests — end-to-end, parallelism, checkpointing,
recovery, interruption, human-in-the-loop, plus load and chaos."""
import unittest

from tests._career_support import CareerDB
from core import tenancy
from core.career_graph import CareerGraph
from core.career_workflow import (
    AWAITING_APPROVAL,
    CANCELLED,
    COMPLETED,
    FAILED,
    CareerWorkflow,
    ExplanationAgent,
    GapAnalysisAgent,
)

RESUME = ("Jane Roe. Skilled in Python, SQL, and product management. "
          "Acme Corp, Jan 2018 - Present. MBA POLIMI. AWS Certified.")


def _graph():
    g = CareerGraph()
    g.add_role("AI Product Manager",
               required_skills=["python", "product management", "machine learning", "sql"])
    g.add_skill("machine learning")
    g.add_prerequisite("machine learning", "python")
    return g


class TestWorkflowEndToEnd(CareerDB):
    def test_full_pipeline_completes(self):
        wf = CareerWorkflow(graph=_graph())
        rid = wf.start(RESUME, "AI Product Manager", full_name="Jane Roe",
                       demand={"ai-product-manager": 8})
        run = wf.get_run(rid)
        self.assertEqual(run["status"], COMPLETED)
        bb = run["state"]["blackboard"]
        # every agent contributed to the shared blackboard
        for key in ("profile", "skill_profile", "gap", "recommendations",
                    "learning_path", "explanation"):
            self.assertIn(key, bb)
        self.assertEqual(set(run["state"]["completed"]), set(CareerWorkflow.GRAPH))

    def test_recommendation_and_learning_run_in_parallel_batch(self):
        wf = CareerWorkflow(graph=_graph())
        # both depend only on gap_analysis → selected as one batch
        batch = wf._select_batch(["recommendation", "learning_path"])
        self.assertEqual(set(batch), {"recommendation", "learning_path"})

    def test_memory_sharing(self):
        wf = CareerWorkflow(graph=_graph())
        wf.start(RESUME, "AI Product Manager", full_name="Jane Roe")
        # the resume agent shared a memory other agents can retrieve
        hits = wf.memory.long_term.retrieve("candidate parsed skills", k=3)
        self.assertTrue(hits)


class TestCheckpointingAndRecovery(CareerDB):
    def test_recovery_after_failure(self):
        state = {"fail": True}
        wf = CareerWorkflow(graph=_graph())

        class FlakyGap(GapAnalysisAgent):
            def run(self, bb, *, tenant_id=None):
                if state["fail"]:
                    raise RuntimeError("transient")
                return super().run(bb, tenant_id=tenant_id)

        wf.agents["gap_analysis"] = FlakyGap(memory=wf.memory)
        rid = wf.start(RESUME, "AI Product Manager")
        run = wf.get_run(rid)
        self.assertEqual(run["status"], FAILED)
        # resume + skills completed before the failure; checkpoint persisted them
        self.assertIn("resume", run["state"]["completed"])
        self.assertIn("skills", run["state"]["completed"])
        self.assertNotIn("gap_analysis", run["state"]["completed"])

        state["fail"] = False
        wf.resume(rid)
        self.assertEqual(wf.get_run(rid)["status"], COMPLETED)

    def test_interruption(self):
        wf = CareerWorkflow(graph=_graph())
        rid = wf.start(RESUME, "AI Product Manager")
        self.assertEqual(wf.get_run(rid)["status"], COMPLETED)
        # reset to a mid-run checkpoint, interrupt, then resume → cancelled
        run = wf.get_run(rid)
        state = run["state"]
        state["completed"] = ["resume"]
        wf._save(rid, run["tenant_id"], "RUNNING", state)
        wf.interrupt(rid)
        wf.resume(rid)
        self.assertEqual(wf.get_run(rid)["status"], CANCELLED)


class TestHumanInTheLoop(CareerDB):
    def test_pauses_for_approval_then_completes(self):
        wf = CareerWorkflow(graph=_graph(), require_approval=True)
        rid = wf.start(RESUME, "AI Product Manager")
        self.assertEqual(wf.get_run(rid)["status"], AWAITING_APPROVAL)
        # before approval, no recommendations were generated
        self.assertNotIn("recommendations", wf.get_run(rid)["state"]["blackboard"])
        wf.approve(rid)
        wf.resume(rid)
        self.assertEqual(wf.get_run(rid)["status"], COMPLETED)


class TestLoadAndChaos(CareerDB):
    def test_load_many_workflows(self):
        wf = CareerWorkflow(graph=_graph())
        statuses = [wf.get_run(wf.start(RESUME, "AI Product Manager"))["status"]
                    for _ in range(25)]
        self.assertTrue(all(s == COMPLETED for s in statuses))

    def test_chaos_explanation_agent_failure_isolated(self):
        wf = CareerWorkflow(graph=_graph())

        class BoomExplain(ExplanationAgent):
            def run(self, bb, *, tenant_id=None):
                raise RuntimeError("explainer down")

        wf.agents["explanation"] = BoomExplain(memory=wf.memory)
        rid = wf.start(RESUME, "AI Product Manager")
        run = wf.get_run(rid)
        self.assertEqual(run["status"], FAILED)
        # upstream results survived the checkpoint despite the late failure
        self.assertIn("recommendation", run["state"]["completed"])

    def test_tenant_isolation_across_runs(self):
        wf = CareerWorkflow(graph=_graph())
        with tenancy.use_tenant("acme"):
            rid = wf.start(RESUME, "AI Product Manager", tenant_id="acme")
        self.assertEqual(wf.get_run(rid)["tenant_id"], "acme")


if __name__ == "__main__":
    unittest.main(verbosity=2)
