"""PreFilter — cheap, deterministic, pre-persistence job filtering.

Runs before persistence and scoring so high-volume sources (LinkedIn / Indeed /
WTTJ) can't flood the DB and scorer with obviously-irrelevant rows. This is a
COARSE gate only — it deliberately does NOT do scoring, taxonomy, language/visa
gates, embeddings, or LLM calls. Those remain the responsibility of the existing
downstream nodes.

Rejection rules (in order):
  1. expired         — job["is_expired"] is truthy
  2. duplicate       — same (company, title, location) seen already
  3. geo_rejected    — location clearly outside the active geo focus
                       (GEO_COUNTRIES defaults to "it,nl"; empty = legacy EU-wide)
  4. role_rejected   — title not in the broad target-domain keyword set

Pure function, no I/O. Geo delegates to agents.search_agent.geo_outside_focus
(single source of truth) so search, ingestion, prefilter and export agree.
"""
from __future__ import annotations

import os
import re

# Intentionally broad target-domain keywords. This is a coarse pre-gate, NOT
# taxonomy/scoring — real role classification happens downstream.
ROLE_KEYWORDS = [
    "ai",
    "artificial intelligence",
    "machine learning",
    "product",
    "strategy",
    "transformation",
    "analytics",
    "data",
    "innovation",
]

# Clearly-senior title markers — the candidate targets internship/junior roles,
# so these are rejected unless the title also carries an intern/graduate signal
# (e.g. "Senior ... Internship"). "Product Manager" alone is NOT senior.
_SENIOR_TITLE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|head\s+of|director|"
    r"vice[\s-]?president|vp|chief|distinguished|president)\b", re.IGNORECASE)


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _dedup_key(job: dict) -> tuple:
    return (_norm(job.get("company")), _norm(job.get("title")), _norm(job.get("location")))


def _geo_rejected(location: str | None) -> bool:
    """True only when the location is *clearly* outside the active geo focus.
    Missing / remote / unknown locations are NOT rejected here (downstream gates
    decide). GEO_COUNTRIES defaults to "it,nl"; set it to an explicit empty
    string to reject only clearly non-EU locations (legacy)."""
    if not location:
        return False
    from agents.search_agent import geo_outside_focus  # single source of truth
    return geo_outside_focus(location)


def _role_rejected(title: str | None) -> bool:
    """True when the title is missing or contains no broad target-domain keyword."""
    t = (title or "").lower()
    if not t:
        return True
    return not any(kw in t for kw in ROLE_KEYWORDS)


def intern_only() -> bool:
    """When INTERN_ONLY is set, keep ONLY internship/graduate/trainee roles."""
    return os.getenv("INTERN_ONLY", "").strip().lower() in ("1", "true", "yes", "on")


def is_intern_role(title: str | None) -> bool:
    """True when the title signals an internship / graduate program / trainee role
    (reuses the search agent's intern-accept pattern)."""
    from agents.search_agent import _INTERN_ACCEPT
    return bool(_INTERN_ACCEPT.search((title or "").lower()))


def _seniority_rejected(title: str | None) -> bool:
    """True for clearly-senior titles (Senior/Staff/Principal/Lead/Director/VP/…),
    unless the title also carries an intern/graduate signal (override). Keeps
    intern/junior/associate/analyst and plain "Product Manager" roles."""
    t = (title or "").lower()
    if not t:
        return False
    from agents.search_agent import _INTERN_ACCEPT  # reuse existing accept signal
    if _INTERN_ACCEPT.search(t):
        return False
    return bool(_SENIOR_TITLE.search(t))


def prefilter_jobs(
    jobs: list[dict],
    profile,
    existing_job_keys: set | None = None,
) -> tuple[list[dict], dict]:
    """Filter raw jobs before persistence. Returns (kept_jobs, stats).

    `profile` is accepted for interface stability / future use; the coarse gate is
    deterministic and profile-independent (EU geography + fixed domain keywords).
    `existing_job_keys` lets callers seed the dedup set with keys already in the DB.
    """
    jobs = jobs or []
    stats = {
        "duplicates": 0,
        "expired": 0,
        "geo_rejected": 0,
        "role_rejected": 0,
        "seniority_rejected": 0,
        "non_intern_rejected": 0,
        "kept": 0,
        "total": len(jobs),
    }
    _intern_only = intern_only()
    seen: set = set(existing_job_keys) if existing_job_keys else set()
    kept: list[dict] = []

    for job in jobs:
        if job.get("is_expired"):
            stats["expired"] += 1
            continue
        key = _dedup_key(job)
        if key in seen:
            stats["duplicates"] += 1
            continue
        if _geo_rejected(job.get("location")):
            stats["geo_rejected"] += 1
            continue
        if _role_rejected(job.get("title")):
            stats["role_rejected"] += 1
            continue
        if _seniority_rejected(job.get("title")):
            stats["seniority_rejected"] += 1
            continue
        if _intern_only and not is_intern_role(job.get("title")):
            stats["non_intern_rejected"] += 1
            continue
        seen.add(key)
        kept.append(job)

    stats["kept"] = len(kept)
    return kept, stats
