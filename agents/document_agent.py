"""Document Generation Agent (Phase 4).

For every job scored >= a threshold (default 80), generate a tailored resume and
cover letter with Claude, write them to outputs/documents/, and record each in
the documents table. Uses persisted company research when available.

Degrades gracefully: missing ANTHROPIC_API_KEY or a failed call skips the job
rather than crashing the autonomous pipeline. Entry point: run().
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from core import config, database
from core.event_log import log_event
from core.profile_loader import load as load_profile
from core.prompt_loader import load, version

_CLAUDE_DELAY_SECONDS = 0.3
_DOCS_SUBDIR = "documents"


def _slug(text: str, max_len: int = 40) -> str:
    text = re.sub(r"[^\w\s-]", "", (text or "").lower()).strip()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:max_len].strip("-") or "untitled"


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _research_context(job_id: int) -> str:
    research = database.get_research_for_job(job_id)
    if not research:
        return "No company research available."
    # talking_points holds the structured JSON payload from research_agent.
    try:
        data = json.loads(research.get("talking_points") or "{}")
        return json.dumps(data, ensure_ascii=False, indent=1)
    except (json.JSONDecodeError, TypeError):
        return research.get("mission", "") or "No company research available."


def _build_message(prompt_name: str, profile: dict, job: dict, research_ctx: str) -> str:
    instructions = load(prompt_name)
    return f"""{instructions}

---
CANDIDATE PROFILE (JSON):
{json.dumps(profile, ensure_ascii=False)[:6000]}

---
TARGET JOB
Title   : {job.get('title', '')}
Company : {job.get('company', '')}
Location: {job.get('location', '')}

JOB DESCRIPTION (first 3000 chars):
{(job.get('description') or '')[:3000]}

---
COMPANY RESEARCH:
{research_ctx}
"""


def _call_claude(message: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=config.CLAUDE_MAX_TOKENS,
        messages=[{"role": "user", "content": message}],
    )
    return resp.content[0].text.strip()


def _write_doc(job: dict, doc_type: str, content: str, doc_version: int) -> Path:
    docs_dir = Path(config.OUTPUTS_DIR) / _DOCS_SUBDIR
    docs_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{_slug(job.get('company'))}_{_slug(job.get('title'))}_{doc_type}_v{doc_version}.md"
    path = docs_dir / fname
    path.write_text(content, encoding="utf-8")
    return path


def _generate_one(job: dict, doc_type: str, prompt_name: str, profile: dict,
                  research_ctx: str, source_hash: str, research_hash: str) -> str:
    """Generate a document unless an identical one already exists.

    Reuses the latest version when job text, prompt, and research are all
    unchanged. Returns 'reused' or 'generated'.
    """
    prompt_hash = version(prompt_name)
    latest = database.get_latest_document(job["id"], doc_type)
    if latest and (latest.get("source_hash") == source_hash
                   and latest.get("prompt_hash") == prompt_hash
                   and latest.get("research_hash") == research_hash):
        log_event("documents", "document_reused", job_id=job["id"],
                  company=job.get("company"), status=f"{doc_type}_v{latest.get('version')}")
        return "reused"

    new_version = (latest.get("version") or 0) + 1 if latest else 1
    content = _call_claude(_build_message(prompt_name, profile, job, research_ctx))
    path = _write_doc(job, doc_type, content, new_version)
    database.insert_document(
        job_id           = job["id"],
        doc_type         = doc_type,
        file_path        = str(path),
        file_name        = path.name,
        word_count       = len(content.split()),
        version          = new_version,
        langgraph_run_id = "",
        prompt_version   = prompt_hash,
        source_hash      = source_hash,
        prompt_hash      = prompt_hash,
        research_hash    = research_hash,
    )
    log_event("documents", "document_generated", job_id=job["id"],
              company=job.get("company"), status=f"{doc_type}_v{new_version}")
    return "generated"


def run(min_score: int = 80, max_workers: int = 1) -> dict:
    """Generate resume + cover letter for every job scored >= min_score.

    max_workers > 1 generates documents for multiple jobs in parallel (v4 perf).
    Returns a stats dict: {eligible, resumes, cover_letters, reused, errors, reason}.
    """
    stats = {"eligible": 0, "resumes": 0, "cover_letters": 0,
             "reused": 0, "errors": 0, "reason": ""}

    jobs = database.get_jobs_above_threshold(min_score)
    stats["eligible"] = len(jobs)
    if not jobs:
        stats["reason"] = "no jobs above threshold"
        return stats

    if not os.environ.get("ANTHROPIC_API_KEY"):
        stats["reason"] = "ANTHROPIC_API_KEY not set — document generation skipped"
        print(f"  ⓘ {stats['reason']}")
        return stats

    profile = load_profile()

    def _one(job: dict) -> dict:
        research_ctx  = _research_context(job["id"])
        source_hash   = _hash(job.get("description"))
        research_hash = _hash(research_ctx)
        local = {"resumes": 0, "cover_letters": 0, "reused": 0}
        for doc_type, prompt_name, counter in (
            ("resume", "resume_prompt", "resumes"),
            ("cover_letter", "cover_letter_prompt", "cover_letters"),
        ):
            result = _generate_one(job, doc_type, prompt_name, profile,
                                   research_ctx, source_hash, research_hash)
            if result == "generated":
                local[counter] += 1
            else:
                local["reused"] += 1
        return local

    from core.concurrency import parallel_map
    results = parallel_map(_one, jobs, max_workers=max_workers,
                           rate_limit=_CLAUDE_DELAY_SECONDS)
    for job, res in zip(jobs, results):
        if res.ok:
            stats["resumes"]       += res.value["resumes"]
            stats["cover_letters"] += res.value["cover_letters"]
            stats["reused"]        += res.value["reused"]
        else:
            print(f"  [documents] {job.get('company')} — {job.get('title')}: {res.error}")
            stats["errors"] += 1

    return stats


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    database.initialize()
    print(run())
