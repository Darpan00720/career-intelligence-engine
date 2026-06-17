"""
Phase 2 Scoring Agent.

Two-layer pipeline:
  Layer 1 — Deterministic (core/scorer.py): runs for every job, zero API cost.
  Layer 2 — Claude qualitative review: only fires for jobs above Ignore threshold (score >= 35).
             Claude returns a score_adjustment (-10 to +10) and a 2-sentence explanation.
             This catches nuance the keyword engine misses (e.g., "AI" in title but JD is ops).

Entry point: run()
"""
import json
import time

import anthropic

from core import config, database
from core.eligibility import check_eligibility
from core.language_detector import detect_jd_language
from core.profile_loader import load as load_profile
from core.prompt_loader import load
from core.scorer import ScoreResult, assign_priority_bucket, score_job

# Jobs below this deterministic score skip the Claude call entirely (Ignore bucket)
_CLAUDE_SCORE_MIN = 35

# Rate-limit guard: pause between Claude calls to avoid burst throttling
_CLAUDE_DELAY_SECONDS = 0.3


def _build_claude_message(job: dict, det: ScoreResult, profile: dict) -> str:
    """Assemble the full user message sent to Claude."""
    instructions = load("scoring_prompt")

    skill_summary = ""
    for cluster, val in det.skill_breakdown.items():
        if val > 0:
            skill_summary += f"  {cluster}: {val}\n"

    matched_cats_list = [
        f"  {cat}: {kws[:3]}" for cat, kws in det.matched_categories.items()
    ]

    return f"""{instructions}

---
JOB UNDER REVIEW
Title    : {job.get('title', '')}
Company  : {job.get('company', '')}
Location : {job.get('location', '')}
Category : {job.get('role_category', 'unknown')}  |  Track {job.get('track', '?')}
Posted   : {job.get('posted_date', 'unknown')}

DESCRIPTION (first 3000 chars):
{(job.get('description') or '')[:3000]}

---
DETERMINISTIC PRE-SCORE: {det.total_score}/100  →  {det.priority_bucket}
  Track alignment    : {det.track_alignment_score}/30
  Skill match        : {det.skill_match_score}/25
  MBA relevance      : {det.mba_relevance_score}/15
  Company quality    : {det.company_quality_score}/15
  Intl. friendliness : {det.intl_friendliness_score}/10
  Pivot bonus        : {det.pivot_bonus_score}/5

SKILL CLUSTER SCORES:
{skill_summary or '  (no cluster matches)'}
MATCHED SKILL CATEGORIES:
{chr(10).join(matched_cats_list) or '  (none)'}

---
Apply score_adjustment in range -10 to +10. Respond in the JSON format specified above.
"""


def _call_claude(message: str) -> dict:
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": message}],
    )
    raw = resp.content[0].text.strip()

    # Strip markdown code fences if Claude wraps the JSON
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    return json.loads(raw)


def _auto_explanation(det: ScoreResult) -> str:
    """Fallback explanation when Claude is not called or call fails."""
    parts: list[str] = []
    if det.track_alignment_score >= 24:
        parts.append("Strong primary track alignment (AI/Product/Strategy)")
    elif det.track_alignment_score >= 10:
        parts.append("Secondary track alignment")
    else:
        parts.append("Unclassified role")
    if det.skill_match_score >= 15:
        parts.append("strong skill keyword overlap")
    elif det.skill_match_score >= 8:
        parts.append("moderate skill overlap")
    if det.mba_relevance_score >= 10:
        parts.append("MBA-caliber opportunity signals present")
    if det.company_quality_score >= 13:
        parts.append("Tier 1 company")
    elif det.company_quality_score >= 11:
        parts.append("Tier 2 company")
    if det.pivot_bonus_score >= 4:
        parts.append("explicit AI × strategy intersection in JD")
    return ". ".join(parts) + "."


def _write_score(
    job_id: int,
    det: ScoreResult,
    explanation: str,
    profile_version: str = "unknown",
) -> None:
    metadata = {
        "track_alignment":     det.track_alignment_score,
        "skill_match":         det.skill_match_score,
        "mba_relevance":       det.mba_relevance_score,
        "company_quality":     det.company_quality_score,
        "intl_friendliness":   det.intl_friendliness_score,
        "pivot_bonus":         det.pivot_bonus_score,
        "priority_bucket":     det.priority_bucket,
        "scoring_notes":       det.scoring_notes,
        "skill_breakdown":     det.skill_breakdown,
        "semantic_similarity": det.semantic_similarity,
    }
    database.insert_score(
        job_id                  = job_id,
        role_category           = None,          # already stored on jobs table
        total_score             = det.total_score,
        role_fit                = det.track_alignment_score,
        skills_match            = det.skill_match_score,
        location_fit            = det.intl_friendliness_score,
        seniority_fit           = det.pivot_bonus_score,
        explanation             = explanation,
        matched_categories      = det.matched_categories,
        prompt_version          = "2.0",
        dictionary_version      = "1.1",
        role_dictionary_version = "1.1",
        track_alignment_score   = det.track_alignment_score,
        mba_relevance_score     = det.mba_relevance_score,
        company_quality_score   = det.company_quality_score,
        intl_friendliness_score = det.intl_friendliness_score,
        pivot_bonus_score       = det.pivot_bonus_score,
        priority_bucket         = det.priority_bucket,
        scoring_metadata        = json.dumps(metadata),
        semantic_similarity     = det.semantic_similarity if det.semantic_similarity > 0.0 else None,
    )

    # Selective JD embedding persistence (Issue #2): only store embeddings for
    # actionable jobs. Ignore and Rejected jobs are excluded to keep the
    # jd_embeddings table bounded to jobs that may actually be re-scored.
    if det.priority_bucket not in ("Ignore", "Rejected"):
        from core.semantic_matcher import flush_jd_to_db
        flush_jd_to_db(job_id, profile_version)


def run() -> None:
    profile         = load_profile()
    profile_version = profile.get("metadata", {}).get("profile_version", "unknown")

    from core.semantic_matcher import (
        warm_embedding_model, batch_embed_jobs,
        compute_calibrated_bounds, set_calibration,
    )

    # Warm the embedding model before scoring begins (eliminates cold-start latency).
    warm_embedding_model()

    # Issue A: retention — purge stale-version and aged-out JD embeddings
    # so the table stays bounded. Run before calibration to ensure only
    # current-version embeddings inform the percentile calculation.
    n_ver, n_age = database.purge_stale_jd_embeddings(profile_version)
    if n_ver or n_age:
        print(f"[Scoring Agent] Purged JD embeddings: {n_ver} stale-version, {n_age} aged-out.")

    # Issue B: wire calibrated sim bounds — uses historical embeddings from
    # previous runs to derive P10/P90. Falls back to static [0.30, 0.82]
    # when fewer than 20 embeddings are stored.
    floor, ceil = compute_calibrated_bounds(profile, profile_version)
    set_calibration(floor, ceil)

    jobs = database.get_unscored_jobs()

    if not jobs:
        print("[Scoring Agent] No unscored jobs found.")
        return

    print(f"\n[Scoring Agent] Scoring {len(jobs)} jobs...")

    # Issue #7: pre-encode all JD texts in a single model forward pass before
    # the per-job loop. Results land in _jd_mem_cache so per-job calls are
    # cache-hits. persist=False defers SQLite writes to _write_score.
    n_encoded = batch_embed_jobs(jobs, profile_version, persist=False)
    if n_encoded:
        print(f"[Scoring Agent] Pre-encoded {n_encoded} JD embeddings (batch).")

    stats: dict[str, int] = {
        "total_unscored":             len(jobs),
        "lang_detect_non_english":    0,
        "lang_detect_review_req":     0,
        "lang_detect_auto_rejected":  0,   # → Rejected (language detection)
        "language_rejected":          0,   # → Rejected (language gate)
        "visa_rejected":              0,   # → Rejected (visa gate)
        "rejected":                   0,   # total jobs written to Rejected bucket
        "eligible":                   0,
        "total_eligibility_score":    0,
        "ignored":                    0,   # Eligible + below strategic threshold
        "scored":                     0,
        "claude_called":              0,
        "claude_errors":              0,
    }
    bucket_counts: dict[str, int] = {}
    lang_detect_counts: dict[str, int] = {}   # language → count for non-English JDs

    for job in jobs:
        try:
            desc = job.get("description") or ""

            # ── Phase 1.5c: Language detection ────────────────────────────────
            lang_det = detect_jd_language(desc)
            database.update_job_language_detection(
                job_id                   = job["id"],
                detected_language        = lang_det.detected_language,
                language_risk            = lang_det.language_risk,
                eligibility_review_required = lang_det.eligibility_review_required,
            )
            if lang_det.language_risk == "HIGH":
                stats["lang_detect_non_english"] += 1
                lang_detect_counts[lang_det.detected_language] = (
                    lang_detect_counts.get(lang_det.detected_language, 0) + 1
                )
                if lang_det.eligibility_review_required:
                    stats["lang_detect_review_req"] += 1
                # Auto-reject when the config flag is enabled
                if config.NON_ENGLISH_AUTO_REJECT:
                    database.set_job_eligibility_status(job["id"], "REJECTED")
                    database.insert_rejected_score(
                        job_id           = job["id"],
                        rejection_reason = f"Language-detected: {lang_det.detected_language}",
                    )
                    stats["lang_detect_auto_rejected"] += 1
                    stats["rejected"] += 1
                    bucket_counts["Rejected"] = bucket_counts.get("Rejected", 0) + 1
                    continue

            # ── Phase 1.5: Eligibility gates ──────────────────────────────────
            eligibility = check_eligibility(desc, company=job.get("company", ""))
            database.update_job_eligibility(
                job_id                   = job["id"],
                language_gate            = eligibility.language_gate,
                language_rejection_reason= eligibility.language_rejection_reason,
                visa_gate                = eligibility.visa_gate,
                visa_rejection_reason    = eligibility.visa_rejection_reason,
                eligibility_status       = eligibility.eligibility_status,
                eligibility_score        = eligibility.eligibility_score,
                language_accessibility   = eligibility.language_accessibility,
                visa_accessibility       = eligibility.visa_accessibility,
                english_environment      = eligibility.english_environment,
                international_signals    = eligibility.international_signals,
            )
            if not eligibility.eligible:
                if eligibility.language_gate == "FAIL":
                    stats["language_rejected"] += 1
                if eligibility.visa_gate == "FAIL":
                    stats["visa_rejected"] += 1
                database.insert_rejected_score(
                    job_id           = job["id"],
                    rejection_reason = eligibility.rejection_reason,
                )
                stats["rejected"] += 1
                bucket_counts["Rejected"] = bucket_counts.get("Rejected", 0) + 1
                continue
            stats["eligible"] += 1
            stats["total_eligibility_score"] += eligibility.eligibility_score

            # ── Layer 1: deterministic pre-score ──────────────────────────────
            det = score_job(job)

            if det.total_score < _CLAUDE_SCORE_MIN:
                # Ignore bucket — write score, skip Claude
                stats["ignored"] += 1
                _write_score(job["id"], det, "Below scoring threshold.", profile_version)
                bucket_counts["Ignore"] = bucket_counts.get("Ignore", 0) + 1
                continue

            # ── Layer 2: Claude qualitative review ────────────────────────────
            explanation = _auto_explanation(det)
            try:
                message = _build_claude_message(job, det, profile)
                claude_data = _call_claude(message)

                adjustment = int(claude_data.get("score_adjustment", 0))
                adjustment = max(-10, min(10, adjustment))

                if adjustment != 0:
                    det.total_score = min(100, max(0, det.total_score + adjustment))
                    det.priority_bucket = assign_priority_bucket(det.total_score)

                explanation = claude_data.get("explanation") or explanation

                stats["claude_called"] += 1
                time.sleep(_CLAUDE_DELAY_SECONDS)

            except Exception as exc:
                print(f"  [Claude] {job.get('company')} — {job.get('title')}: {exc}")
                stats["claude_errors"] += 1

            _write_score(job["id"], det, explanation, profile_version)
            stats["scored"] += 1
            bucket_counts[det.priority_bucket] = bucket_counts.get(det.priority_bucket, 0) + 1

        except Exception as exc:
            print(f"  [ERROR] job_id={job.get('id')}: {exc}")
            stats["claude_errors"] += 1

    _print_summary(stats, bucket_counts, lang_detect_counts)


def _print_summary(stats: dict, bucket_counts: dict, lang_detect_counts: dict) -> None:
    with database.get_connection() as conn:
        total_db = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        lang_rej_all = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE language_gate = 'FAIL'"
        ).fetchone()[0]
        visa_rej_all = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE visa_gate = 'FAIL'"
        ).fetchone()[0]
        ignored_all = conn.execute(
            "SELECT COUNT(*) FROM scores WHERE priority_bucket = 'Ignore'"
        ).fetchone()[0]
        non_en_all_time = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE language_risk = 'HIGH'"
        ).fetchone()[0]

    auto_reject_flag = "ON" if config.NON_ENGLISH_AUTO_REJECT else "OFF"
    avg_elig = (
        stats["total_eligibility_score"] // stats["eligible"]
        if stats["eligible"] else 0
    )

    W = 58
    print("\n" + "═" * W)
    print("  SCORING AGENT — RUN SUMMARY")
    print("═" * W)
    print(f"  Jobs in database               : {total_db}")
    print(f"  Processed this run             : {stats['total_unscored']}")
    print("─" * W)
    print(f"  Language detection  (NON_ENGLISH_AUTO_REJECT={auto_reject_flag})")
    print(f"  Non-English detected           : {stats['lang_detect_non_english']}")
    if lang_detect_counts:
        for lang, n in sorted(lang_detect_counts.items(), key=lambda x: -x[1]):
            print(f"    {lang:<29}: {n}")
    print(f"  Flagged for review             : {stats['lang_detect_review_req']}")
    print(f"  Auto-rejected (lang detect)    : {stats['lang_detect_auto_rejected']}")
    print("─" * W)
    print(f"  Rejected (this run)            : {stats['rejected']}")
    print(f"    incl. Language Rejected      : {stats['language_rejected']}")
    print(f"    incl. Visa Rejected          : {stats['visa_rejected']}")
    print(f"  Passed eligibility             : {stats['eligible']}")
    print(f"  Avg eligibility score          : {avg_elig}/100")
    print("─" * W)
    print(f"  Scored (above threshold)       : {stats['scored']}")
    print(f"  Ignore (low strategic fit)     : {stats['ignored']}")
    print(f"  Claude calls                   : {stats['claude_called']}")
    print(f"  Claude errors                  : {stats['claude_errors']}")
    print("─" * W)
    order = ["Apply Immediately", "High Priority", "Medium Priority",
             "Low Priority", "Ignore", "Rejected"]
    for bucket in order:
        n = bucket_counts.get(bucket, 0)
        if n:
            print(f"  {bucket:<31}: {n}")
    print("─" * W)
    print(f"  Language Rejected  (all-time)  : {lang_rej_all}")
    print(f"  Visa Rejected      (all-time)  : {visa_rej_all}")
    print(f"  Ignore             (all-time)  : {ignored_all}")
    print(f"  Non-English detected(all-time) : {non_en_all_time}")
    print("═" * W)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    from core.database import initialize
    initialize()
    run()
