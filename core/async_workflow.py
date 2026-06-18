"""Async DAG workflow engine + worker runtime (v5.2).

AsyncWorkflowEngine executes a DependencyGraph level-by-level, running each
level's nodes concurrently via ``asyncio.TaskGroup`` with per-node timeouts
(``asyncio.wait_for``), retries, graceful cancellation, and saga compensation.
Node functions may be sync or async (sync ones run via ``to_thread`` so the loop
never blocks).

AsyncWorkerRuntime is an asyncio task pool with a bounded queue (backpressure),
retry handling, and graceful shutdown — the async analogue of the local worker
pool, ready to back a distributed broker.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

from core.workflow_dag import DependencyGraph, ExecutionPlanner, WorkflowNode
from core.workflow_engine import CANCELLED, COMPLETED, FAILED, PARTIAL_SUCCESS

SKIPPED = "SKIPPED"


async def _maybe_async(func: Callable, arg: Any) -> Any:
    """Await async funcs; run sync funcs off-loop via to_thread."""
    if inspect.iscoroutinefunction(func):
        return await func(arg)
    return await asyncio.to_thread(func, arg)


class AsyncWorkflowEngine:
    def __init__(self):
        self._cancel = asyncio.Event()

    def cancel(self) -> None:
        self._cancel.set()

    async def _run_node(self, node: WorkflowNode, ctx: dict) -> dict:
        if node.condition is not None and not node.condition(ctx):
            return {"name": node.name, "status": SKIPPED, "output": {}}
        attempts, last_error = 0, None
        while attempts <= node.retries:
            attempts += 1
            try:
                coro = _maybe_async(node.func, ctx)
                output = (await asyncio.wait_for(coro, node.timeout)
                          if node.timeout else await coro) or {}
                return {"name": node.name, "status": COMPLETED,
                        "output": output if isinstance(output, dict) else {}}
            except asyncio.TimeoutError:
                last_error = f"timeout after {node.timeout}s"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
        return {"name": node.name, "status": FAILED, "error": last_error,
                "critical": node.critical}

    async def run(self, graph: DependencyGraph, context: dict | None = None,
                  mode: str = "parallel") -> str:
        context = dict(context or {})
        completed: list[str] = []
        any_failed = False

        for level in ExecutionPlanner.plan(graph):
            if self._cancel.is_set():
                return CANCELLED
            nodes = [graph.nodes[n] for n in level]
            snapshot = dict(context)   # immutable per-level view

            if mode == "parallel" and len(nodes) > 1:
                results: list[dict] = []
                # TaskGroup: node wrappers never raise (errors captured), so a
                # non-critical failure doesn't cancel its siblings.
                async with asyncio.TaskGroup() as tg:
                    tasks = [tg.create_task(self._run_node(n, snapshot)) for n in nodes]
                results = [t.result() for t in tasks]
            else:
                results = [await self._run_node(n, snapshot) for n in nodes]

            for res in sorted(results, key=lambda r: r["name"]):
                if res["status"] == COMPLETED:
                    context.update(res["output"])
                    completed.append(res["name"])
                elif res["status"] == FAILED:
                    any_failed = True
                    if res.get("critical"):
                        await self._compensate(graph, completed, context)
                        return FAILED
        return PARTIAL_SUCCESS if any_failed else COMPLETED

    async def _compensate(self, graph: DependencyGraph, completed: list[str],
                          context: dict) -> None:
        for name in reversed(completed):
            node = graph.nodes.get(name)
            if node and node.compensation:
                try:
                    await _maybe_async(node.compensation, context)
                except Exception:  # noqa: BLE001 - best-effort rollback
                    pass


# ── Async worker runtime ────────────────────────────────────────────────────────

@dataclass
class AsyncWorkerRuntime:
    """Bounded asyncio task pool with backpressure, retries, graceful shutdown."""
    concurrency: int = 4
    maxsize: int = 0
    max_retries: int = 2
    _queue: asyncio.Queue = field(default=None, repr=False)
    completed: int = 0
    failed: int = 0

    def __post_init__(self):
        self._queue = asyncio.Queue(maxsize=self.maxsize)

    async def submit(self, coro_factory: Callable[[], Any], *, block: bool = True) -> bool:
        """Enqueue a 0-arg (async or sync) callable. Returns False if full and not blocking."""
        if block:
            await self._queue.put((coro_factory, 0))
            return True
        try:
            self._queue.put_nowait((coro_factory, 0))
            return True
        except asyncio.QueueFull:
            return False

    async def _worker(self) -> None:
        while True:
            try:
                factory, attempts = await self._queue.get()
            except asyncio.CancelledError:
                return
            try:
                result = factory()
                if inspect.isawaitable(result):
                    await result
                self.completed += 1
            except Exception:  # noqa: BLE001
                if attempts < self.max_retries:
                    await self._queue.put((factory, attempts + 1))
                else:
                    self.failed += 1
            finally:
                self._queue.task_done()

    async def run_until_empty(self) -> dict:
        """Process all queued work across `concurrency` workers, then stop."""
        workers = [asyncio.create_task(self._worker()) for _ in range(self.concurrency)]
        await self._queue.join()
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        return {"completed": self.completed, "failed": self.failed}

    @property
    def depth(self) -> int:
        return self._queue.qsize()
