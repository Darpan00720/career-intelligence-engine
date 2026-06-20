"""API authentication + tenant identity (security layer).

A single dependency, :func:`authenticate`, is the front door for the CRM (v1) and
Gateway (v2) routes. It enforces API-key auth **when keys are configured** and
binds each request to a tenant so tenant isolation can no longer be spoofed by a
client-supplied header or query param.

Configuration via the ``API_KEYS`` environment variable (comma-separated):

    API_KEYS="key_abc:acme,key_def:globex"   # each key maps to a tenant
    API_KEYS="key_abc,key_def"                # keys with no tenant -> 'default'

Behaviour:

* **Keys configured** — requests must send a matching ``X-API-Key`` header or get
  ``401``. The tenant is taken from the key's mapping; the ``X-Tenant-ID`` header
  and ``?tenant=`` query param are ignored (this closes the cross-tenant IDOR).
* **No keys configured** — open/development mode: requests are allowed and the
  tenant falls back to the ``X-Tenant-ID`` header (or ``default``). Production
  deployments MUST set ``API_KEYS`` (a startup warning fires otherwise).

The dependency also sets the tenancy contextvar so downstream subsystems
(events, costs, usage, audit) are scoped to the authenticated tenant.
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException


@dataclass(frozen=True)
class Identity:
    tenant: str
    authenticated: bool
    api_key: str | None = None


def _configured_keys() -> dict[str, str]:
    """Parse ``API_KEYS`` into a {key: tenant} mapping. Read live so the value can
    be set per test/process; the env string is tiny so parsing cost is negligible."""
    raw = os.getenv("API_KEYS", "").strip()
    if not raw:
        return {}
    from core.tenancy import DEFAULT_TENANT

    mapping: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        key, _, tenant = part.partition(":")
        key = key.strip()
        if key:
            mapping[key] = tenant.strip() or DEFAULT_TENANT
    return mapping


def auth_enabled() -> bool:
    return bool(_configured_keys())


def _match_key(presented: str | None, keys: dict[str, str]) -> str | None:
    """Constant-time lookup of a presented key. Returns the mapped tenant or None."""
    if not presented:
        return None
    for key, tenant in keys.items():
        if secrets.compare_digest(presented, key):
            return tenant
    return None


def authenticate(
    x_api_key: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> Identity:
    """FastAPI dependency: enforce auth (when configured) and resolve the tenant.

    Raises 401 when keys are configured and the request lacks a valid key.
    Always sets the tenancy contextvar for the duration of the request.
    """
    from core.tenancy import DEFAULT_TENANT, _current_tenant

    keys = _configured_keys()
    if not keys:
        # Open/dev mode: trust the tenant header (no cross-tenant guarantee here,
        # which is why production must configure API_KEYS).
        tenant = x_tenant_id or DEFAULT_TENANT
        _current_tenant.set(tenant)
        return Identity(tenant=tenant, authenticated=False)

    tenant = _match_key(x_api_key, keys)
    if tenant is None:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    _current_tenant.set(tenant)  # tenant is bound to the key, not client input
    return Identity(tenant=tenant, authenticated=True, api_key=x_api_key)


# ── Fixed-window rate limiter (shared by the gateway routes) ────────────────────

_RL_WINDOW = 60.0
_RL_MAX = int(os.getenv("API_RATE_LIMIT", "600"))
_rl_state: dict[str, tuple[float, int]] = {}


def rate_limit(key: str) -> None:
    """Fixed-window limiter. Raises 429 over the per-window budget.

    Stale windows are pruned on each call so ``_rl_state`` stays bounded by the
    number of *active* clients rather than growing unbounded over time.
    """
    now = time.time()
    # Prune expired windows (bounds memory for churny key spaces).
    if len(_rl_state) > 1024:
        for k, (start, _) in list(_rl_state.items()):
            if now - start >= _RL_WINDOW:
                _rl_state.pop(k, None)
    start, count = _rl_state.get(key, (now, 0))
    if now - start >= _RL_WINDOW:
        start, count = now, 0
    count += 1
    _rl_state[key] = (start, count)
    if count > _RL_MAX:
        raise HTTPException(status_code=429, detail="rate limit exceeded")


def tenant_ctx(identity: Identity = Depends(authenticate)) -> str:
    """Dependency returning the request tenant, after auth + rate limiting.

    Used by tenant-scoped v2 routes that need the tenant value (not just
    enforcement).
    """
    rate_limit(identity.tenant)
    return identity.tenant
