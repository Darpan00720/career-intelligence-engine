"""v6 Phase 2 tests: deterministic planner_node + plan generation.

Covers intent classification, transitive-dependency selection, DAG validity,
the node's state update, and the full-pipeline fallback. No routing/execution is
exercised — Phase 2 only produces and stores a plan.
"""
import unittest

from core.workflow_dag import DependencyGraph, WorkflowNode
from graph.planner import (
    CANONICAL_DEPS,
    CANONICAL_ORDER,
    build_execution_plan,
    classify_intent,
    planner_node,
    select_agents,
)
from schemas.execution import ExecutionPlan


class TestClassifyIntent(unittest.TestCase):
    def test_empty_query_is_full_pipeline(self):
        self.assertEqual(classify_intent(""), ("output_experience", "full_pipeline"))
        self.assertEqual(classify_intent("   "), ("output_experience", "full_pipeline"))

    def test_unmatched_query_defaults_full(self):
        target, kind = classify_intent("please do everything end to end")
        self.assertEqual((target, kind), ("output_experience", "full_pipeline"))

    def test_keyword_routing(self):
        self.assertEqual(classify_intent("just ingest jobs")[0], "job_ingestion")
        self.assertEqual(classify_intent("rescore my pipeline")[0], "scoring")
        self.assertEqual(classify_intent("rank the shortlist")[0], "prioritization")
        self.assertEqual(classify_intent("what should I apply to?")[0], "recommendations")
        self.assertEqual(classify_intent("build me a dashboard")[0], "output_experience")

    def test_case_insensitive(self):
        self.assertEqual(classify_intent("RESCORE NOW")[0], "scoring")

class TestClassifyIntent(unittest.TestCase):
    ...

    def test_case_insensitive(self):
        self.assertEqual(classify_intent("RESCORE NOW")[0], "scoring")

    def test_score_keyword(self):
        self.assertEqual(
            classify_intent("score jobs"),
            ("scoring", "rescore"),
        )

class TestSelectAgents(unittest.TestCase):
    def test_transitive_prereqs_in_canonical_order(self):
        self.assertEqual(
            select_agents("scoring"),
            ["profile_strategy", "job_ingestion", "taxonomy", "scoring"])

    def test_single_dependency_free_agent(self):
        self.assertEqual(select_agents("profile_strategy"), ["profile_strategy"])

    def test_full_pipeline(self):
        self.assertEqual(select_agents("output_experience"), CANONICAL_ORDER)

    def test_unknown_target_falls_back_to_full(self):
        self.assertEqual(select_agents("does_not_exist"), CANONICAL_ORDER)


class TestBuildExecutionPlan(unittest.TestCase):

    def test_returns_execution_plan_with_query(self):
        plan = build_execution_plan("rescore my jobs")

        self.assertIsInstance(plan, ExecutionPlan)
        self.assertEqual(plan.user_query, "rescore my jobs")
        self.assertEqual(plan.intent_kind, "rescore")
        self.assertEqual(
            plan.task_ids(),
            ["profile_strategy", "job_ingestion", "taxonomy", "scoring"],
        )

    def test_score_jobs_routes_to_scoring(self):
        plan = build_execution_plan("score jobs")

        self.assertEqual(plan.intent_kind, "rescore")
        self.assertEqual(
            plan.task_ids(),
            [
                "profile_strategy",
                "job_ingestion",
                "taxonomy",
                "scoring",
            ],
        )

    def test_deps_are_prefix_closed_and_internal(self):
        plan = build_execution_plan("rescore")
        ids = set(plan.task_ids())

        for t in plan.tasks:
            self.assertTrue(set(t.depends_on) <= ids)
            self.assertEqual(t.depends_on, CANONICAL_DEPS[t.id])


    def test_full_pipeline_has_all_eight_agents(self):
        plan = build_execution_plan("generate full report and export")
        self.assertEqual(len(plan.tasks), 8)

    def test_plan_id_unique(self):
        a = build_execution_plan("rescore")
        b = build_execution_plan("rescore")
        self.assertNotEqual(a.plan_id, b.plan_id)

    def test_explicit_plan_id(self):
        self.assertEqual(build_execution_plan("x", plan_id="fixed").plan_id, "fixed")


class TestPlannerNode(unittest.TestCase):
    def test_stores_plan_and_pending_tasks(self):
        out = planner_node({"user_query": "rescore my jobs"})
        self.assertIn("execution_plan", out)
        self.assertIsInstance(out["execution_plan"], ExecutionPlan)
        self.assertEqual(out["pending_tasks"], out["execution_plan"].task_ids())
        self.assertEqual(out["audit_log"], ["planner"])

    def test_missing_query_defaults_to_full_pipeline(self):
        out = planner_node({})
        self.assertEqual(out["execution_plan"].intent_kind, "full_pipeline")
        self.assertEqual(len(out["pending_tasks"]), 8)

    def test_does_not_set_phase_or_route(self):
        # Phase 2 must not influence supervisor routing.
        out = planner_node({"user_query": "rank jobs"})
        self.assertNotIn("phase", out)
        self.assertNotIn("next_node", out)
        self.assertNotIn("current_task", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
