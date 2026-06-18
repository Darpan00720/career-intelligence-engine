"""DAG Workflow Orchestration (v5.1).

Upgrades the linear v5 workflow engine to a directed-acyclic-graph executor with:

  * topological scheduling (level-by-level), with parallel execution per level
  * conditional branches (node.condition)
  * fan-out / fan-in (nodes accumulate outputs into a shared context by name)
  * sub-workflows (a node runs a child DAG)
  * checkpointing + resume-after-crash (per-node status persisted)
  * dynamic retries and per-node timeouts
  * compensation steps (saga rollback in reverse completion order on failure)

Reuses the workflow_runs / workflow_steps / workflow_events tables for
durability, so resume and partial replay work across process restarts.
"""
from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from core import database
from core.event_log import log_event
from core.workflow_engine import (
    CANCELLED,
    COMPLETED,
    FAILED,
    PARTIAL_SUCCESS,
    RUNNING,
    _run_with_timeout,
)

SKIPPED = "SKIPPED"


@dataclass
class WorkflowNode:
    name: str
    func: Callable[[dict], dict]
    depends_on: list[str] = field(default_factory=list)
    retries: int = 1
    timeout: float | None = None
    condition: Callable[[dict], bool] | None = None
    compensation: Callable[[dict], None] | None = None
    critical: bool = False


class CycleError(ValueError):
    pass


class DependencyGraph:
    """A validated DAG of WorkflowNodes with topological levelization."""

    def __init__(self, nodes: list[WorkflowNode]):
        self.nodes: dict[str, WorkflowNode] = {n.name: n for n in nodes}
        self._validate()

    def _validate(self) -> None:
        for node in self.nodes.values():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    raise ValueError(f"node {node.name!r} depends on unknown {dep!r}")
        self.levels()  # raises CycleError if not a DAG

    def levels(self) -> list[list[str]]:
        """Kahn's algorithm: group nodes into dependency levels (parallel sets)."""
        indegree = {n: len(node.depends_on) for n, node in self.nodes.items()}
        dependents: dict[str, list[str]] = {n: [] for n in self.nodes}
        for n, node in self.nodes.items():
            for dep in node.depends_on:
                dependents[dep].append(n)

        levels: list[list[str]] = []
        ready = sorted(n for n, d in indegree.items() if d == 0)
        seen = 0
        while ready:
            levels.append(ready)
            seen += len(ready)
            nxt: list[str] = []
            for n in ready:
                for m in dependents[n]:
                    indegree[m] -= 1
                    if indegree[m] == 0:
                        nxt.append(m)
            ready = sorted(nxt)
        if seen != len(self.nodes):
            raise CycleError("workflow graph contains a cycle")
        return levels


class ExecutionPlanner:
    """Turns a DependencyGraph into an ordered list of parallel execution levels."""

    @staticmethod
    def plan(graph: DependencyGraph) -> list[list[str]]:
        return graph.levels()


def make_subworkflow_node(name: str, child_graph: DependencyGraph,
                          depends_on: list[str] | None = None) -> WorkflowNode:
    """Wrap a child DAG as a single node (sub-workflow)."""
    def _run(ctx: dict) -> dict:
        engine = DAGWorkflowEngine()
        status = engine.run(f"{name}_sub", child_graph, context=dict(ctx))
        return {f"{name}_status": status}
    return WorkflowNode(name=name, func=_run, depends_on=depends_on or [])


class DAGWorkflowEngine:
    # ── Persistence helpers (shared schema with the linear engine) ──────────────
    def _set_run_status(self, run_id: str, status: str) -> None:
        with database.get_connection() as conn:
            conn.execute(
                "UPDATE workflow_runs SET status = ?, updated_at = DATETIME('now') "
                "WHERE run_id = ?", (status, run_id))

    def _run_status(self, run_id: str) -> str | None:
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT status FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone()
        return row["status"] if row else None

    def _emit(self, run_id: str, event: str, detail: str = "") -> None:
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO workflow_events (run_id, event, detail) VALUES (?, ?, ?)",
                (run_id, event, detail))

    def _save_step(self, run_id: str, name: str, status: str,
                   attempts: int = 0, output: dict | None = None,
                   error: str | None = None) -> None:
        with database.get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM workflow_steps WHERE run_id = ? AND name = ?",
                (run_id, name)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE workflow_steps SET status=?, attempts=?, output=?, error=?, "
                    "updated_at=DATETIME('now') WHERE id=?",
                    (status, attempts, json.dumps(output) if output else None,
                     error, existing["id"]))
            else:
                conn.execute(
                    "INSERT INTO workflow_steps (run_id, name, status, attempts, output, error) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, name, status, attempts,
                     json.dumps(output) if output else None, error))

    def _completed_steps(self, run_id: str) -> dict[str, dict]:
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT name, status, output FROM workflow_steps WHERE run_id = ?",
                (run_id,)).fetchall()
        out = {}
        for r in rows:
            out[r["name"]] = {"status": r["status"],
                              "output": json.loads(r["output"]) if r["output"] else {}}
        return out

    # ── Public API ───────────────────────────────────────────────────────────
    def run(self, name: str, graph: DependencyGraph, *, tenant_id: str | None = None,
            context: dict | None = None, mode: str = "parallel",
            max_workers: int = 4) -> str:
        from core.tenancy import current_tenant
        run_id = uuid.uuid4().hex
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO workflow_runs (run_id, tenant_id, definition, status) "
                "VALUES (?, ?, ?, ?)",
                (run_id, tenant_id or current_tenant(), name, RUNNING))
        return self._execute(run_id, graph, dict(context or {}), mode, max_workers)

    def resume(self, run_id: str, graph: DependencyGraph, *, mode: str = "parallel",
               max_workers: int = 4) -> str:
        ctx: dict = {}
        for node_name, info in self._completed_steps(run_id).items():
            if info["status"] == COMPLETED:
                ctx.update(info["output"])
        self._set_run_status(run_id, RUNNING)
        self._emit(run_id, "resumed")
        return self._execute(run_id, graph, ctx, mode, max_workers, resuming=True)

    def cancel(self, run_id: str) -> None:
        self._set_run_status(run_id, CANCELLED)
        self._emit(run_id, "cancelled")

    # ── Execution ──────────────────────────────────────────────────────────────
    def _run_node(self, node: WorkflowNode, ctx_snapshot: dict, run_id: str) -> dict:
        """Run one node with retries + timeout. Returns a result record."""
        if node.condition is not None and not node.condition(ctx_snapshot):
            self._save_step(run_id, node.name, SKIPPED)
            self._emit(run_id, "node_skipped", node.name)
            return {"name": node.name, "status": SKIPPED, "output": {}}

        attempts, last_error = 0, None
        while attempts <= node.retries:
            attempts += 1
            try:
                output = _run_with_timeout(node.func, ctx_snapshot, node.timeout) or {}
                self._save_step(run_id, node.name, COMPLETED, attempts,
                                output if isinstance(output, dict) else {"result": output})
                self._emit(run_id, "node_completed", node.name)
                return {"name": node.name, "status": COMPLETED,
                        "output": output if isinstance(output, dict) else {}}
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
        self._save_step(run_id, node.name, FAILED, attempts, error=last_error)
        self._emit(run_id, "node_failed", f"{node.name}: {last_error}")
        log_event("pipeline", "dag_node_failed", status="error",
                  error=f"{node.name}: {last_error}", level="ERROR")
        return {"name": node.name, "status": FAILED, "error": last_error,
                "critical": node.critical}

    def _execute(self, run_id: str, graph: DependencyGraph, context: dict,
                 mode: str, max_workers: int, resuming: bool = False) -> str:
        levels = ExecutionPlanner.plan(graph)
        done = self._completed_steps(run_id) if resuming else {}
        completed_order: list[str] = [n for n, i in done.items() if i["status"] == COMPLETED]
        any_failed = False

        for level in levels:
            if self._run_status(run_id) == CANCELLED:
                self._emit(run_id, "cancelled_midrun")
                return CANCELLED

            pending = [graph.nodes[n] for n in level
                       if not (resuming and done.get(n, {}).get("status") == COMPLETED)]
            if not pending:
                continue

            snapshot = dict(context)  # immutable per-level view (no shared mutable state)
            if mode == "parallel" and len(pending) > 1:
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    results = list(pool.map(
                        lambda node: self._run_node(node, snapshot, run_id), pending))
            else:
                results = [self._run_node(node, snapshot, run_id) for node in pending]

            # Merge outputs deterministically after the level completes.
            for res in sorted(results, key=lambda r: r["name"]):
                if res["status"] == COMPLETED:
                    context.update(res["output"])
                    context[f"{res['name']}__output"] = res["output"]
                    completed_order.append(res["name"])
                elif res["status"] == FAILED:
                    any_failed = True
                    if res.get("critical"):
                        self._compensate(graph, completed_order, context, run_id)
                        self._finish(run_id, FAILED)
                        return FAILED

        final = PARTIAL_SUCCESS if any_failed else COMPLETED
        self._finish(run_id, final)
        return final

    def _compensate(self, graph: DependencyGraph, completed: list[str],
                    context: dict, run_id: str) -> None:
        """Saga rollback: run compensation for completed nodes in reverse order."""
        for name in reversed(completed):
            node = graph.nodes.get(name)
            if node and node.compensation:
                try:
                    node.compensation(context)
                    self._emit(run_id, "compensated", name)
                except Exception as exc:  # noqa: BLE001
                    self._emit(run_id, "compensation_failed", f"{name}: {exc}")

    def _finish(self, run_id: str, status: str) -> None:
        self._set_run_status(run_id, status)
        self._emit(run_id, f"finished:{status}")
        try:
            from events import WORKFLOW_COMPLETED, WORKFLOW_FAILED, get_bus
            get_bus().emit(WORKFLOW_FAILED if status == FAILED else WORKFLOW_COMPLETED,
                           {"run_id": run_id, "status": status},
                           idempotency_key=f"{run_id}:{status}")
        except Exception:  # pragma: no cover
            pass
