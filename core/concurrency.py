"""Concurrency primitives (v4 performance layer).

Pure, dependency-free helpers used by agents and the orchestrator:

  parallel_map  — bounded ThreadPool map with optional rate limiting; preserves
                  input order and never raises (errors are returned per item).
  backoff_retry — retry a callable with exponential backoff.
  RateLimiter   — simple min-interval throttle, thread-safe.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Iterable


class RateLimiter:
    """Allow at most one call per `min_interval` seconds (thread-safe)."""

    def __init__(self, min_interval: float = 0.0):
        self.min_interval = max(0.0, min_interval)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.perf_counter()
            delta = now - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.perf_counter()


@dataclass
class TaskResult:
    index: int
    ok: bool
    value: Any = None
    error: str | None = None


def parallel_map(fn: Callable[[Any], Any], items: Iterable[Any], *,
                 max_workers: int = 4, rate_limit: float = 0.0) -> list[TaskResult]:
    """Map `fn` over `items` across up to `max_workers` threads.

    Returns TaskResult per item in input order. Per-item exceptions are captured
    (never raised) so one failure doesn't abort the batch. max_workers <= 1 runs
    sequentially. `rate_limit` is the min seconds between task starts.
    """
    items = list(items)
    limiter = RateLimiter(rate_limit)
    results: list[TaskResult] = [TaskResult(i, False) for i in range(len(items))]

    def _wrapped(index: int, item: Any) -> TaskResult:
        limiter.wait()
        try:
            return TaskResult(index, True, fn(item))
        except Exception as exc:  # noqa: BLE001 - captured per item
            return TaskResult(index, False, error=str(exc))

    if max_workers <= 1 or len(items) <= 1:
        for i, item in enumerate(items):
            results[i] = _wrapped(i, item)
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for res in pool.map(lambda pair: _wrapped(*pair), list(enumerate(items))):
            results[res.index] = res
    return results


def backoff_retry(fn: Callable[[], Any], *, retries: int = 2,
                  base_delay: float = 0.2, factor: float = 2.0,
                  exceptions: tuple = (Exception,)) -> Any:
    """Call `fn`, retrying on the given exceptions with exponential backoff.

    Re-raises the last exception if all attempts fail.
    """
    delay = base_delay
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except exceptions as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                time.sleep(delay)
                delay *= factor
    raise last  # type: ignore[misc]
