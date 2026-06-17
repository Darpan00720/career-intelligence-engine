"""Test package bootstrap.

R7 (Phase 8B): isolate a THIRD-PARTY warning so it cannot fail strict
`-W error::UserWarning` runs. starlette 1.3.1's TestClient import emits
`StarletteDeprecationWarning` (a UserWarning subclass) advising `httpx2`.
This originates entirely in starlette — NOT in our code. Per the allowed
remediation, we filter ONLY that specific third-party warning, in test
bootstrap only. No engine/production code is touched.
"""
import warnings

try:  # pragma: no cover - depends on installed starlette
    from starlette.exceptions import StarletteDeprecationWarning
    warnings.filterwarnings("ignore", category=StarletteDeprecationWarning)
except Exception:
    # Fallback: match by message if the class path changes across versions.
    warnings.filterwarnings(
        "ignore",
        message=r".*httpx.*starlette\.testclient.*deprecated.*",
    )
