"""Scheduler (v4) — cron/daily/weekly job scheduling with lifecycle control.

Deterministic and testable: instead of running a live daemon thread, the
scheduler computes which jobs are due for a given `now` and runs them via
`tick(now)`. A thin `run_forever()` loop is provided for production use.

Features: daily / weekly / custom 5-field cron; pause, resume, disable; per-job
execution history; and a retry policy. No external dependencies.

    sched = Scheduler()
    sched.add("nightly", "0 8 * * *", run_pipeline)   # 08:00 every day
    sched.tick(datetime(2026, 6, 18, 8, 0))            # runs nightly
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from core.event_log import log_event


# ── Cron matching (5 fields: minute hour day-of-month month day-of-week) ────────

def _match_field(spec: str, value: int, lo: int, hi: int) -> bool:
    spec = spec.strip()
    if spec == "*":
        return True
    for part in spec.split(","):
        if part.startswith("*/"):
            step = int(part[2:])
            if step and (value - lo) % step == 0:
                return True
        elif "-" in part:
            a, b = part.split("-")
            if int(a) <= value <= int(b):
                return True
        elif part.isdigit() and int(part) == value:
            return True
    return False


def cron_matches(expr: str, when: datetime) -> bool:
    """Return True if a 5-field cron expression matches the given datetime
    (to minute precision). day-of-week: 0=Sunday..6=Saturday."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron expression must have 5 fields: {expr!r}")
    minute, hour, dom, month, dow = fields
    return (
        _match_field(minute, when.minute, 0, 59)
        and _match_field(hour, when.hour, 0, 23)
        and _match_field(dom, when.day, 1, 31)
        and _match_field(month, when.month, 1, 12)
        and _match_field(dow, (when.weekday() + 1) % 7, 0, 6)  # Python Mon=0 → cron Sun=0
    )


def daily(hour: int, minute: int = 0) -> str:
    return f"{minute} {hour} * * *"


def weekly(dow: int, hour: int, minute: int = 0) -> str:
    """dow: 0=Sunday..6=Saturday."""
    return f"{minute} {hour} * * {dow}"


# ── Scheduled jobs ──────────────────────────────────────────────────────────────

@dataclass
class ScheduledJob:
    name: str
    cron: str
    func: Callable
    enabled: bool = True
    paused: bool = False
    max_retries: int = 1
    last_run: str | None = None
    last_status: str | None = None
    history: list[dict] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.enabled and not self.paused

    def is_due(self, when: datetime) -> bool:
        # Guard against double-firing within the same minute.
        minute_key = when.strftime("%Y-%m-%d %H:%M")
        return self.active and cron_matches(self.cron, when) and self.last_run != minute_key


class Scheduler:
    def __init__(self):
        self.jobs: dict[str, ScheduledJob] = {}

    # ── Registration / lifecycle ──────────────────────────────────────────────
    def add(self, name: str, cron: str, func: Callable, *,
            max_retries: int = 1) -> ScheduledJob:
        cron_matches(cron, datetime.now())  # validate expression eagerly
        job = ScheduledJob(name=name, cron=cron, func=func, max_retries=max_retries)
        self.jobs[name] = job
        return job

    def pause(self, name: str) -> None:
        self.jobs[name].paused = True

    def resume(self, name: str) -> None:
        self.jobs[name].paused = False

    def disable(self, name: str) -> None:
        self.jobs[name].enabled = False

    def enable(self, name: str) -> None:
        self.jobs[name].enabled = True

    def remove(self, name: str) -> None:
        self.jobs.pop(name, None)

    def history(self, name: str) -> list[dict]:
        return self.jobs[name].history if name in self.jobs else []

    # ── Execution ──────────────────────────────────────────────────────────────
    def due_jobs(self, when: datetime) -> list[ScheduledJob]:
        return [j for j in self.jobs.values() if j.is_due(when)]

    def _run_job(self, job: ScheduledJob, when: datetime) -> dict:
        minute_key = when.strftime("%Y-%m-%d %H:%M")
        attempt, status, error, result = 0, "ok", None, None
        while True:
            try:
                result = job.func()
                status, error = "ok", None
                break
            except Exception as exc:  # noqa: BLE001
                status, error = "error", str(exc)
                if attempt >= job.max_retries:
                    break
                attempt += 1
        record = {"run_at": minute_key, "status": status, "attempts": attempt + 1,
                  "error": error}
        job.last_run, job.last_status = minute_key, status
        job.history.append(record)
        log_event("pipeline", "scheduled_job", status=status, error=error)
        return record

    def tick(self, when: datetime | None = None) -> list[dict]:
        """Run all jobs due at `when` (default: now). Returns their run records."""
        when = when or datetime.now()
        return [self._run_job(job, when) for job in self.due_jobs(when)]

    def run_forever(self, poll_seconds: int = 30) -> None:  # pragma: no cover - daemon
        """Production loop: tick once per minute boundary."""
        while True:
            self.tick(datetime.now())
            time.sleep(poll_seconds)
