"""Adzuna job search — official free REST API (app_id + app_key).

Adzuna aggregates listings from many boards (including roles also posted on
LinkedIn / Indeed / company sites) with strong EU coverage. Unlike LinkedIn/
Indeed, it has a documented public API — no scraping, no Apify, no Apify credits.

Enable by registering a free account at https://developer.adzuna.com/ and setting
in .env:
    ADZUNA_APP_ID=...
    ADZUNA_APP_KEY=...
Without both, every function is a graceful no-op. Output dicts match the other
connectors, so results flow through search_agent._process_job (dedup/gating).
"""
from __future__ import annotations

import json
import os

import requests

from core.logging_config import get_logger

logger = get_logger(__name__)

_BASE = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"
# Target geography (the candidate is EU-focused). Override via ADZUNA_COUNTRIES.
_DEFAULT_COUNTRIES = "it,gb,de,fr,nl,es"


def _creds() -> tuple[str | None, str | None]:
    return os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")


def _countries() -> list[str]:
    return [c.strip() for c in os.getenv("ADZUNA_COUNTRIES", _DEFAULT_COUNTRIES).split(",") if c.strip()]


def _rows() -> int:
    return int(os.getenv("ADZUNA_ROWS_PER_QUERY", "50"))


def _norm(raw: dict, country: str) -> dict:
    loc = raw.get("location") or {}
    company = raw.get("company") or {}
    return {
        "title":       (raw.get("title") or "").strip(),
        "company":     (company.get("display_name") if isinstance(company, dict) else "") or "",
        "location":    (loc.get("display_name") if isinstance(loc, dict) else "") or country.upper(),
        "job_board":   "adzuna",
        "url":         (raw.get("redirect_url") or "").strip(),
        "description": (raw.get("description") or "").strip(),
        "posted_date": str(raw.get("created") or "")[:10],
        "raw_data":    json.dumps(raw, ensure_ascii=False)[:20000],
    }


def fetch_adzuna(keyword: str, country: str) -> list[dict]:
    app_id, app_key = _creds()
    if not (app_id and app_key):
        return []
    params = {
        "app_id": app_id, "app_key": app_key, "what": keyword,
        "results_per_page": _rows(), "max_days_old": 30, "content-type": "application/json",
    }
    try:
        resp = requests.get(_BASE.format(country=country), params=params, timeout=20)
        if resp.status_code != 200:
            logger.warning("adzuna %s '%s': HTTP %s", country, keyword, resp.status_code)
            return []
        return [_norm(j, country) for j in resp.json().get("results", [])]
    except requests.RequestException as exc:
        logger.warning("adzuna %s '%s' failed: %s", country, keyword, exc)
        return []


def search_adzuna(profile: dict) -> list[dict]:
    """Search Adzuna across target countries for the profile's roles. Empty list
    when credentials are absent."""
    if not all(_creds()):
        return []
    from agents.apify_search import _query_terms  # reuse keyword derivation

    keywords, _loc = _query_terms(profile)
    out: list[dict] = []
    for kw in keywords:
        for country in _countries():
            out += fetch_adzuna(kw, country)
    logger.info("adzuna: %d job(s) for %d keyword(s) x %d countries",
                len(out), len(keywords), len(_countries()))
    return out
