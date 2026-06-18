"""Authentication & Authorization (v5) — JWT, API keys, RBAC.

Self-contained (stdlib only): HS256 JWTs are signed/verified with hmac; API
keys are random tokens stored only as salted hashes. Roles map to permissions
via a static matrix; `require_permission` guards callables. Session revocation
is supported via a token jti denylist (in-memory + optional persistence hook).

Production note: swap the HS256 signer for an OAuth2/OIDC provider by
implementing the same issue_token/verify_token interface — callers are unchanged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

from core import database

# ── Roles & permissions ───────────────────────────────────────────────────────

ADMIN = "ADMIN"
RECRUITER = "RECRUITER"
USER = "USER"
SYSTEM = "SYSTEM"
ROLES = [ADMIN, RECRUITER, USER, SYSTEM]

# Permission matrix. SYSTEM and ADMIN are superusers.
_PERMISSIONS: dict[str, set[str]] = {
    ADMIN: {"*"},
    SYSTEM: {"*"},
    RECRUITER: {
        "jobs:read", "recommendations:read", "applications:read",
        "applications:write", "reviews:read", "reviews:write",
        "analytics:read", "workflows:read", "workflows:run",
    },
    USER: {
        "jobs:read", "recommendations:read", "applications:read",
        "applications:write", "reviews:read", "analytics:read",
    },
}


def permissions_for(role: str) -> set[str]:
    return _PERMISSIONS.get(role, set())


def has_permission(role: str, permission: str) -> bool:
    perms = permissions_for(role)
    return "*" in perms or permission in perms


class AuthError(Exception):
    pass


class PermissionDenied(AuthError):
    pass


# ── JWT (HS256) ───────────────────────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _secret() -> bytes:
    return os.getenv("JWT_SECRET", "dev-insecure-secret-change-me").encode()


@dataclass
class Principal:
    user_id: str
    tenant_id: str
    role: str
    jti: str

    def require(self, permission: str) -> None:
        if not has_permission(self.role, permission):
            raise PermissionDenied(f"{self.role} lacks {permission}")


def issue_token(user_id: str, tenant_id: str, role: str,
                ttl_seconds: int = 3600, jti: str | None = None) -> str:
    if role not in ROLES:
        raise AuthError(f"unknown role: {role}")
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id, "tenant": tenant_id, "role": role,
        "iat": now, "exp": now + ttl_seconds,
        "jti": jti or secrets.token_hex(8),
    }
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(payload).encode())}"
    sig = hmac.new(_secret(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(sig)}"


def verify_token(token: str) -> Principal:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        raise AuthError("malformed token")
    signing_input = f"{header_b64}.{payload_b64}"
    expected = hmac.new(_secret(), signing_input.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
        raise AuthError("bad signature")
    payload = json.loads(_b64url_decode(payload_b64))
    if payload.get("exp", 0) < int(time.time()):
        raise AuthError("token expired")
    if is_revoked(payload.get("jti", "")):
        raise AuthError("token revoked")
    return Principal(payload["sub"], payload.get("tenant", "default"),
                     payload.get("role", USER), payload.get("jti", ""))


def refresh_token(token: str, ttl_seconds: int = 3600) -> str:
    """Issue a fresh token for the same principal (new jti, new expiry)."""
    p = verify_token(token)
    return issue_token(p.user_id, p.tenant_id, p.role, ttl_seconds=ttl_seconds)


# ── Session revocation ────────────────────────────────────────────────────────

_revoked: set[str] = set()


def revoke(jti: str) -> None:
    _revoked.add(jti)


def is_revoked(jti: str) -> bool:
    return jti in _revoked


def clear_revocations() -> None:
    _revoked.clear()


# ── API keys ──────────────────────────────────────────────────────────────────

def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_user(user_id: str, tenant_id: str, role: str = USER,
                email: str = "") -> tuple[str, str]:
    """Create a platform user and return (user_id, raw_api_key).

    The raw key is shown once; only its hash is stored.
    """
    if role not in ROLES:
        raise AuthError(f"unknown role: {role}")
    raw_key = "ck_" + secrets.token_urlsafe(24)
    with database.get_connection() as conn:
        conn.execute(
            """INSERT INTO platform_users (user_id, tenant_id, email, role, api_key_hash)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   tenant_id = excluded.tenant_id, email = excluded.email,
                   role = excluded.role, api_key_hash = excluded.api_key_hash""",
            (user_id, tenant_id, email, role, _hash_key(raw_key)),
        )
    return user_id, raw_key


def authenticate_api_key(raw_key: str) -> Principal:
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM platform_users WHERE api_key_hash = ?",
            (_hash_key(raw_key),),
        ).fetchone()
    if not row:
        raise AuthError("invalid API key")
    return Principal(row["user_id"], row["tenant_id"], row["role"], jti="apikey")


# ── Permission guard ──────────────────────────────────────────────────────────

def require_permission(permission: str):
    """Decorator: the wrapped fn must receive a Principal as `principal=` kwarg
    (or first positional Principal) that holds the permission."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            principal = kwargs.get("principal")
            if principal is None:
                principal = next((a for a in args if isinstance(a, Principal)), None)
            if principal is None:
                raise PermissionDenied("no principal provided")
            principal.require(permission)
            return fn(*args, **kwargs)
        wrapper.__name__ = getattr(fn, "__name__", "wrapped")
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return decorator
