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


def main() -> None:
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
