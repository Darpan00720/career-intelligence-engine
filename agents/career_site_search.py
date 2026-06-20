"""CareerSiteSource — find jobs on company career pages that use no ATS.

Free, no API keys, no credits. Many large EU companies (SAP, Siemens, Bosch,
Lufthansa, Adidas, Zalando, …) don't use Greenhouse/Lever/Ashby/SmartRecruiters
but DO expose machine-readable jobs. Two strategies, most reliable first:

  1. JSON-LD `JobPosting` (schema.org) embedded in the careers-page HTML — the
     same structured data Google for Jobs consumes; widely available.
  2. RSS / Atom job feeds.

Curated via data/career_sites.json (created/expanded by you):
  [{"name": "Zalando", "url": "https://jobs.zalando.com/en/jobs", "type": "jsonld"},
   {"name": "Acme",    "url": "https://acme.com/careers/feed.xml", "type": "rss"}]

No file → no career-site jobs (graceful). Output dicts match the ATS connectors,
so they flow through search_agent._process_job (dedup/gating/persistence).
"""
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET

import requests

from core import config
from core.logging_config import get_logger

logger = get_logger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (career-intelligence-agent)"}
_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _iter_jobpostings(data):
    """Yield schema.org JobPosting objects from any JSON-LD value (object,
    @graph, ItemList, or a bare list)."""
    if isinstance(data, list):
        for item in data:
            yield from _iter_jobpostings(item)
    elif isinstance(data, dict):
        types = data.get("@type")
        types = types if isinstance(types, list) else [types]
        if "JobPosting" in types:
            yield data
        if isinstance(data.get("@graph"), list):
            yield from _iter_jobpostings(data["@graph"])
        if "ItemList" in types:
            for el in data.get("itemListElement", []):
                yield from _iter_jobpostings(el.get("item") if isinstance(el, dict) else el)


def _jobposting_location(jp: dict) -> str:
    loc = jp.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if not isinstance(loc, dict):
        return ""
    addr = loc.get("address")
    if isinstance(addr, str):
        return addr
    if isinstance(addr, dict):
        parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
        return ", ".join(p for p in parts if isinstance(p, str) and p)
    return ""


def _norm_jobposting(jp: dict, company: str, source_url: str) -> dict:
    org = jp.get("hiringOrganization")
    company_name = (org.get("name") if isinstance(org, dict) else None) or company or ""
    return {
        "title":       str(jp.get("title") or "").strip(),
        "company":     str(company_name).strip(),
        "location":    _jobposting_location(jp),
        "job_board":   "career_site",
        "url":         str(jp.get("url") or jp.get("@id") or source_url or "").strip(),
        "description": _strip_html(jp.get("description") or ""),
        "posted_date": str(jp.get("datePosted") or "")[:10],
        "raw_data":    json.dumps(jp, ensure_ascii=False)[:20000],
    }


def fetch_jsonld(url: str, company: str = "") -> list[dict]:
    try:
        resp = requests.get(url, timeout=20, headers=_UA)
        if resp.status_code >= 400:
            return []
        out: list[dict] = []
        for block in _LDJSON_RE.findall(resp.text):
            try:
                data = json.loads(block.strip())
            except json.JSONDecodeError:
                continue
            for jp in _iter_jobpostings(data):
                out.append(_norm_jobposting(jp, company, url))
        return out
    except requests.RequestException as exc:
        logger.warning("career_site jsonld %s failed: %s", url, exc)
        return []


def fetch_rss(url: str, company: str = "") -> list[dict]:
    try:
        resp = requests.get(url, timeout=20, headers=_UA)
        if resp.status_code >= 400:
            return []
        root = ET.fromstring(resp.content)
    except (requests.RequestException, ET.ParseError) as exc:
        logger.warning("career_site rss %s failed: %s", url, exc)
        return []

    def _child(item, *names):
        for c in item:
            if c.tag.split("}")[-1] in names:
                return (c.text or c.get("href") or "").strip()
        return ""

    out: list[dict] = []
    for item in root.iter():
        if item.tag.split("}")[-1] in ("item", "entry"):
            out.append({
                "title":       _child(item, "title"),
                "company":     company,
                "location":    "",
                "job_board":   "career_site_rss",
                "url":         _child(item, "link"),
                "description": _strip_html(_child(item, "description", "summary", "content")),
                "posted_date": _child(item, "pubDate", "updated", "published")[:10],
                "raw_data":    "",
            })
    return out


def fetch_career_site(site: dict) -> list[dict]:
    url, company = site.get("url", ""), site.get("name", "")
    if not url:
        return []
    return fetch_rss(url, company) if site.get("type") == "rss" else fetch_jsonld(url, company)


def search_career_sites() -> list[dict]:
    """Fetch jobs from every site in data/career_sites.json. [] if the file is absent."""
    path = config.BASE_DIR / "data" / "career_sites.json"
    if not path.exists():
        return []
    try:
        sites = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("career_sites.json unreadable: %s", exc)
        return []
    out: list[dict] = []
    for site in sites if isinstance(sites, list) else []:
        out += fetch_career_site(site)
    logger.info("career sites: %d job(s) from %d site(s)", len(out), len(sites))
    return out
