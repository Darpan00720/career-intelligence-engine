"""Distributed-execution workers (v5).

Local-first task execution behind an interface that a distributed broker
(Celery / RQ / Redis Streams) can implement later. Provides a bounded task queue
(backpressure), a retry queue, rate limiting, and a worker pool. A shared queue
across workers gives natural work-stealing.
"""
from workers.queue import Task, TaskQueue
from workers.pool import LocalWorkerPool

__all__ = ["Task", "TaskQueue", "LocalWorkerPool"]
