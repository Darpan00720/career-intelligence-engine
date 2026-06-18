"""BaseAgent (v4) — a standard, independently-testable agent interface.

Lifecycle: initialize() → validate() → run() → cleanup(), driven by execute(),
which times the run, captures failures, and emits an event-log line. Agents
receive a shared read-only `context` and a `deps` mapping for dependency
injection (tests inject fakes; production binds real functions lazily).

Subclasses override run() (required) and any hooks they need.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from core.event_log import log_event

# Result statuses
OK = "ok"
ERROR = "error"
SKIPPED = "skipped"


@dataclass
class AgentResult:
    name: str
    status: str
    data: dict = field(default_factory=dict)
    duration: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == OK


class BaseAgent(ABC):
    """Abstract agent with lifecycle hooks and dependency injection."""

    name: str = "base"

    def __init__(self, context: dict | None = None, deps: dict | None = None):
        # context is shared input (treated read-only); deps holds injected callables.
        self.context: dict = dict(context or {})
        self.deps: dict = dict(deps or {})
        self.initialized: bool = False

    # ── Dependency injection helper ────────────────────────────────────────────
    def dep(self, key: str, default: Callable | None = None) -> Callable:
        """Return an injected dependency by key, else the provided default."""
        fn = self.deps.get(key, default)
        if fn is None:
            raise KeyError(f"{self.name}: missing dependency {key!r}")
        return fn

    # ── Lifecycle hooks (override as needed) ────────────────────────────────────
    def initialize(self) -> None:
        """Set up resources before run(). Default: no-op."""

    def validate(self) -> bool:
        """Return False to skip run() (e.g., preconditions unmet). Default: True."""
        return True

    @abstractmethod
    def run(self) -> dict:
        """Perform the agent's work and return a result dict."""

    def cleanup(self) -> None:
        """Release resources after run(). Always called. Default: no-op."""

    # ── Template method ─────────────────────────────────────────────────────────
    def execute(self) -> AgentResult:
        """Run the full lifecycle, never raising; returns an AgentResult."""
        start = time.perf_counter()
        result: AgentResult
        try:
            self.initialize()
            self.initialized = True
            if not self.validate():
                result = AgentResult(self.name, SKIPPED,
                                     duration=time.perf_counter() - start,
                                     error="validation failed")
            else:
                data = self.run() or {}
                result = AgentResult(self.name, OK, data=data,
                                     duration=time.perf_counter() - start)
        except Exception as exc:  # noqa: BLE001 - agents must not crash the orchestrator
            result = AgentResult(self.name, ERROR,
                                 duration=time.perf_counter() - start, error=str(exc))
        finally:
            try:
                self.cleanup()
            except Exception:  # pragma: no cover - cleanup best-effort
                pass

        log_event("pipeline", f"agent_{self.name}", duration=result.duration,
                  status=result.status, error=result.error,
                  level="ERROR" if result.status == ERROR else "INFO")
        return result
