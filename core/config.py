from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

# --- File paths ---
PROFILE_PATH    = BASE_DIR / "data" / "candidate_profile.json"
SKILL_DICT_PATH = BASE_DIR / "data" / "skill_dictionary.json"
ROLE_DICT_PATH  = BASE_DIR / "data" / "role_dictionary.json"
PROMPTS_DIR     = BASE_DIR / "prompts"
DB_PATH         = BASE_DIR / "data" / "career_agent.db"
OUTPUTS_DIR     = BASE_DIR / "outputs"
LOGS_DIR        = BASE_DIR / "data" / "logs"

# --- Scoring ---
MIN_SCORE_THRESHOLD      = 70
MAX_APPLICATIONS_PER_WEEK = 15

# --- Research quality tier thresholds ---
RESEARCH_POOR_MAX     = 3   # 0–3 → Poor → generic
RESEARCH_MODERATE_MAX = 6   # 4–6 → Moderate → company_aware
                             # 7–10 → Strong → deep_personalization

# --- LangGraph ---
MAX_DOCUMENT_RETRIES = 2

# --- Language Detection ---
# Set True to automatically reject JDs whose primary language is not English.
# When False (default) non-English JDs are flagged for human review but still scored.
NON_ENGLISH_AUTO_REJECT   = False
LANG_DETECT_MIN_CHARS     = 50    # below this, detection is unreliable → "Unknown"
LANG_DETECT_SAMPLE_CHARS  = 3000  # feed only the first N chars to the detector

# --- Claude ---
CLAUDE_MODEL = "claude-sonnet-4-6"

CLAUDE_MAX_TOKENS     = 4096
