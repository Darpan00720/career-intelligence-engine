"""Public job-board search via Apify actors — LinkedIn, Indeed, Welcome to the Jungle.

Phase-2 design: these boards (LinkedIn especially) have anti-scraping protections,
so they're searched through Apify managed actors rather than direct requests.

Graceful degradation: apify-client is lazy-imported and every function returns []
when apify-client is not installed OR APIFY_API_KEY is unset — so the ATS search
path keeps working with no Apify. To enable:  pip install apify-client  and set
APIFY_API_KEY in .env.

Actor ids are env-configurable because Apify-store actor ids change and you must
have access to the actor you point at. The run_input shapes below are sensible
defaults; align them with your chosen actor's input schema if it differs.
"""
from __future__ import annotations

import json
import os

from core.logging_config import get_logger

logger = get_logger(__name__)

# Actor ids are read from the env at call time and DEFAULT TO EMPTY — never a
# guessed id. A board stays off until you set its verified actor id in .env.
def _actor(env_name: str) -> str:
    return os.getenv(env_name, "").strip()


def _rows() -> int:
    return int(os.getenv("APIFY_ROWS_PER_QUERY", "50"))


def _max_keywords() -> int:
    return int(os.getenv("APIFY_MAX_KEYWORDS", "3"))


def _client():
    token = os.getenv("APIFY_API_KEY") or os.getenv("APIFY_TOKEN")
    if not token:
        return None
    try:
        from apify_client import ApifyClient
    except ImportError:
        logger.warning("apify-client not installed — run: pip install apify-client")
        return None
    return ApifyClient(token)


def _run_actor(actor_id: str, run_input: dict) -> list[dict]:
    """Call an Apify actor and return its dataset items. Resilient: [] on any error."""
    client = _client()
    if client is None or not actor_id:
        return []
    try:
        run = client.actor(actor_id).call(run_input=run_input)
        return list(client.dataset(run["defaultDatasetId"]).iterate_items())
    except Exception as exc:  # network / actor / quota — never abort the search
        logger.warning("apify actor %s failed: %s", actor_id, exc)
        return []


def _norm(raw: dict, source: str) -> dict:
    """Map an Apify job item to the standard search dict (tolerant of actor schemas)."""
    g = raw.get
    title = g("title") or g("positionName") or g("jobTitle") or ""
    company = g("companyName") or g("company") or g("employer") or ""
    location = g("location") or g("jobLocation") or g("place") or ""
    url = g("jobUrl") or g("url") or g("link") or g("applyUrl") or g("externalApplyLink") or ""
    desc = g("description") or g("descriptionText") or g("jobDescription") or g("descriptionHtml") or ""
    posted = g("postedAt") or g("postedDate") or g("publishedAt") or g("date") or ""
    return {
        "title": str(title).strip(),
        "company": str(company).strip(),
        "location": str(location).strip(),
        "job_board": source,
        "url": str(url).strip(),
        "description": str(desc).strip(),
        "posted_date": str(posted)[:10],
        "raw_data": json.dumps(raw, ensure_ascii=False)[:20000],
    }


def fetch_linkedin(keyword: str, location: str) -> list[dict]:
    run_input = {"title": keyword, "location": location, "rows": _rows(),
                 "publishedAt": "r604800"}  # last 7 days
    return [_norm(j, "linkedin") for j in _run_actor(_actor("APIFY_LINKEDIN_ACTOR"), run_input)]


def fetch_indeed(keyword: str, location: str) -> list[dict]:
    run_input = {"position": keyword, "location": location, "maxItems": _rows(),
                 "parseCompanyDetails": False}
    return [_norm(j, "indeed") for j in _run_actor(_actor("APIFY_INDEED_ACTOR"), run_input)]


def fetch_wttj(keyword: str, location: str) -> list[dict]:
    run_input = {"query": keyword, "aroundQuery": location, "maxItems": _rows()}
    return [_norm(j, "wttj") for j in _run_actor(_actor("APIFY_WTTJ_ACTOR"), run_input)]


def _query_terms(profile: dict) -> tuple[list[str], str]:
    """Derive search keywords + a primary location from the candidate profile."""
    roles = profile.get("target_roles", {}) or {}
    kws: list[str] = []
    for key, val in roles.items():
        if (key.startswith("track_1") or key.startswith("track_2")) and isinstance(val, list):
            kws.extend(str(x) for x in val)
    variants = roles.get("search_keyword_variants")
    if isinstance(variants, list):
        kws.extend(str(x) for x in variants)
    geo = profile.get("target_geography", {}) or {}
    locs = geo.get("preferred_locations") or []
    location = (locs[0] if locs else None) or geo.get("based_in") or "Europe"
    # de-dup, preserve order, cap for cost
    seen, ordered = set(), []
    for k in kws:
        if k not in seen:
            seen.add(k)
            ordered.append(k)
    return ordered[:_max_keywords()], str(location)


def search_public_boards(profile: dict) -> list[dict]:
    """Search LinkedIn + Indeed (+ WTTJ if configured) for the profile's roles.

    Returns normalized job dicts (same shape as the ATS connectors) ready for
    search_agent._process_job. Empty list when Apify is unavailable.
    """
    if _client() is None:
        return []
    keywords, location = _query_terms(profile)
    out: list[dict] = []
    for kw in keywords:
        out += fetch_linkedin(kw, location)
        out += fetch_indeed(kw, location)
        out += fetch_wttj(kw, location)
    logger.info("apify public boards: %d job(s) for %d keyword(s) @ %s",
                len(out), len(keywords), location)
    return out
