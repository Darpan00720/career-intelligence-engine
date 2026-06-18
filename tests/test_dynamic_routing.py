"""v6 Phase 3 tests: dependency-based routing + completion marking.

Covers the router (legacy fallback preserved, ready-set dispatch, parallel
fan-out, completion, deadlock guard), the supervisor's completion marking, and
end-to-end plan execution through the compiled graph with a checkpointer
(reducers + checkpointing preserved).
"""
import tempfile
import unittest
from pathlib import Path

from langgraph.graph import END

from graph.build import build_graph
from graph.nodes import EXECUTION_LOG, reset_execution_log, supervisor_node
from graph.planner import build_execution_plan
from graph.routing import as_plan, ready_agents, route_from_supervisor
from graph.state import new_career_state, validate_state
from schemas.control import Phase
from schemas.execution import ExecutionPlan, Task
from tests._support import close_all, make_saver


def _plan(*tasks: Task, kind: str = "test") -> ExecutionPlan:
    return ExecutionPlan(plan_id="p", intent_kind=kind, tasks=list(tasks))


class TestRouterLegacyFallback(unittest.TestCase):
    """No execution_plan => exact legacy phase-based behaviour."""

    def test_phase_routing_unchanged(self):
        self.assertEqual(route_from_supervisor({}), "profile_strategy")
        self.assertEqual(route_from_supervisor({"phase": Phase.SCORING}), "opportunity_intel")
        self.assertEqual(route_from_supervisor({"phase": Phase.OUTPUT}), END)


class TestDependencyRouter(unittest.TestCase):
    def test_dispatches_only_dependency_satisfied(self):
        plan = build_execution_plan("")  # full linear pipeline
        # nothing completed -> only the dependency-free root is ready
        self.assertEqual(route_from_supervisor({"execution_plan": plan}),
                         ["profile_strategy"])
        # profile done -> ingestion unlocked, scoring still blocked
        st = {"execution_plan": plan, "completed_tasks": ["profile_strategy"]}
        self.assertEqual(route_from_supervisor(st), ["job_ingestion"])

    def test_parallel_ready_returns_list(self):
        # two dependency-free tasks -> both dispatched in one superstep
        plan = _plan(Task(id="a", agent="profile_strategy"),
                     Task(id="b", agent="taxonomy"))
        out = route_from_supervisor({"execution_plan": plan})
        self.assertIsInstance(out, list)
        self.assertCountEqual(out, ["profile_strategy", "taxonomy"])

    def test_end_when_all_complete(self):
        plan = build_execution_plan("ingest jobs")  # profile + ingestion
        st = {"execution_plan": plan, "completed_tasks": plan.task_ids()}
        self.assertEqual(route_from_supervisor(st), END)

    def test_deadlock_guard_ends_cleanly(self):
        # a task whose dependency can never be satisfied -> END, no spin
        plan = _plan(Task(id="x", agent="scoring", depends_on=["missing_dep"]))
        self.assertEqual(route_from_supervisor({"execution_plan": plan}), END)

    def test_as_plan_coerces_dict(self):
        plan = build_execution_plan("rescore")
        as_dict = plan.model_dump()
        self.assertIsInstance(as_plan(as_dict), ExecutionPlan)
        self.assertEqual(route_from_supervisor({"execution_plan": as_dict}),
                         ["profile_strategy"])

    def test_ready_agents_helper(self):
        plan = build_execution_plan("")
        self.assertEqual(ready_agents({"execution_plan": plan}), ["profile_strategy"])
        self.assertEqual(ready_agents({}), [])


class TestSupervisorCompletionMarking(unittest.TestCase):
    def test_marks_newly_completed_from_audit_log(self):
        plan = build_execution_plan("")
        out = supervisor_node({
            "execution_plan": plan,
            "audit_log": ["planner", "profile_strategy", "supervisor"],
            "completed_tasks": [],
        })
        self.assertEqual(out["completed_tasks"], ["profile_strategy"])
        self.assertEqual(out["current_task"], "profile_strategy")

    def test_does_not_remark_already_completed(self):
        plan = build_execution_plan("")
        out = supervisor_node({
            "execution_plan": plan,
            "audit_log": ["profile_strategy", "job_ingestion"],
            "completed_tasks": ["profile_strategy"],
        })
        self.assertEqual(out["completed_tasks"], ["job_ingestion"])

    def test_no_plan_is_legacy_behaviour(self):
        out = supervisor_node({"audit_log": []})
        self.assertEqual(out, {"audit_log": ["supervisor"]})
        self.assertNotIn("completed_tasks", out)


class TestEndToEndPlanExecution(unittest.TestCase):
    def setUp(self):
        reset_execution_log()

    def tearDown(self):
        close_all()

    def test_full_pipeline_via_planner_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = build_graph(checkpointer=make_saver(Path(tmp) / "cp.db"),
                                planner_mode=True)
            cfg = {"configurable": {"thread_id": "full"}}
            result = graph.invoke(
                new_career_state(run_id="full", profile_path="candidate_profile.json"), cfg)
            validate_state(result)
            # every task dependency-gated, executed, and marked complete
            self.assertEqual(set(result["completed_tasks"]),
                             {"profile_strategy", "job_ingestion", "taxonomy", "scoring",
                              "opportunity_intel", "prioritization", "recommendations",
                              "output_experience"})
            self.assertTrue(result.get("scored_jobs"))
            workers = [n for n in EXECUTION_LOG if n not in ("supervisor", "planner")]
            self.assertEqual(workers,
                             ["profile_strategy", "job_ingestion", "taxonomy", "scoring",
                              "opportunity_intel", "prioritization", "recommendations",
                              "output_experience"])

    def test_scoped_plan_runs_only_necessary_tasks(self):
        # plan injected directly into state (no planner node) -> router still
        # reads execution_plan from state and stops after the needed tasks.
        plan = build_execution_plan("just ingest jobs")  # profile + ingestion only
        with tempfile.TemporaryDirectory() as tmp:
            graph = build_graph(checkpointer=make_saver(Path(tmp) / "cp.db"))
            cfg = {"configurable": {"thread_id": "scoped"}}
            init = new_career_state(run_id="scoped", profile_path="p.json")
            init["execution_plan"] = plan
            result = graph.invoke(init, cfg)
            self.assertEqual(set(result["completed_tasks"]),
                             {"profile_strategy", "job_ingestion"})
            # downstream tasks never ran (channel may default to [], so assert
            # the node never executed and produced no scores)
            self.assertNotIn("scoring", EXECUTION_LOG)
            self.assertFalse(result.get("scored_jobs"))

    def test_completed_tasks_persisted_in_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            saver = make_saver(Path(tmp) / "cp.db")
            graph = build_graph(checkpointer=saver, planner_mode=True)
            cfg = {"configurable": {"thread_id": "persist"}}
            graph.invoke(new_career_state(run_id="persist", profile_path="p.json"), cfg)
            snapshot = graph.get_state(cfg)
            self.assertEqual(len(snapshot.values.get("completed_tasks", [])), 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
