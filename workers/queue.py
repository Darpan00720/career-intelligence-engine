"""Bounded task queue with backpressure (v5)."""
from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Task:
    func: Callable[[], Any]
    max_retries: int = 2
    attempts: int = 0
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)


class QueueFull(Exception):
    pass


class TaskQueue:
    """Thread-safe FIFO queue with a max size (backpressure)."""

    def __init__(self, maxsize: int = 0):
        self.maxsize = maxsize
        self._dq: deque[Task] = deque()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._dq)

    @property
    def depth(self) -> int:
        return len(self)

    def put(self, task: Task, block: bool = False) -> bool:
        """Enqueue a task. Returns False (or raises if block=False and full).

        With maxsize>0 and the queue full, returns False to signal backpressure.
        """
        with self._lock:
            if self.maxsize and len(self._dq) >= self.maxsize:
                if not block:
                    return False
                raise QueueFull("task queue is full")
            self._dq.append(task)
            return True

    def get(self) -> Task | None:
        with self._lock:
            return self._dq.popleft() if self._dq else None
