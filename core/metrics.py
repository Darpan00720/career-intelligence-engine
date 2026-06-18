"""Metrics registry (v5) — Prometheus-style counters / gauges / histograms.

In-process, thread-safe, and dependency-free. `render_prometheus()` produces a
text exposition that a Prometheus exporter (or the /metrics endpoint) can serve.
An OpenTelemetry/Prometheus client can replace this registry behind the same
record_* helpers without touching call sites.

Standard metric names: agent_latency, workflow_duration, queue_depth,
notification_failures, cache_hit_ratio, llm_cost, api_latency, tenant_usage.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


def _key(name: str, labels: dict | None) -> str:
    if not labels:
        return name
    label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{label_str}}}"


@dataclass
class Histogram:
    count: int = 0
    total: float = 0.0
    buckets: list[float] = field(default_factory=list)

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.buckets.append(value)

    @property
    def avg(self) -> float:
        return round(self.total / self.count, 4) if self.count else 0.0


class MetricsRegistry:
    def __init__(self):
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def inc(self, name: str, value: float = 1.0, labels: dict | None = None) -> None:
        with self._lock:
            k = _key(name, labels)
            self._counters[k] = self._counters.get(k, 0.0) + value

    def set_gauge(self, name: str, value: float, labels: dict | None = None) -> None:
        with self._lock:
            self._gauges[_key(name, labels)] = value

    def observe(self, name: str, value: float, labels: dict | None = None) -> None:
        with self._lock:
            self._histograms.setdefault(_key(name, labels), Histogram()).observe(value)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {k: {"count": h.count, "avg": h.avg, "sum": round(h.total, 4)}
                               for k, h in self._histograms.items()},
            }

    def render_prometheus(self) -> str:
        lines: list[str] = []
        snap = self.snapshot()
        for k, v in snap["counters"].items():
            lines.append(f"{k} {v}")
        for k, v in snap["gauges"].items():
            lines.append(f"{k} {v}")
        for k, h in snap["histograms"].items():
            base = k.split("{")[0]
            lines.append(f"{base}_count {h['count']}")
            lines.append(f"{base}_sum {h['sum']}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


_registry = MetricsRegistry()


def registry() -> MetricsRegistry:
    return _registry


# ── Convenience recorders for the standard metric names ─────────────────────────

def record_agent_latency(agent: str, seconds: float) -> None:
    _registry.observe("agent_latency", seconds, {"agent": agent})


def record_workflow_duration(seconds: float) -> None:
    _registry.observe("workflow_duration", seconds)


def set_queue_depth(queue: str, depth: int) -> None:
    _registry.set_gauge("queue_depth", depth, {"queue": queue})


def record_notification_failure() -> None:
    _registry.inc("notification_failures")


def set_cache_hit_ratio(ratio: float) -> None:
    _registry.set_gauge("cache_hit_ratio", ratio)


def record_llm_cost(cost: float, tenant: str) -> None:
    _registry.inc("llm_cost", cost, {"tenant": tenant})


def record_api_latency(path: str, seconds: float) -> None:
    _registry.observe("api_latency", seconds, {"path": path})


def record_tenant_usage(tenant: str, amount: float = 1.0) -> None:
    _registry.inc("tenant_usage", amount, {"tenant": tenant})


# ── Database metrics (v5.2.1) ───────────────────────────────────────────────────

def set_db_pool(active: int, idle: int, pool: str = "primary") -> None:
    _registry.set_gauge("db_pool_active", active, {"pool": pool})
    _registry.set_gauge("db_pool_idle", idle, {"pool": pool})


def observe_db_query_latency(seconds: float, op: str = "query") -> None:
    _registry.observe("db_query_latency", seconds, {"op": op})


def record_slow_query(op: str = "query") -> None:
    _registry.inc("db_slow_queries", labels={"op": op})


def set_db_replication_lag(seconds: float) -> None:
    _registry.set_gauge("db_replication_lag", seconds)


def set_db_partition_size(table: str, size_bytes: float) -> None:
    _registry.set_gauge("db_partition_size", size_bytes, {"table": table})
