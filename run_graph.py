"""ONE-COMMAND LangGraph workflow — score your CV against every JD, end to end.

Just run:

    python3 run_graph.py

(or press ▶ Run / F5 in VS Code). No flags, no env vars needed — everything is
configured below. It runs the whole pipeline through the LangGraph engine:

    score every JD vs your CV (deterministic + Claude adjustment, like main.py)
      -> research -> documents (resume + cover letter) -> export(xlsx) -> tracker

Only JDs that genuinely score >= 80% get research + documents. If none do, it
says so — nothing is forced.

COST/TIME: Claude scoring runs once per qualifying JD, so a full run makes paid
Claude calls and takes several minutes. To also SEARCH for new jobs first, set
ENABLE_ACQUISITION=1 below (off by default — your JDs are already in the DB).
"""
from __future__ import annotations

import os
import sys
import uuid

# --- Everything is configured here; no env vars needed to run -----------------
# All stages run on every run (no opt-in): search -> filter -> score -> docs -> excel.
os.environ.setdefault("ENABLE_ACQUISITION", "1")   # search career pages/portals every run
os.environ.setdefault("ENABLE_CLAUDE_SCORING", "1")  # CV-vs-JD scoring like main.py (reaches 80)
os.environ.setdefault("PERSIST_SCORES", "1")       # write scores to the DB
os.environ.setdefault("ENABLE_RESEARCH", "1")      # company research for >=80 matches
os.environ.setdefault("ENABLE_DOCUMENTS", "1")     # resume + cover letter for >=80 matches
os.environ.setdefault("ENABLE_EXPORT", "1")        # outputs/jobs_master.xlsx
os.environ.setdefault("ENABLE_TRACKER", "1")       # application-tracker rows
os.environ.setdefault("DB_JOB_LIMIT", "500")       # score all ~437 JDs, not just 10
os.environ.setdefault("INTERN_ONLY", "1")          # keep ONLY internship/graduate roles
os.environ.setdefault("GEO_COUNTRIES", "it,nl")    # focus: Netherlands + Italy only
os.environ.setdefault("NON_ENGLISH_AUTO_REJECT", "1")  # English-only: drop non-English JDs

from dotenv import load_dotenv

load_dotenv()  # API keys / model id (does not override the settings above)


def _make_claude_scoring_hook():
    """The same Claude -10..+10 adjustment agents/scoring_agent uses, so the graph
    reaches main.py-level (80) scores instead of capping at the deterministic ~70."""
    from agents.scoring_agent import _CLAUDE_SCORE_MIN, _build_claude_message, _call_claude
    from core.profile_loader import load as load_profile
    from schemas.scoring import ClaudeScoreAdjustment

    profile = load_profile()

    def _hook(job, det):
        if det.total_score < _CLAUDE_SCORE_MIN:
            return ClaudeScoreAdjustment(score_adjustment=0, explanation="below claude min")
        try:
            data = _call_claude(_build_claude_message(job, det, profile))
            adj = max(-10, min(10, int(data.get("score_adjustment", 0))))
            return ClaudeScoreAdjustment(score_adjustment=adj,
                                         explanation=(data.get("explanation") or "graph claude")[:480])
        except Exception:
            return ClaudeScoreAdjustment(score_adjustment=0, explanation="claude scoring failed")

    return _hook


def run_demo() -> None:
    """Zero-keys, zero-credit demo: score bundled sample jobs and write a dashboard.

    Runs the real pipeline (filter -> deterministic scoring -> export) against an
    ISOLATED demo database (data/demo.db) so the real career_agent.db is never
    touched. No Anthropic credit, no Adzuna keys, no live search — just the bundled
    data/demo_jobs.json. Demonstrates the geography, internship, and language
    filters and produces outputs/demo_jobs_master.xlsx.
    """
    import json
    from pathlib import Path

    from core import config, database
    from core.eligibility import check_eligibility
    from core.exporter import export_jobs_master_xlsx
    from core.language_detector import detect_jd_language
    import graph.providers as gp

    base = Path(config.BASE_DIR)
    demo_db = str(base / "data" / "demo.db")
    config.DB_PATH = demo_db        # database.get_connection() reads this dynamically
    gp.DB_PATH = demo_db            # DbJobProvider binds it at import; override here
    for ext in ("", "-wal", "-shm"):
        Path(demo_db + ext).unlink(missing_ok=True)
    database.initialize()

    # Load the sample jobs and pre-evaluate eligibility (mirrors the real
    # acquisition path) so the language/visa gates are populated for filtering.
    jobs = json.loads((base / "data" / "demo_jobs.json").read_text())
    for j in jobs:
        jid = database.insert_job(title=j["title"], company=j["company"],
                                  location=j["location"], job_board="demo",
                                  url=j["url"], description=j["description"])
        if not jid:
            continue
        elig = check_eligibility(j["description"], company=j["company"])
        database.update_job_eligibility(
            job_id=jid, language_gate=elig.language_gate,
            language_rejection_reason=elig.language_rejection_reason,
            visa_gate=elig.visa_gate, visa_rejection_reason=elig.visa_rejection_reason,
            eligibility_status=elig.eligibility_status,
            eligibility_score=elig.eligibility_score,
            language_accessibility=elig.language_accessibility,
            visa_accessibility=elig.visa_accessibility,
            english_environment=elig.english_environment,
            international_signals=elig.international_signals)
        det = detect_jd_language(j["description"])
        database.update_job_language_detection(
            job_id=jid, detected_language=det.detected_language,
            language_risk=det.language_risk,
            eligibility_review_required=det.eligibility_review_required)

    # Deterministic only — overrides the module-level defaults above.
    os.environ.update({
        "ENABLE_ACQUISITION": "0", "ENABLE_CLAUDE_SCORING": "0", "ENABLE_RESEARCH": "0",
        "ENABLE_DOCUMENTS": "0", "PERSIST_SCORES": "1", "ENABLE_EXPORT": "0",
        "ENABLE_TRACKER": "0", "INTERN_ONLY": "1", "GEO_COUNTRIES": "it,nl",
        "NON_ENGLISH_AUTO_REJECT": "true", "DB_JOB_LIMIT": "100",
    })
    config.NON_ENGLISH_AUTO_REJECT = True   # read at import; force it for the demo

    from services.career_service import analyze_profile
    print("Demo — no API keys, no credits. Scoring bundled sample jobs…\n")
    state = analyze_profile(thread_id=f"demo-{uuid.uuid4().hex[:6]}",
                            profile_path="candidate_profile.json", limit=100)

    out = export_jobs_master_xlsx(str(base / "outputs" / "demo_jobs_master.xlsx"))
    scored = state.get("scored_jobs") or []
    ing = {ij.job_id: ij for ij in (state.get("ingested_jobs") or [])}
    print(f"Sample jobs loaded : {len(jobs)}")
    print(f"Kept after filters : {len(scored)}  (Netherlands + Italy · interns · English-sufficient)\n")
    print("Ranked matches:")
    for sj in sorted(scored, key=lambda s: s.total_score or 0, reverse=True):
        j = ing.get(sj.job_id)
        company = j.company if j else "?"
        title = (j.title if j else str(sj.job_id))[:44]
        print(f"  {sj.total_score:>3}  {company} — {title}")
    print(f"\nWrote {out}")
    print("Filtered out: roles outside NL/Italy (Berlin), non-internships (Senior PM),\n"
          "and roles requiring a non-English language (Italian-required sales).")


def main() -> None:
    if "--demo" in sys.argv:
        run_demo()
        return
    from graph.persistence import _flag
    from graph.providers import clear_claude_hook, set_claude_hook
    from services.career_service import analyze_profile

    thread_id = f"vscode-{uuid.uuid4().hex[:8]}"
    print("Scoring your CV against all JDs via LangGraph (this takes a few minutes)…\n")

    if _flag("ENABLE_CLAUDE_SCORING"):
        set_claude_hook(thread_id, _make_claude_scoring_hook())
    try:
        state = analyze_profile(thread_id=thread_id,
                                profile_path="candidate_profile.json",
                                limit=500)
    finally:
        clear_claude_hook(thread_id)

    scored = state.get("scored_jobs") or []
    ing = {ij.job_id: ij for ij in (state.get("ingested_jobs") or [])}
    terminal = state.get("terminal_stats") or {}
    matched = [s for s in scored if (s.total_score or 0) >= 80]

    print(f"\nJDs scored vs your CV : {len(scored)}")
    print(f"Matches >= 80%        : {len(matched)}")
    print("\nTop CV–JD matches:")
    for sj in sorted(scored, key=lambda s: s.total_score, reverse=True)[:10]:
        j = ing.get(sj.job_id)
        tag = "  ✅ >=80" if sj.total_score >= 80 else ""
        company = j.company if j else "?"
        title = (j.title if j else str(sj.job_id))[:50]
        print(f"  {sj.total_score:>3}%  {company} — {title}{tag}")

    print("\nresearch  :", state.get("research_stats") or "—")
    print("documents :", terminal.get("documents") or "—")
    print("export    :", (terminal.get("export") or {}).get("path", "—"))
    if not matched:
        print("\nNo JD reached 80% — no documents generated (this is fine; the bar held).")


if __name__ == "__main__":
    main()
