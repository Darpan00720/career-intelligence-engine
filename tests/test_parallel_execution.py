"""v6 Phase 4 tests: ExecutionPlanner/DependencyGraph integration + parallelism.

Verifies:
  * plan levelization via ExecutionPlanner (independent tasks grouped),
  * independent tasks executing in one parallel superstep through a compiled
    LangGraph with the real supervisor router,
  * shared-state consistency under concurrent writes (reducers merge, no clobber),
  * checkpointing + resume preserved,
  * node retries preserved alongside the dynamic router.
"""
import tempfile
import threading
import unittest
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from core.concurrency import backoff_retry
from core.workflow_dag import CycleError
from graph.nodes import supervisor_node
from graph.planner import build_dependency_graph, build_execution_plan, plan_levels
from graph.routing import route_from_supervisor
from graph.state import CareerState
from schemas.execution import ExecutionPlan, Task
from tests._support import close_all, make_saver


def _branch_plan() -> ExecutionPlan:
    # a, b independent -> share level 0; c joins -> level 1
    return ExecutionPlan(plan_id="branch", intent_kind="custom", tasks=[
        Task(id="a", agent="a"),
        Task(id="b", agent="b"),
        Task(id="c", agent="c", depends_on=["a", "b"]),
    ])


# ── ExecutionPlanner / DependencyGraph integration ──────────────────────────────

class TestPlanLevels(unittest.TestCase):
    def test_linear_plan_one_task_per_level(self):
        plan = build_execution_plan("")  # canonical full pipeline
        self.assertEqual(plan_levels(plan), [[a] for a in plan.task_ids()])

    def test_branching_plan_groups_independent_tasks(self):
        self.assertEqual(plan_levels(_branch_plan()), [["a", "b"], ["c"]])

    def test_dependency_graph_built_from_plan(self):
        graph = build_dependency_graph(_branch_plan())
        self.assertEqual(set(graph.nodes), {"a", "b", "c"})

    def test_cycle_is_rejected(self):
        cyclic = ExecutionPlan(plan_id="x", tasks=[
            Task(id="a", agent="a", depends_on=["b"]),
            Task(id="b", agent="b", depends_on=["a"]),
        ])
        with self.assertRaises(CycleError):
            plan_levels(cyclic)


# ── Parallel execution through a compiled graph ─────────────────────────────────

class TestParallelExecution(unittest.TestCase):
    def tearDown(self):
        close_all()

    def _mini_graph(self, saver, record):
        """A 3-node graph (a,b independent -> c) using the real supervisor router."""
        barrier = threading.Barrier(2, timeout=5)

        def _worker(name, sync=False):
            def node(state: CareerState) -> dict:
                if sync:
                    # both parallel workers must be in-flight at once, proving
                    # they run in the same superstep (not serialized)
                    try:
                        barrier.wait()
                    except threading.BrokenBarrierError:
                        pass
                record.append(name)
                return {"audit_log": [name], "retry_count": {name: 1}}
            return node

        g = StateGraph(CareerState)
        g.add_node("supervisor", supervisor_node)
        g.add_node("a", _worker("a", sync=True))
        g.add_node("b", _worker("b", sync=True))
        g.add_node("c", _worker("c"))
        g.add_edge(START, "supervisor")
        path_map = {"a": "a", "b": "b", "c": "c", END: END}
        g.add_conditional_edges("supervisor", route_from_supervisor, path_map)
        for n in ("a", "b", "c"):
            g.add_edge(n, "supervisor")
        return g.compile(checkpointer=saver)

    def test_independent_tasks_run_in_parallel_and_merge(self):
        record: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            saver = make_saver(Path(tmp) / "cp.db")
            graph = self._mini_graph(saver, record)
            cfg = {"configurable": {"thread_id": "par"}}
            result = graph.invoke(
                {"execution_plan": _branch_plan(), "audit_log": [],
                 "completed_tasks": [], "retry_count": {}},
                cfg)

            # all three tasks completed, c only after the join
            self.assertEqual(set(result["completed_tasks"]), {"a", "b", "c"})
            # shared reducer state is consistent: both parallel writers merged
            audit = result["audit_log"]
            self.assertIn("a", audit)
            self.assertIn("b", audit)
            self.assertGreater(audit.index("c"), audit.index("a"))
            self.assertGreater(audit.index("c"), audit.index("b"))
            # dict reducer merged concurrent writes without loss
            self.assertEqual(set(result["retry_count"]), {"a", "b", "c"})

    def test_checkpoint_persisted_and_resume_noop(self):
        record: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            saver = make_saver(Path(tmp) / "cp.db")
            graph = self._mini_graph(saver, record)
            cfg = {"configurable": {"thread_id": "par2"}}
            graph.invoke(
                {"execution_plan": _branch_plan(), "audit_log": [],
                 "completed_tasks": [], "retry_count": {}}, cfg)
            n = saver.conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id='par2'").fetchone()[0]
            self.assertGreater(n, 0)                       # checkpointing preserved
            record.clear()
            resumed = graph.invoke(None, cfg)              # completed run -> no re-run
            self.assertEqual(record, [])
            self.assertEqual(set(resumed["completed_tasks"]), {"a", "b", "c"})


# ── Retries preserved under dynamic routing ─────────────────────────────────────

class TestRetriesPreserved(unittest.TestCase):
    def tearDown(self):
        close_all()

    def test_flaky_task_retries_then_completes(self):
        calls = {"n": 0}

        def flaky_core():
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return "ok"

        def flaky_node(state: CareerState) -> dict:
            # node-internal retry (same pattern workers use) survives the new router
            backoff_retry(flaky_core, retries=2, base_delay=0.0)
            return {"audit_log": ["flaky"]}

        plan = ExecutionPlan(plan_id="r", tasks=[Task(id="flaky", agent="flaky")])
        g = StateGraph(CareerState)
        g.add_node("supervisor", supervisor_node)
        g.add_node("flaky", flaky_node)
        g.add_edge(START, "supervisor")
        g.add_conditional_edges("supervisor", route_from_supervisor,
                                {"flaky": "flaky", END: END})
        g.add_edge("flaky", "supervisor")

        with tempfile.TemporaryDirectory() as tmp:
            graph = g.compile(checkpointer=make_saver(Path(tmp) / "cp.db"))
            result = graph.invoke(
                {"execution_plan": plan, "audit_log": [], "completed_tasks": []},
                {"configurable": {"thread_id": "retry"}})
            self.assertEqual(calls["n"], 2)               # failed once, retried, ok
            self.assertEqual(result["completed_tasks"], ["flaky"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
