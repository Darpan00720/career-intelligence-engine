"""Feature flags & runtime configuration (v5.2).

A tenant-aware flag service supporting global defaults, per-tenant overrides,
percentage rollouts (stable per-unit hashing), kill switches, runtime reload,
and audit logging. Backed by the tenant config (core.tenancy) plus an in-process
override layer, so it works in single- and multi-tenant modes with no new infra.

Flag keys follow dotted scopes, e.g.:
    tenant.acme.llm.provider
    tenant.beta.semantic_cache
    tenant.gamma.workflow_engine
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any

from core import tenancy


@dataclass
class FlagSpec:
    key: str
    default: Any = False
    rollout_percentage: int = 0          # 0–100; gradual enablement
    kill_switch: bool = False            # when True, force-off regardless of else
    tenant_overrides: dict[str, Any] = field(default_factory=dict)


class FeatureFlagService:
    def __init__(self):
        self._flags: dict[str, FlagSpec] = {}
        self._config: dict[str, Any] = {}     # runtime key/value config
        self._lock = threading.RLock()

    # ── Registration / runtime reload ───────────────────────────────────────────
    def register(self, key: str, default: Any = False, *, rollout_percentage: int = 0,
                 kill_switch: bool = False) -> FlagSpec:
        with self._lock:
            spec = FlagSpec(key, default, rollout_percentage, kill_switch)
            self._flags[key] = spec
        tenancy.audit("flag.register", "feature_flag", key)
        return spec

    def reload(self, flags: dict[str, dict]) -> int:
        """Atomically replace the flag set from a config payload (hot reload)."""
        with self._lock:
            self._flags = {
                k: FlagSpec(key=k, default=v.get("default", False),
                            rollout_percentage=v.get("rollout_percentage", 0),
                            kill_switch=v.get("kill_switch", False),
                            tenant_overrides=dict(v.get("tenant_overrides", {})))
                for k, v in flags.items()
            }
        tenancy.audit("flag.reload", "feature_flag", f"{len(self._flags)} flags")
        return len(self._flags)

    # ── Overrides / kill switch ──────────────────────────────────────────────────
    def set_tenant_override(self, key: str, tenant_id: str, value: Any) -> None:
        with self._lock:
            self._flags.setdefault(key, FlagSpec(key)).tenant_overrides[tenant_id] = value
        tenancy.audit("flag.override", "feature_flag", f"{key}:{tenant_id}={value}",
                      tenant_id=tenant_id)

    def set_kill_switch(self, key: str, on: bool) -> None:
        with self._lock:
            self._flags.setdefault(key, FlagSpec(key)).kill_switch = on
        tenancy.audit("flag.kill_switch", "feature_flag", f"{key}={on}")

    def set_rollout(self, key: str, percentage: int) -> None:
        with self._lock:
            self._flags.setdefault(key, FlagSpec(key)).rollout_percentage = max(0, min(100, percentage))

    # ── Evaluation ───────────────────────────────────────────────────────────────
    @staticmethod
    def _bucket(key: str, unit: str) -> int:
        digest = hashlib.sha256(f"{key}:{unit}".encode()).hexdigest()[:8]
        return int(digest, 16) % 100

    def is_enabled(self, key: str, tenant_id: str | None = None, unit: str | None = None) -> bool:
        tenant_id = tenant_id or tenancy.current_tenant()
        with self._lock:
            spec = self._flags.get(key)
        if spec is None:
            return False
        if spec.kill_switch:
            return False
        if tenant_id in spec.tenant_overrides:
            return bool(spec.tenant_overrides[tenant_id])
        if spec.rollout_percentage > 0:
            # Stable per-(tenant or unit) bucketing for gradual rollout.
            return self._bucket(key, unit or tenant_id) < spec.rollout_percentage
        return bool(spec.default)

    def value(self, key: str, tenant_id: str | None = None, default: Any = None) -> Any:
        """Resolve a non-boolean config value with tenant override precedence."""
        tenant_id = tenant_id or tenancy.current_tenant()
        with self._lock:
            spec = self._flags.get(key)
            if spec and tenant_id in spec.tenant_overrides:
                return spec.tenant_overrides[tenant_id]
            if spec is not None and spec.default not in (False, None):
                return spec.default
            return self._config.get(key, default)

    # ── Runtime config (key/value) ───────────────────────────────────────────────
    def set_config(self, key: str, value: Any) -> None:
        with self._lock:
            self._config[key] = value

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "flags": {k: {"default": s.default, "rollout": s.rollout_percentage,
                              "kill_switch": s.kill_switch,
                              "overrides": dict(s.tenant_overrides)}
                          for k, s in self._flags.items()},
                "config": dict(self._config),
            }


_service: FeatureFlagService | None = None


def get_flag_service() -> FeatureFlagService:
    global _service
    if _service is None:
        _service = FeatureFlagService()
    return _service


def reset_flag_service() -> None:
    global _service
    _service = None
