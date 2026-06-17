"""
Phase 2 Career Pivot Scoring Engine.

Computes a 0–100 score across 6 weighted dimensions:

  track_alignment      0–30   career pivot fit (primary tracks score highest)
  skill_match          0–25   keyword overlap via weighted skill clusters
  mba_relevance        0–15   MBA-caliber opportunity signals in JD + company
  company_quality      0–15   tier-based company assessment + watchlist bonus
  intl_friendliness    0–10   EU location, visa sponsorship, international signals
  pivot_bonus          0–5    explicit AI × strategy × product intersection

Usage:
  from core.scorer import score_job
  result = score_job(job_dict)   # job_dict from database.get_unscored_jobs()
  print(result.total_score, result.priority_bucket)
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from core import config
from core.skill_loader import match_keywords


# ── Track alignment weights ────────────────────────────────────────────────────
# Reflects the stated priority order for this career pivot profile.
# Primary tracks (A) score 24–30; secondary tracks (B) score 6–18.

TRACK_ALIGNMENT: dict[str, int] = {
    # Primary — AI Product / Strategy / Digital Transformation
    "ai_strategy":            30,
    "product_management":     28,
    "digital_transformation": 26,
    "business_strategy":      24,
    # Secondary — HR / People / Workforce
    "workforce_strategy":     18,
    "hr_transformation":      16,
    "people_analytics":       14,
    "hr_analytics":           12,
    "workforce_planning":     10,
    "talent_acquisition":      6,
    # No classification
    "unknown":                 0,
}

# ── MBA relevance signal patterns ─────────────────────────────────────────────
# List of (compiled_regex, points). First match per pattern counts — no double-counting.

_MBA_SIGNALS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"\bmba\b", re.IGNORECASE), 4),
    (re.compile(
        r"\b(graduate programme?|graduate program|rotational programme?|rotational program"
        r"|graduate rotation|rotational scheme)\b", re.IGNORECASE), 3),
    (re.compile(
        r"\bleadership (programme?|program|development|track|academy)\b",
        re.IGNORECASE), 3),
    (re.compile(
        r"\b(c-suite|c suite|board level|board reporting|executive visibility"
        r"|senior leadership team|senior stakeholder)\b", re.IGNORECASE), 2),
    (re.compile(
        r"\b(transformation (project|initiative|programme?|program)"
        r"|strategic (project|initiative|assignment))\b", re.IGNORECASE), 2),
    (re.compile(
        r"\b(structured (thinking|analysis|approach|problem.solving)"
        r"|strategic frameworks?|hypothesis.driven)\b", re.IGNORECASE), 1),
    (re.compile(
        r"\b(high.?impact|high.?visibility|cross.?functional|global (exposure|experience))\b",
        re.IGNORECASE), 1),
]

# Companies that reliably run MBA-level programs or recruit heavily from MBA cohorts.
# Stored lowercase for matching against normalized company names.
_MBA_TARGET_COMPANIES: frozenset[str] = frozenset({
    "mckinsey", "bcg", "bain", "deloitte", "accenture", "kpmg", "pwc", "ey",
    "oliver wyman", "roland berger", "at kearney", "a.t. kearney", "strategy&",
    "booz allen", "booz allen hamilton",
    "google", "deepmind", "google deepmind", "amazon", "meta", "microsoft",
    "apple", "netflix", "openai", "anthropic",
    "goldman sachs", "jp morgan", "jpmorgan", "morgan stanley", "blackrock",
    "revolut", "stripe", "klarna", "n26", "spotify", "airbnb",
    "ibm", "salesforce", "workday", "servicenow", "sap",
    "boston consulting group",
})

# ── International student friendliness patterns ────────────────────────────────
# Takes highest-scoring visa signal (not additive), then adds location bonus separately.

_VISA_SIGNALS: list[tuple[re.Pattern, int]] = [
    (re.compile(
        r"\b(visa sponsorship|will sponsor|sponsoring visa|visa support"
        r"|work authorization provided|provides? visa)\b", re.IGNORECASE), 10),
    (re.compile(
        r"\b(work permit|sponsoring work permit|permit sponsorship"
        r"|relocation support|relocation assistance|relocation package)\b", re.IGNORECASE), 7),
    (re.compile(
        r"\b(eu blue card|blue card|skilled worker visa|tier ?2 visa)\b",
        re.IGNORECASE), 6),
    (re.compile(
        r"\b(global (team|company|offices|organization|workforce)"
        r"|international (team|company|environment|offices)"
        r"|multicultural|multi-national|diverse (team|workforce))\b", re.IGNORECASE), 4),
    (re.compile(
        r"\b(fully remote|remote.?first|work from anywhere|location.?flexible"
        r"|remote eligible|distributed team)\b", re.IGNORECASE), 3),
]

_TARGET_CITIES: frozenset[str] = frozenset({
    "milan", "milano", "london", "amsterdam", "berlin", "paris",
    "barcelona", "dublin", "zurich", "zürich", "munich", "münchen",
    "vienna", "wien", "stockholm", "brussels", "lisbon", "madrid",
    "oslo", "copenhagen", "helsinki", "warsaw", "prague", "budapest",
})

# ── Pivot bonus patterns ───────────────────────────────────────────────────────

_PIVOT_AI_PRODUCT_TITLE = re.compile(
    r"\b(ai product|product ai|ai strategy|digital transformation strategy"
    r"|ai.{1,15}strategy|strategy.{1,15}ai"
    r"|product (strategy|management).{1,15}(ai|digital)"
    r"|(ai|digital).{1,15}product (strategy|management))\b",
    re.IGNORECASE,
)
_PIVOT_AI_PRODUCT_JD = re.compile(
    r"\b(ai.{1,30}product|product.{1,30}ai"
    r"|ai.{1,30}strategy|strategy.{1,30}ai)\b",
    re.IGNORECASE,
)
_PIVOT_TECH_STRATEGY_TITLE = re.compile(
    r"\b(technology strategy|tech strategy|platform strategy|data strategy"
    r"|digital strategy|innovation strategy|ai roadmap|transformation strategy)\b",
    re.IGNORECASE,
)
_PIVOT_TECH_STRATEGY_JD = re.compile(
    r"\b(technology strategy|tech strategy|digital strategy|innovation strategy"
    r"|data strategy|platform strategy)\b",
    re.IGNORECASE,
)
_PIVOT_MBA_BRIDGE = re.compile(
    r"\b(ai.{1,20}(mba|graduate|intern|internship)"
    r"|(mba|graduate|intern|internship).{1,20}(ai|product|strategy|digital))\b",
    re.IGNORECASE,
)

# ── Priority bucket thresholds ─────────────────────────────────────────────────
# Score-based buckets only. "Rejected" is NOT listed here — it is written
# directly by the scoring agent for jobs that failed an eligibility gate
# (language or visa). Those jobs are never scored and never reach this table.

PRIORITY_THRESHOLDS: list[tuple[int, str]] = [
    (85, "Apply Immediately"),
    (70, "High Priority"),
    (50, "Medium Priority"),
    (35, "Low Priority"),
    (0,  "Ignore"),          # eligible but below strategic threshold
]

# Canonical bucket ordering for display / export (Rejected always last)
BUCKET_ORDER: list[str] = [
    "Apply Immediately",
    "High Priority",
    "Medium Priority",
    "Low Priority",
    "Ignore",
    "Rejected",
]


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class ScoreResult:
    total_score:             int
    track_alignment_score:   int
    skill_match_score:       int
    mba_relevance_score:     int
    company_quality_score:   int
    intl_friendliness_score: int
    pivot_bonus_score:       int
    priority_bucket:         str
    skill_breakdown:         dict  = field(default_factory=dict)
    matched_categories:      dict  = field(default_factory=dict)
    scoring_notes:           list[str] = field(default_factory=list)
    semantic_similarity:     float = 0.0   # raw cosine value from semantic_matcher


# ── Company tier loader ────────────────────────────────────────────────────────

_company_tier_cache: dict[str, int] | None = None
_watchlist_cache: set[str] | None = None

_LEGAL_SUFFIXES = re.compile(
    r"\b(gmbh|spa|s\.p\.a\.|ltd|limited|inc|incorporated|se|ag|nv|bv"
    r"|srl|sarl|sa|plc|llc|llp|kg|co\.?|group|holding|holdings)\b",
    re.IGNORECASE,
)


def _normalize_company(name: str) -> str:
    name = name.lower().strip()
    name = _LEGAL_SUFFIXES.sub("", name)
    return re.sub(r"\s+", " ", name).strip()


def _load_company_tiers() -> dict[str, int]:
    global _company_tier_cache
    if _company_tier_cache is not None:
        return _company_tier_cache
    path = Path(config.BASE_DIR) / "data" / "company_tiers.json"
    if not path.exists():
        _company_tier_cache = {}
        return _company_tier_cache
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    lookup: dict[str, int] = {}
    for tier_str, tier_data in data.get("tiers", {}).items():
        tier_num = int(tier_str)
        for name in tier_data.get("companies", []):
            lookup[_normalize_company(name)] = tier_num
    _company_tier_cache = lookup
    return _company_tier_cache


def _load_watchlist() -> set[str]:
    global _watchlist_cache
    if _watchlist_cache is not None:
        return _watchlist_cache
    path = Path(config.BASE_DIR) / "data" / "company_watchlist.json"
    if not path.exists():
        _watchlist_cache = set()
        return _watchlist_cache
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _watchlist_cache = {
            _normalize_company(c.get("name", ""))
            for c in data.get("companies", [])
        }
    except (json.JSONDecodeError, KeyError):
        _watchlist_cache = set()
    return _watchlist_cache


def _get_company_tier(company_name: str) -> int:
    tiers = _load_company_tiers()
    norm = _normalize_company(company_name)
    if norm in tiers:
        return tiers[norm]
    # Partial containment match (handles "Google LLC" vs "google")
    for known, tier in tiers.items():
        if known and (norm in known or known in norm):
            return tier
    return 4


def _is_watchlist_company(company_name: str) -> bool:
    return _normalize_company(company_name) in _load_watchlist()


# ── Dimension scorers ──────────────────────────────────────────────────────────

def _score_track_alignment(role_category: str) -> tuple[int, str]:
    score = TRACK_ALIGNMENT.get(role_category, 0)
    return score, f"track={role_category} → {score}/30"


def _keyword_score(category_matches: dict) -> int:
    """
    Convert matched_categories to a 0-25 score.

    Each matched category contributes 5 pts; capped at 25 (5 categories).
    Used as: (a) 40% component in the hybrid score when semantic is available,
             (b) full fallback score when sentence-transformers is not installed.
    """
    return min(25, len(category_matches) * 5)


def _score_skill_match(
    title: str,
    description: str,
    job_id: int | None = None,
) -> tuple[int, dict, float, str]:
    """
    Hybrid skill matching: 40% keyword + 60% semantic (all-MiniLM-L6-v2).

    Governance gate (Constraint 3): if the skill dictionary finds zero matching
    categories, the score is forced to 0 before semantic matching is attempted.
    This ensures the skill dictionary remains the gate for every scored job.

    Fallback (Issue #4): if sentence-transformers is unavailable the semantic
    step returns "sem_unavailable" and the score degrades gracefully to the
    pure keyword component — no collapse to zero when keywords do match.

    Returns: (score, detail_dict, raw_cosine_sim, note)
    """
    text             = f"{title} {description}"
    category_matches = match_keywords(text)

    # Constraint 3 gate — skill dictionary must gate scoring
    if not category_matches:
        return 0, {"matched_categories": {}}, 0.0, "kw_gate=empty"

    from core.semantic_matcher import compute_semantic_match
    from core.profile_loader import load as _load_profile
    profile = _load_profile()

    sem_score, raw_sim, sem_note = compute_semantic_match(
        {"id": job_id, "title": title, "description": description},
        profile,
        persist=False,   # defer SQLite persistence; scoring_agent flushes non-Ignore only
    )

    kw_score = _keyword_score(category_matches)

    if sem_note == "sem_unavailable":
        # Fallback: pure keyword score — preserves signal when model is not installed
        final_score = kw_score
        note = f"kw_fallback={kw_score}/25"
    else:
        # Hybrid: 40% keyword + 60% semantic
        final_score = min(25, max(0, round(0.4 * kw_score + 0.6 * sem_score)))
        note = f"hybrid=40%kw({kw_score})+60%sem({sem_score})={final_score}/25"

    detail = {"matched_categories": category_matches}
    return final_score, detail, raw_sim, note


def _score_mba_relevance(title: str, description: str, company: str) -> tuple[int, str]:
    text = f"{title} {description}"
    score = 0
    notes: list[str] = []

    for pattern, pts in _MBA_SIGNALS:
        if pattern.search(text):
            score += pts
            notes.append(f"+{pts}")

    norm = _normalize_company(company)
    is_mba_co = norm in _MBA_TARGET_COMPANIES or any(t in norm for t in _MBA_TARGET_COMPANIES)
    if is_mba_co:
        score += 3
        notes.append("+3:mba_co")

    score = min(15, score)
    return score, ",".join(notes) or "none"


def _score_company_quality(company: str, company_profile=None) -> tuple[int, str]:
    tier = _get_company_tier(company)
    watchlist = _is_watchlist_company(company)

    base = {1: 15, 2: 11, 3: 7, 4: 4}.get(tier, 4)
    bonus = 2 if watchlist else 0

    if company_profile is not None and not company_profile.is_fallback:
        mba = company_profile.mba_friendliness_score
        if mba >= 8:
            bonus += 2
        elif mba >= 6:
            bonus += 1

    score = min(15, base + bonus)

    note = f"tier={tier} base={base}"
    if watchlist:
        note += " +2:watchlist"
    if company_profile is not None and not company_profile.is_fallback:
        note += f" mba_profile={company_profile.mba_friendliness_score}"
    return score, note


def _score_intl_friendliness(description: str, location: str, company_profile=None) -> tuple[int, str]:
    text = f"{description} {location}"
    visa_score = 0
    notes: list[str] = []

    # Take the highest single visa signal (not additive — they describe the same thing)
    for pattern, pts in _VISA_SIGNALS:
        if pattern.search(text) and pts > visa_score:
            visa_score = pts
            notes.append(f"+{pts}:visa")

    # Location bonus — additive and independent
    loc_lower = location.lower()
    location_bonus = 0
    if any(city in loc_lower for city in _TARGET_CITIES):
        location_bonus = 2
        notes.append("+2:target_city")
    elif re.search(r"\b(remote|hybrid)\b", loc_lower):
        location_bonus = 1
        notes.append("+1:remote")

    score = min(10, visa_score + location_bonus)

    # Floor: being in Europe at all has default value
    if score == 0:
        score = 2
        notes.append("+2:eu_default")

    # Company profile floor: known international-friendly companies raise the floor
    if company_profile is not None and not company_profile.is_fallback:
        company_intl = round(
            (company_profile.visa_friendliness_score + company_profile.international_student_score) / 2
        )
        if company_intl > score:
            score = min(10, company_intl)
            notes.append(f"+company_intl={company_intl}")

    return score, ",".join(notes) or "eu_default"


def _score_pivot_bonus(title: str, description: str, role_category: str) -> tuple[int, str]:
    jd_excerpt = description[:1500]
    score = 0
    notes: list[str] = []

    if _PIVOT_AI_PRODUCT_TITLE.search(title):
        score = max(score, 5)
        notes.append("+5:ai_product_title")
    elif _PIVOT_AI_PRODUCT_JD.search(jd_excerpt):
        score = max(score, 3)
        notes.append("+3:ai_product_jd")

    if _PIVOT_TECH_STRATEGY_TITLE.search(title):
        score = max(score, 4)
        notes.append("+4:tech_strat_title")
    elif _PIVOT_TECH_STRATEGY_JD.search(jd_excerpt):
        score = max(score, 2)
        notes.append("+2:tech_strat_jd")

    if _PIVOT_MBA_BRIDGE.search(jd_excerpt):
        score = max(score, 3)
        notes.append("+3:mba_bridge")

    # Baseline for primary track — ensures it always gets at least +2
    if role_category in ("ai_strategy", "product_management", "digital_transformation"):
        score = max(score, 2)
        if not any("+2:primary" in n for n in notes):
            notes.append("+2:primary_track")

    score = min(5, score)
    return score, ",".join(notes) or "none"


# ── Main scoring entry point ───────────────────────────────────────────────────

def score_job(job: dict) -> ScoreResult:
    """
    Score a single job record.

    job must contain: title, company, location, description, role_category.
    All fields tolerate None — defaults to empty string / "unknown".
    """
    from core.company_eligibility import lookup_company

    title       = (job.get("title")        or "").strip()
    company     = (job.get("company")      or "").strip()
    location    = (job.get("location")     or "").strip()
    description = (job.get("description") or "").strip()
    role_cat    = (job.get("role_category") or "unknown").strip()

    company_profile = lookup_company(company)

    track_score,   track_note  = _score_track_alignment(role_cat)
    skill_score,   skill_detail, raw_sim, skill_note = _score_skill_match(
        title, description, job_id=job.get("id")
    )
    mba_score,     mba_note    = _score_mba_relevance(title, description, company)
    company_score, co_note     = _score_company_quality(company, company_profile)
    intl_score,    intl_note   = _score_intl_friendliness(description, location, company_profile)
    pivot_score,   pivot_note  = _score_pivot_bonus(title, description, role_cat)

    total = min(100, max(0,
        track_score + skill_score + mba_score + company_score + intl_score + pivot_score
    ))
    bucket = assign_priority_bucket(total)

    return ScoreResult(
        total_score             = total,
        track_alignment_score   = track_score,
        skill_match_score       = skill_score,
        mba_relevance_score     = mba_score,
        company_quality_score   = company_score,
        intl_friendliness_score = intl_score,
        pivot_bonus_score       = pivot_score,
        priority_bucket         = bucket,
        skill_breakdown         = {},
        matched_categories      = skill_detail.get("matched_categories", {}),
        scoring_notes           = [track_note, skill_note, mba_note, co_note, intl_note, pivot_note],
        semantic_similarity     = raw_sim,
    )


def assign_priority_bucket(total_score: int) -> str:
    return next(label for threshold, label in PRIORITY_THRESHOLDS if total_score >= threshold)


def clear_caches() -> None:
    global _company_tier_cache, _watchlist_cache
    _company_tier_cache = None
    _watchlist_cache = None
