"""v6 Phase 1 tests: Task / ExecutionPlan schemas + CareerState planning fields.

Scope is data-only. These tests do NOT exercise routing or graph execution —
they verify the new schemas, their pure helpers, and that the additive
CareerState fields validate while preserving backward compatibility.
"""
import unittest

from pydantic import ValidationError

from graph.state import CareerStateModel, new_career_state, validate_state
from schemas.control import Phase
from schemas.execution import ExecutionPlan, Task, TaskStatus


def _full_plan() -> ExecutionPlan:
    return ExecutionPlan(
        plan_id="p1",
        user_query="find and rank AI PM jobs",
        tasks=[
            Task(id="profile_strategy", agent="profile_strategy", capability="profile"),
            Task(id="job_ingestion", agent="job_ingestion", depends_on=["profile_strategy"]),
            Task(id="taxonomy", agent="taxonomy", depends_on=["job_ingestion"]),
            Task(id="scoring", agent="scoring", depends_on=["taxonomy"]),
        ],
    )


class TestTask(unittest.TestCase):
    def test_defaults(self):
        t = Task(id="scoring", agent="scoring")
        self.assertEqual(t.status, TaskStatus.PENDING)
        self.assertEqual(t.depends_on, [])
        self.assertEqual(t.params, {})
        self.assertFalse(t.optional)

    def test_is_ready(self):
        t = Task(id="scoring", agent="scoring", depends_on=["taxonomy"])
        self.assertFalse(t.is_ready(set()))
        self.assertTrue(t.is_ready({"taxonomy"}))

    def test_independent_lists(self):
        # default_factory must not share mutable state across instances
        a, b = Task(id="a", agent="a"), Task(id="b", agent="b")
        a.depends_on.append("x")
        self.assertEqual(b.depends_on, [])

    def test_extra_field_rejected(self):
        with self.assertRaises(ValidationError):
            Task(id="a", agent="a", bogus=1)


class TestExecutionPlan(unittest.TestCase):
    def test_task_ids_and_get(self):
        plan = _full_plan()
        self.assertEqual(plan.task_ids(),
                         ["profile_strategy", "job_ingestion", "taxonomy", "scoring"])
        self.assertEqual(plan.get("scoring").agent, "scoring")
        self.assertIsNone(plan.get("missing"))

    def test_ready_tasks_progression(self):
        plan = _full_plan()
        # nothing done -> only the dependency-free task is ready
        self.assertEqual([t.id for t in plan.ready_tasks(set())], ["profile_strategy"])
        # profile done -> ingestion becomes ready
        self.assertEqual([t.id for t in plan.ready_tasks({"profile_strategy"})],
                         ["job_ingestion"])

    def test_is_complete(self):
        plan = _full_plan()
        self.assertFalse(plan.is_complete({"profile_strategy"}))
        self.assertTrue(plan.is_complete(set(plan.task_ids())))

    def test_defaults(self):
        plan = ExecutionPlan(plan_id="p")
        self.assertEqual(plan.tasks, [])
        self.assertEqual(plan.intent_kind, "full_pipeline")
        self.assertEqual(plan.planner_version, "v1")
        self.assertIsNotNone(plan.created_at)

    def test_roundtrip_serialization(self):
        # must survive dump/load for checkpoint persistence later
        plan = _full_plan()
        restored = ExecutionPlan.model_validate(plan.model_dump())
        self.assertEqual(restored.task_ids(), plan.task_ids())


class TestCareerStatePlanningFields(unittest.TestCase):
    def test_new_state_initializes_task_lists(self):
        s = new_career_state(run_id="r1", profile_path="p.json")
        self.assertEqual(s["pending_tasks"], [])
        self.assertEqual(s["completed_tasks"], [])
        # plan-only fields stay unset until a planner populates them
        self.assertNotIn("execution_plan", s)
        self.assertNotIn("user_query", s)

    def test_backward_compatible_legacy_state_still_validates(self):
        # A pre-v6 state (no planning fields) must validate unchanged.
        s = new_career_state(run_id="r1", profile_path="p.json")
        model = validate_state(s)
        self.assertEqual(model.run_id, "r1")

    def test_planning_fields_validate(self):
        plan = _full_plan()
        model = validate_state({
            "phase": Phase.INIT,
            "user_query": "rank jobs",
            "execution_plan": plan,
            "pending_tasks": ["scoring"],
            "completed_tasks": ["profile_strategy"],
            "current_task": "job_ingestion",
        })
        self.assertEqual(model.execution_plan.plan_id, "p1")
        self.assertEqual(model.completed_tasks, ["profile_strategy"])

    def test_plan_accepts_dict_form(self):
        # state arriving as plain JSON (e.g. from a checkpoint) coerces to model
        model = validate_state({"execution_plan": _full_plan().model_dump()})
        self.assertIsInstance(model, CareerStateModel)
        self.assertEqual(model.execution_plan.task_ids()[0], "profile_strategy")

    def test_unknown_planning_key_rejected(self):
        with self.assertRaises(ValidationError):
            validate_state({"not_a_field": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
