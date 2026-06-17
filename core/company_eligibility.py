"""
Company Eligibility Intelligence Layer (Phase 1.6).

Provides company-level intelligence about visa friendliness, English
environment, international student accessibility, and MBA suitability.

This layer augments JD-text-based eligibility and career pivot scores
with known company reputation when JD text is silent on a dimension.

Matching strategy:
  1. Exact normalized match (case-insensitive, legal suffixes stripped)
  2. Partial containment match (handles "Amazon AWS" vs "Amazon")
  3. Fallback: NEUTRAL_PROFILE (all scores = 5, is_fallback = True)

Usage:
    from core.company_eligibility import lookup_company, NEUTRAL_PROFILE

    profile = lookup_company("Amazon")
    # profile.visa_friendliness_score == 10
    # profile.is_fallback == False

    unknown = lookup_company("Random Corp")
    # unknown.is_fallback == True  (neutral scores applied)
"""
import re
from dataclasses import dataclass

from core import database

_LEGAL_SUFFIXES = re.compile(
    r"\b(?:gmbh|spa|s\.p\.a\.|ltd|limited|inc|incorporated|se|ag|nv|bv"
    r"|srl|sarl|sa|plc|llc|llp|kg|co\.?|group|holding|holdings)\b",
    re.IGNORECASE,
)


def _normalize(name: str) -> str:
    """Lowercase, strip legal suffixes, collapse whitespace."""
    n = name.lower().strip()
    n = _LEGAL_SUFFIXES.sub("", n)
    return re.sub(r"\s+", " ", n).strip()


# ── Profile dataclass ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CompanyEligibilityProfile:
    company_name:                str
    visa_friendliness_score:     int   # 0–10
    english_environment_score:   int   # 0–10
    international_student_score: int   # 0–10
    mba_friendliness_score:      int   # 0–10
    is_fallback:                 bool = False


NEUTRAL_PROFILE = CompanyEligibilityProfile(
    company_name                = "Unknown",
    visa_friendliness_score     = 5,
    english_environment_score   = 5,
    international_student_score = 5,
    mba_friendliness_score      = 5,
    is_fallback                 = True,
)


# ── In-process profile cache ───────────────────────────────────────────────────
# Small table (dozens of rows) — loaded once per process, refreshed by clear_cache().

_profiles_cache: list[dict] | None = None


def _get_profiles() -> list[dict]:
    global _profiles_cache
    if _profiles_cache is None:
        _profiles_cache = database.get_all_company_eligibility_profiles()
    return _profiles_cache


def clear_cache() -> None:
    """Invalidate the in-process profile cache. Call after bulk upserts."""
    global _profiles_cache
    _profiles_cache = None


# ── Public lookup ─────────────────────────────────────────────────────────────

def lookup_company(company_name: str) -> CompanyEligibilityProfile:
    """
    Return the eligibility profile for company_name.
    Falls back to NEUTRAL_PROFILE if no match is found.
    """
    if not company_name or not company_name.strip():
        return NEUTRAL_PROFILE

    norm = _normalize(company_name)
    profiles = _get_profiles()

    # Phase 1: exact normalized match
    for row in profiles:
        if _normalize(row["company_name"]) == norm:
            return _row_to_profile(row)

    # Phase 2: partial containment match
    for row in profiles:
        db_norm = _normalize(row["company_name"])
        if db_norm and (db_norm in norm or norm in db_norm):
            return _row_to_profile(row)

    return NEUTRAL_PROFILE


def _row_to_profile(row: dict) -> CompanyEligibilityProfile:
    return CompanyEligibilityProfile(
        company_name                = row["company_name"],
        visa_friendliness_score     = int(row["visa_friendliness_score"]),
        english_environment_score   = int(row["english_environment_score"]),
        international_student_score = int(row["international_student_score"]),
        mba_friendliness_score      = int(row["mba_friendliness_score"]),
        is_fallback                 = False,
    )
