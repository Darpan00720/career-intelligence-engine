"""Multi-tenancy (v5) — tenants, workspaces, feature flags, quotas, audit.

Backward compatible: everything defaults to a single 'default' tenant, so the
existing single-node behavior is unchanged. Multi-tenant SaaS mode is enabled
per process via TENANCY_MODE=multi. Tenant scoping for a unit of work is carried
on a contextvar (no shared mutable state) and read by tenant-aware subsystems
(events, workflows, LLM costs, audit, usage).
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from core import database

DEFAULT_TENANT = "default"
SINGLE = "single"
MULTI = "multi"

_current_tenant: ContextVar[str] = ContextVar("current_tenant", default=DEFAULT_TENANT)


def mode() -> str:
    return MULTI if os.getenv("TENANCY_MODE", SINGLE).lower() == MULTI else SINGLE


def current_tenant() -> str:
    return _current_tenant.get()


@contextmanager
def use_tenant(tenant_id: str):
    """Scope a block of work to a tenant (restores the previous tenant on exit)."""
    token = _current_tenant.set(tenant_id or DEFAULT_TENANT)
    try:
        yield tenant_id
    finally:
        _current_tenant.reset(token)


@dataclass
class Tenant:
    tenant_id: str
    name: str
    mode: str
    config: dict

    def feature(self, flag: str, default: bool = False) -> bool:
        return bool(self.config.get("features", {}).get(flag, default))

    def quota(self, metric: str) -> float | None:
        return self.config.get("quotas", {}).get(metric)


# ── Tenant CRUD ───────────────────────────────────────────────────────────────

def create_tenant(tenant_id: str, name: str = "", tenant_mode: str = SINGLE,
                  config: dict | None = None) -> Tenant:
    with database.get_connection() as conn:
        conn.execute(
            """INSERT INTO tenants (tenant_id, name, mode, config)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(tenant_id) DO UPDATE SET
                   name = excluded.name, mode = excluded.mode, config = excluded.config""",
            (tenant_id, name, tenant_mode, json.dumps(config or {})),
        )
    audit("tenant.create", "tenant", tenant_id, tenant_id=tenant_id)
    return Tenant(tenant_id, name, tenant_mode, config or {})


def get_tenant(tenant_id: str | None = None) -> Tenant | None:
    tenant_id = tenant_id or current_tenant()
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
    if not row:
        # The implicit default tenant always exists logically.
        if tenant_id == DEFAULT_TENANT:
            return Tenant(DEFAULT_TENANT, "Default", SINGLE, {})
        return None
    cfg = json.loads(row["config"]) if row["config"] else {}
    return Tenant(row["tenant_id"], row["name"] or "", row["mode"] or SINGLE, cfg)


def list_tenants() -> list[dict]:
    with database.get_connection() as conn:
        rows = conn.execute("SELECT * FROM tenants ORDER BY tenant_id").fetchall()
    return [dict(r) for r in rows]


def create_workspace(workspace_id: str, name: str = "", tenant_id: str | None = None) -> dict:
    tenant_id = tenant_id or current_tenant()
    with database.get_connection() as conn:
        conn.execute(
            """INSERT INTO workspaces (workspace_id, tenant_id, name) VALUES (?, ?, ?)
               ON CONFLICT(workspace_id) DO UPDATE SET name = excluded.name""",
            (workspace_id, tenant_id, name),
        )
    return {"workspace_id": workspace_id, "tenant_id": tenant_id, "name": name}


# ── Feature flags ─────────────────────────────────────────────────────────────

def feature_enabled(flag: str, tenant_id: str | None = None, default: bool = False) -> bool:
    tenant = get_tenant(tenant_id)
    return tenant.feature(flag, default) if tenant else default


# ── Usage + quotas ────────────────────────────────────────────────────────────

def record_usage(metric: str, amount: float = 1.0, tenant_id: str | None = None) -> None:
    tenant_id = tenant_id or current_tenant()
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO usage_records (tenant_id, metric, amount) VALUES (?, ?, ?)",
            (tenant_id, metric, amount),
        )


def get_usage(metric: str | None = None, tenant_id: str | None = None) -> dict[str, float]:
    tenant_id = tenant_id or current_tenant()
    with database.get_connection() as conn:
        if metric:
            rows = conn.execute(
                "SELECT metric, SUM(amount) AS total FROM usage_records "
                "WHERE tenant_id = ? AND metric = ? GROUP BY metric",
                (tenant_id, metric),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT metric, SUM(amount) AS total FROM usage_records "
                "WHERE tenant_id = ? GROUP BY metric", (tenant_id,)
            ).fetchall()
    return {r["metric"]: r["total"] for r in rows}


def check_quota(metric: str, tenant_id: str | None = None) -> dict:
    """Return {allowed, used, limit}. allowed=True when under the configured limit
    (or when no quota is configured)."""
    tenant_id = tenant_id or current_tenant()
    tenant = get_tenant(tenant_id)
    limit = tenant.quota(metric) if tenant else None
    used = get_usage(metric, tenant_id).get(metric, 0.0)
    allowed = limit is None or used < limit
    return {"allowed": allowed, "used": used, "limit": limit}


def enforce_quota(metric: str, tenant_id: str | None = None) -> None:
    """Raise QuotaExceeded if the tenant is at/over its quota for `metric`."""
    status = check_quota(metric, tenant_id)
    if not status["allowed"]:
        raise QuotaExceeded(
            f"quota exceeded for {metric}: {status['used']}/{status['limit']}")


class QuotaExceeded(Exception):
    pass


# ── Audit log ─────────────────────────────────────────────────────────────────

def audit(action: str, entity: str = "", detail: str = "",
          actor: str | None = None, tenant_id: str | None = None) -> None:
    tenant_id = tenant_id or current_tenant()
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO audit_log (tenant_id, actor, action, entity, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (tenant_id, actor or "system", action, entity, detail),
        )


def audit_trail(tenant_id: str | None = None, limit: int = 100) -> list[dict]:
    tenant_id = tenant_id or current_tenant()
    with database.get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE tenant_id = ? ORDER BY id DESC LIMIT ?",
            (tenant_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
