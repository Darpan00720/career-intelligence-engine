"""Local worker pool (v5) — multi-threaded task execution with retries.

A shared TaskQueue across worker threads provides work-stealing. Failed tasks
are retried up to their max_retries (re-queued); permanently failed tasks land
in a dead-letter list. Optional rate limiting throttles task starts.

This is the local implementation of the worker port; a Celery/RQ-backed pool can
implement the same submit()/run() surface for horizontal scaling.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from core.concurrency import RateLimiter
from core.metrics import set_queue_depth
from workers.queue import Task, TaskQueue


@dataclass
class PoolReport:
    completed: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    results: dict[str, object] = field(default_factory=dict)


class LocalWorkerPool:
    def __init__(self, num_workers: int = 4, maxsize: int = 0, rate_limit: float = 0.0):
        self.num_workers = num_workers
        self.queue = TaskQueue(maxsize=maxsize)
        self.retry_queue = TaskQueue()
        self.limiter = RateLimiter(rate_limit)
        self._report = PoolReport()
        self._lock = threading.Lock()
        # Coordinates termination: a worker only exits when there is no queued
        # work AND no peer is mid-task (so no in-flight task can still re-queue a
        # retry). `_active` counts workers currently executing a task.
        self._cv = threading.Condition()
        self._active = 0

    def submit(self, func, max_retries: int = 2) -> bool:
        """Enqueue work. Returns False when the queue is full (backpressure)."""
        ok = self.queue.put(Task(func=func, max_retries=max_retries))
        set_queue_depth("default", self.queue.depth)
        return ok

    def _next_task(self) -> Task | None:
        # Prefer the main queue; fall back to the retry queue (work-stealing
        # across threads happens naturally on the shared queues).
        return self.queue.get() or self.retry_queue.get()

    def _worker_loop(self) -> None:
        while True:
            with self._cv:
                task = self._next_task()
                while task is None:
                    # No work right now. If nobody is mid-task either, all work is
                    # truly done — wake peers and exit. Otherwise wait: an active
                    # peer may still re-queue a retry.
                    if self._active == 0:
                        self._cv.notify_all()
                        return
                    self._cv.wait()
                    task = self._next_task()
                self._active += 1
            try:
                self.limiter.wait()
                task.attempts += 1
                try:
                    result = task.func()
                    with self._lock:
                        self._report.completed.append(task.task_id)
                        self._report.results[task.task_id] = result
                except Exception as exc:  # noqa: BLE001
                    if task.attempts <= task.max_retries:
                        self.retry_queue.put(task)
                    else:
                        with self._lock:
                            self._report.failed.append(
                                {"task_id": task.task_id, "error": str(exc)})
            finally:
                with self._cv:
                    self._active -= 1
                    self._cv.notify_all()  # a retry may now be available, or we're done

    def run(self) -> PoolReport:
        """Process all queued tasks (incl. retries) across the worker pool."""
        threads = [threading.Thread(target=self._worker_loop, daemon=True)
                   for _ in range(self.num_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        set_queue_depth("default", self.queue.depth)
        return self._report
