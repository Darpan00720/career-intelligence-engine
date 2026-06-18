"""Enterprise security (v5) — PII masking, encryption-at-rest port, audit,
data-retention, and GDPR deletion.

PII masking and audit are production-ready. The Encryptor is a *port*: the
default KeyedEncryptor provides reversible obfuscation suitable for local/dev
use, while production should inject a KMS/Fernet-backed implementation with the
same interface (encrypt/decrypt). Retention + GDPR deletion operate over the
tenant-scoped v5 tables.
"""
from __future__ import annotations

import base64
import os
import re

from core import database

# ── PII masking ───────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{6,}\d)")


def mask_email(email: str) -> str:
    return _EMAIL_RE.sub(lambda m: f"{m.group(1)}***{m.group(2)}", email or "")


def mask_phone(text: str) -> str:
    def _m(match):
        digits = re.sub(r"\D", "", match.group(1))
        return "***" + digits[-2:] if len(digits) >= 2 else "***"
    return _PHONE_RE.sub(_m, text or "")


def mask_pii(text: str) -> str:
    """Mask emails and phone numbers in free text."""
    return mask_phone(mask_email(text or ""))


# ── Encryption-at-rest (port) ─────────────────────────────────────────────────

class Encryptor:
    def encrypt(self, plaintext: str) -> str: raise NotImplementedError
    def decrypt(self, ciphertext: str) -> str: raise NotImplementedError


class NullEncryptor(Encryptor):
    def encrypt(self, plaintext: str) -> str: return plaintext
    def decrypt(self, ciphertext: str) -> str: return ciphertext


class KeyedEncryptor(Encryptor):
    """Reversible keyed obfuscation (XOR + base64).

    NOT a substitute for real cryptography — provided so encryption is wired
    end-to-end behind the Encryptor port. In production inject a Fernet/KMS
    implementation of this interface.
    """
    def __init__(self, key: str | None = None):
        self.key = (key or os.getenv("SECRETS_KEY", "dev-key")).encode()

    def _xor(self, data: bytes) -> bytes:
        return bytes(b ^ self.key[i % len(self.key)] for i, b in enumerate(data))

    def encrypt(self, plaintext: str) -> str:
        return base64.urlsafe_b64encode(self._xor(plaintext.encode())).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._xor(base64.urlsafe_b64decode(ciphertext)).decode()


_encryptor: Encryptor | None = None


def get_encryptor() -> Encryptor:
    global _encryptor
    if _encryptor is None:
        _encryptor = KeyedEncryptor() if os.getenv("SECRETS_KEY") else NullEncryptor()
    return _encryptor


def set_encryptor(enc: Encryptor) -> None:
    global _encryptor
    _encryptor = enc


# ── Data retention ─────────────────────────────────────────────────────────────

# Tables safe to age out by created_at, with their retention windows (days).
_RETENTION_TABLES = {
    "events_log": 90,
    "audit_log": 365,
    "usage_records": 365,
    "llm_costs": 365,
    "workflow_events": 90,
}


def apply_retention(overrides: dict[str, int] | None = None) -> dict[str, int]:
    """Delete rows older than each table's retention window. Returns {table: deleted}."""
    policy = {**_RETENTION_TABLES, **(overrides or {})}
    deleted: dict[str, int] = {}
    with database.get_connection() as conn:
        for table, days in policy.items():
            cur = conn.execute(
                f"DELETE FROM {table} WHERE created_at < DATETIME('now', ?)",
                (f"-{days} days",),
            )
            deleted[table] = cur.rowcount
    return deleted


# ── GDPR deletion ──────────────────────────────────────────────────────────────

_TENANT_TABLES = [
    "events_log", "dead_letter_queue", "workflow_runs", "audit_log",
    "usage_records", "llm_costs", "platform_users", "workspaces", "tenants",
]


def delete_tenant_data(tenant_id: str) -> dict[str, int]:
    """GDPR right-to-erasure: remove all tenant-scoped data. Returns {table: deleted}."""
    deleted: dict[str, int] = {}
    with database.get_connection() as conn:
        for table in _TENANT_TABLES:
            cur = conn.execute(f"DELETE FROM {table} WHERE tenant_id = ?", (tenant_id,))
            deleted[table] = cur.rowcount
    return deleted
