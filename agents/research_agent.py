"""Company Intelligence Agent (Phase 3).

For every job at or above a score threshold (default 80), research the company
with Claude and persist structured intelligence to the company_research table:
mission, recent funding/news, AI initiatives, MBA opportunities, sponsorship
likelihood, product-org maturity, and a suggested networking strategy.

Designed to degrade gracefully: if the ANTHROPIC_API_KEY is absent or a call
fails, the job is skipped (not crashed) so the autonomous pipeline still
completes. Entry point: run().
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta

from core import config, database
from core.event_log import log_event
from core.prompt_loader import load, version

_CLAUDE_DELAY_SECONDS = 0.3

# Company research is reused for this many days before a refresh is triggered.
RESEARCH_TTL_DAYS = 30


def research_is_stale(job_id: int, max_age_days: int = RESEARCH_TTL_DAYS) -> bool:
    """Return True if a job has no research, or its research is older than the TTL.

    Reuses the existing company_research.researched_at timestamp — no schema
    change. Callers skip the LLM call when this returns False.
    """
    record = database.get_research_for_job(job_id)
    if not record:
        return True
    ts = record.get("researched_at")
    if not ts:
        return True
    try:
        generated = datetime.fromisoformat(str(ts).replace("Z", "").strip())
    except ValueError:
        return True
    return (datetime.now() - generated) > timedelta(days=max_age_days)

# Aspects requested from Claude; persisted into company_research columns.
_RESEARCH_KEYS = (
    "mission", "recent_news", "ai_initiatives", "mba_opportunities",
    "sponsorship_likelihood", "product_maturity", "networking_strategy",
)


def _build_message(job: dict) -> str:
    instructions = load("company_research_prompt")
    return f"""{instructions}

---
COMPANY : {job.get('company', '')}
ROLE    : {job.get('title', '')}
LOCATION: {job.get('location', '')}

JOB DESCRIPTION (first 3000 chars):
{(job.get('description') or '')[:3000]}
"""


def _call_claude(message: str) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": message}],
    )
    raw = resp.content[0].text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()
    return json.loads(raw)


def _quality_tier(quality: int) -> str:
    if quality > config.RESEARCH_MODERATE_MAX:
        return "Strong"
    if quality > config.RESEARCH_POOR_MAX:
        return "Moderate"
    return "Poor"


def _persist(job: dict, data: dict) -> None:
    """Map the structured research onto the existing company_research columns.

    No schema migration: the seven aspects are stored across mission /
    recent_news / culture_notes / talking_points, with the full structured
    payload kept as JSON in talking_points for downstream document generation.
    """
    quality = int(data.get("research_quality", 0) or 0)
    quality = max(0, min(10, quality))

    culture_notes = (
        f"AI initiatives: {data.get('ai_initiatives', '')}\n"
        f"Product maturity: {data.get('product_maturity', '')}"
    )
    talking_points = json.dumps(
        {k: data.get(k, "") for k in _RESEARCH_KEYS}, ensure_ascii=False
    )

    database.insert_research(
        job_id                = job["id"],
        company               = job.get("company", ""),
        mission               = data.get("mission", ""),
        recent_news           = data.get("recent_news", ""),
        culture_notes         = culture_notes,
        key_people            = "",
        talking_points        = talking_points,
        research_quality      = quality,
        research_quality_tier = _quality_tier(quality),
        personalization_mode  = "company_aware",
        prompt_version        = version("company_research_prompt"),
    )


def run(min_score: int = 80, max_workers: int = 1) -> dict:
    """Research every stale/uncached job scored >= min_score.

    max_workers > 1 researches companies in parallel (v4 perf). Returns a stats
    dict: {eligible, researched, skipped_fresh, errors, reason}.
    """
    stats = {"eligible": 0, "researched": 0, "skipped_fresh": 0, "errors": 0, "reason": ""}

    jobs = database.get_jobs_above_threshold(min_score)
    stats["eligible"] = len(jobs)
    if not jobs:
        stats["reason"] = "no jobs above threshold"
        return stats

    if not os.environ.get("ANTHROPIC_API_KEY"):
        stats["reason"] = "ANTHROPIC_API_KEY not set — research skipped"
        print(f"  ⓘ {stats['reason']}")
        return stats

    # Cache: only research jobs whose research is missing or past the TTL.
    stale = []
    for job in jobs:
        if research_is_stale(job["id"]):
            stale.append(job)
        else:
            stats["skipped_fresh"] += 1
            log_event("research", "research_cache_hit",
                      job_id=job["id"], company=job.get("company"), status="cached")

    def _one(job: dict):
        start = time.perf_counter()
        data = _call_claude(_build_message(job))
        _persist(job, data)
        log_event("research", "company_researched", job_id=job["id"],
                  company=job.get("company"),
                  duration=time.perf_counter() - start, status="ok")
        return True

    from core.concurrency import parallel_map
    results = parallel_map(_one, stale, max_workers=max_workers,
                           rate_limit=_CLAUDE_DELAY_SECONDS)
    for job, res in zip(stale, results):
        if res.ok:
            stats["researched"] += 1
        else:
            stats["errors"] += 1
            print(f"  [research] {job.get('company')} — {job.get('title')}: {res.error}")
            log_event("research", "company_research_failed", job_id=job["id"],
                      company=job.get("company"), status="error",
                      error=res.error, level="ERROR")

    return stats


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    database.initialize()
    print(run())
