"""
Phase 1.5 Eligibility Gate.

Runs AFTER the internship seniority filter and BEFORE the Career Pivot Scorer.
Two sequential gates check the raw job description text for hard rejection signals.

  Language Gate — rejects JDs that explicitly require non-English language proficiency
  Visa Gate     — rejects JDs that explicitly exclude visa-sponsored / non-EU candidates

Soft-negator logic: if a hard-reject phrase is followed (within 80 chars) by a softener
such as "is a plus", "preferred", "not required", "nice to have", the signal is treated
as a preference — not a hard requirement — and does NOT cause rejection.

Usage:
    from core.eligibility import check_eligibility
    result = check_eligibility(description_text)
    if not result.eligible:
        print(result.rejection_reason)
"""
import html
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.company_eligibility import CompanyEligibilityProfile


def _strip_html(text: str) -> str:
    """Drop tags + unescape entities + collapse whitespace, so language/visa
    phrases split across markup (e.g. "Italian</strong>&nbsp;(fluent)") match."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ── Soft-negator logic ─────────────────────────────────────────────────────────
# A soft negator within _SOFT_WINDOW chars AFTER a hard signal downgrades it
# from a mandatory requirement to a preference — no rejection.

_SOFT_WINDOW = 80  # characters forward from the end of the match

_SOFT_NEGATORS = re.compile(
    r"\b("
    r"is\s+a\s+(nice[- ]to[- ]have|bonus|plus)"
    r"|would\s+be\s+(a\s+)?(plus|bonus|advantage|asset)"
    r"|(?<!\w)(a\s+)?(plus|bonus|advantage|asset)(?!\w)"
    r"|preferred\b"
    r"|preferable\b"
    r"|desirable\b"
    r"|advantageous\b"
    r"|nice[- ]to[- ]have\b"
    r"|not\s+(required|mandatory|essential|obligatory|compulsory)\b"
    r"|if\s+possible\b"
    r"|ideally\b"
    r"|optionally\b"
    r"|welcome\b(?!\s+to\s+apply)"     # "welcome to apply" ≠ softener
    r")\b",
    re.IGNORECASE,
)


def _is_softened(text: str, match_end: int) -> bool:
    """True if the match is immediately followed (within _SOFT_WINDOW) by a soft negator."""
    forward = text[match_end: match_end + _SOFT_WINDOW]
    return bool(_SOFT_NEGATORS.search(forward))


# ── Language gate ──────────────────────────────────────────────────────────────

# Non-English target languages (those that create work barriers for English speakers)
_NON_EN = (
    r"(?:italian|italiano"
    r"|german|deutsch|tedesco"
    r"|french|fran[çc]ais|francese"
    r"|dutch|nederlands|flemish|vlaams"
    r"|spanish|espa[nñ]ol|spagnolo|castellano"
    r"|portuguese|portugu[eê]s"
    r"|mandarin|chinese"
    r"|arabic"
    r"|polish|czech|romanian|hungarian"
    r"|swedish|norwegian|danish|finnish"
    r"|russian|turkish|korean|japanese"
    r"|hebrew|greek|catalan"
    r")"
)

# Words that mark a language as explicitly required
_REQ = r"(?:required|mandatory|essential|must(?:\s+be)?|compulsory|obligatory|richiesto|erforderlich|indispensable|indispensabile|obligatoire|imprescindible|necessario)"

# Compiled language hard-reject patterns — (pattern, label) pairs
_LANG_PATTERNS: list[tuple[re.Pattern, str]] = [

    # "Italian required" / "German mandatory" / "French essential"
    (re.compile(
        rf"\b{_NON_EN}\s+(?:language\s+)?(?:is\s+)?{_REQ}\b",
        re.IGNORECASE,
    ), "language_required"),

    # "required: Italian" / "mandatory: German"
    (re.compile(
        rf"\b{_REQ}:\s*{_NON_EN}\b",
        re.IGNORECASE,
    ), "language_required_colon"),

    # "fluent Italian" / "fluent in German" / "fluent French speaker"
    (re.compile(
        rf"\bfluent\s+(?:in\s+)?{_NON_EN}\b",
        re.IGNORECASE,
    ), "fluent_language"),

    # "must speak Italian" / "required to communicate in Dutch"
    (re.compile(
        rf"\b(?:must|need(?:ed)?|required|expected)\s+(?:to\s+)?"
        rf"(?:speak|write|communicate\s+in|work\s+in)\s+{_NON_EN}\b",
        re.IGNORECASE,
    ), "must_use_language"),

    # "Dutch proficiency required" / "proficiency in Italian is mandatory"
    (re.compile(
        rf"\b{_NON_EN}\s+(?:language\s+)?(?:proficiency|fluency|command)\s+(?:is\s+)?{_REQ}\b"
        rf"|\b(?:proficiency|fluency|command)\s+in\s+{_NON_EN}\s+(?:is\s+)?{_REQ}\b",
        re.IGNORECASE,
    ), "language_proficiency_required"),

    # "Dutch-speaking role" / "Italian speaking internship"
    (re.compile(
        rf"\b{_NON_EN}[\s-]+speaking\b",
        re.IGNORECASE,
    ), "language_speaking_role"),

    # "native Italian" / "native German"
    (re.compile(
        rf"\bnative\s+(?:\w+\s+){{0,2}}{_NON_EN}\b",
        re.IGNORECASE,
    ), "native_language"),

    # "Italian native" / "German native speaker"
    (re.compile(
        rf"\b{_NON_EN}\s+(?:\w+\s+){{0,2}}(?:native|madrelingua|muttersprache)\b",
        re.IGNORECASE,
    ), "language_native"),

    # "C1 Italian" / "Italian C1" / "C2 German" / "German C2"
    (re.compile(
        rf"\b[Cc][12]\s+{_NON_EN}\b"
        rf"|\b{_NON_EN}\s+[Cc][12]\b",
        re.IGNORECASE,
    ), "c1c2_language"),

    # "B2 Italian" / "Italian B2" (upper-intermediate = near-fluent barrier)
    (re.compile(
        rf"\b[Bb]2\s+{_NON_EN}\b"
        rf"|\b{_NON_EN}\s+[Bb]2\b",
        re.IGNORECASE,
    ), "b2_language"),

    # "mother tongue" / "mother-tongue"
    (re.compile(
        r"\bmother[\s-]tongue\b",
        re.IGNORECASE,
    ), "mother_tongue"),

    # "native speaker" — but NOT "native English speaker" or "native speaker of English"
    (re.compile(
        r"\bnative\s+speaker\b(?!\s+of\s+(?:the\s+)?english)"
        r"(?<!\benglish\s)",  # also not preceded by "english"
        re.IGNORECASE,
    ), "native_speaker"),

    # "local language is required/mandatory"
    (re.compile(
        r"\blocal\s+language\s+(?:is\s+)?(?:a\s+)?" + _REQ,
        re.IGNORECASE,
    ), "local_language_required"),

    # Language-specific mother-tongue phrases
    (re.compile(
        r"\b(?:madrelingua\s+italiana?|muttersprache\s+deutsch|langue\s+maternelle"
        r"|moedertaal|lengua\s+materna)\b",
        re.IGNORECASE,
    ), "mother_tongue_specific"),

    # "Italian language is a prerequisite/requirement"
    (re.compile(
        rf"\b{_NON_EN}\s+language\s+(?:is\s+)?(?:a\s+)?(?:requirement|prerequisite)\b",
        re.IGNORECASE,
    ), "language_prerequisite"),

    # "only Italian speakers" / "Italian speakers only"
    (re.compile(
        rf"\b(?:only\s+)?{_NON_EN}\s+speakers?\s+only\b"
        rf"|\bonly\s+{_NON_EN}\s+speakers?\b",
        re.IGNORECASE,
    ), "language_speakers_only"),

    # "Italian (fluent)" / "German (native)" / "Dutch (C1)" — language then level
    # in parentheses. Common ATS phrasing (e.g. Doctolib: "Italian (fluent) and
    # English (fluent)"). A nearby softener ("(fluent) is a plus") still downgrades.
    (re.compile(
        rf"\b{_NON_EN}\s*\(\s*(?:fluent|fluency|native|mother[\s-]?tongue|c[12]|b2)\s*\)",
        re.IGNORECASE,
    ), "language_level_parenthetical"),
]


def check_language_gate(description: str) -> tuple[bool, str]:
    """
    Returns (eligible: bool, rejection_reason: str).

    FAIL only when a hard-reject signal is NOT softened by a nearby qualifier.
    Empty description → PASS (can't reject on absence of evidence).
    """
    description = _strip_html(description)
    if not description or len(description.strip()) < 5:
        return True, ""

    for pattern, label in _LANG_PATTERNS:
        for m in pattern.finditer(description):
            if not _is_softened(description, m.end()):
                snippet = m.group().strip()[:60]
                return False, f"{label}: \"{snippet}\""

    return True, ""


# ── Visa / work-authorization gate ─────────────────────────────────────────────

_VISA_PATTERNS: list[tuple[re.Pattern, str]] = [

    # "EU citizenship required" / "EU passport required" / "EU national required"
    (re.compile(
        r"\bEU\s+(?:citizenship|citizen|passport|national(?:ity)?|membership)\s+(?:is\s+)?(?:required|mandatory|essential|must|needed|necessary)\b",
        re.IGNORECASE,
    ), "eu_citizenship_required"),

    # "must be an EU citizen" / "must hold EU citizenship"
    (re.compile(
        r"\bmust\s+(?:be\s+(?:an?\s+)?EU\s+(?:citizen|national)|hold\s+(?:an?\s+)?EU\s+(?:citizenship|passport))\b",
        re.IGNORECASE,
    ), "must_be_eu_citizen"),

    # "must have/already have work authorization/work permit"
    (re.compile(
        r"\bmust\s+(?:already\s+)?(?:have|hold|possess)\s+(?:(?:existing|valid|current)\s+)?(?:work\s+authori[zs]ation|authoris?ation\s+to\s+work|work\s+permit)\b",
        re.IGNORECASE,
    ), "must_have_work_authorization"),

    # "existing work authorization required" / "work authorization required/needed"
    (re.compile(
        r"\b(?:existing\s+)?work\s+authori[zs]ation\s+(?:is\s+)?(?:required|mandatory|essential|must|needed)\b",
        re.IGNORECASE,
    ), "work_authorization_required"),

    # "right to work required/mandatory" / "must have the right to work"
    (re.compile(
        r"\bright\s+to\s+work\s+(?:in\s+\w+(?:\s+\w+)?\s+)?(?:is\s+)?(?:required|mandatory|essential|must|needed)\b"
        r"|\bmust\s+have\s+(?:the\s+)?right\s+to\s+work\b",
        re.IGNORECASE,
    ), "right_to_work_required"),

    # "no visa sponsorship" / "visa sponsorship not available/offered"
    (re.compile(
        r"\bno\s+(?:visa\s+)?sponsorship\b"
        r"|\bvisa\s+sponsorship\s+(?:is\s+)?(?:not\s+(?:available|offered|provided|possible)|unavailable)\b"
        r"|\bsponsorship\s+(?:is\s+)?(?:not\s+available|unavailable|not\s+offered|not\s+provided|not\s+possible)\b",
        re.IGNORECASE,
    ), "no_visa_sponsorship"),

    # "cannot/unable to/will not sponsor" visa/work permit
    (re.compile(
        r"\b(?:cannot|can\s+not|can[''']t|unable\s+to|do\s+not|don[''']t|will\s+not|won[''']t|not\s+able\s+to)\s+"
        r"(?:provide\s+)?(?:visa\s+)?sponsor(?:ship)?\b",
        re.IGNORECASE,
    ), "cannot_sponsor"),

    # "work permit required" / "must have a valid work permit" (stand-alone — no "we provide")
    (re.compile(
        r"\bwork\s+permit\s+(?:is\s+)?(?:required|mandatory|essential|must)\b"
        r"|\bmust\s+(?:already\s+)?have\s+(?:a\s+)?(?:valid\s+)?work\s+permit\b",
        re.IGNORECASE,
    ), "work_permit_required"),

    # "Swiss work authorization required"
    (re.compile(
        r"\bSwiss\s+(?:work\s+)?(?:authoris?ation|permit|residence\s+permit)\s+(?:is\s+)?(?:required|mandatory|must)\b",
        re.IGNORECASE,
    ), "swiss_authorization_required"),

    # "EU residents only" / "open to EU citizens only"
    (re.compile(
        r"\b(?:only\s+)?EU\s+residents?\s+only\b"
        r"|\bopen\s+(?:only\s+)?to\s+EU\s+(?:residents?|citizens?|nationals?)\b"
        r"|\bfor\s+EU\s+(?:residents?|citizens?|nationals?)\s+only\b",
        re.IGNORECASE,
    ), "eu_residents_only"),

    # "candidates must have existing authorization to work in"
    (re.compile(
        r"\bcandidates?\s+must\s+have\s+(?:existing\s+)?(?:authoris?ation|authorization|permission)\s+to\s+work\b",
        re.IGNORECASE,
    ), "candidate_authorization_required"),
]


def check_visa_gate(description: str) -> tuple[bool, str]:
    """
    Returns (eligible: bool, rejection_reason: str).
    Empty description → PASS.
    """
    description = _strip_html(description)
    if not description or len(description.strip()) < 5:
        return True, ""

    for pattern, label in _VISA_PATTERNS:
        for m in pattern.finditer(description):
            if not _is_softened(description, m.end()):
                snippet = m.group().strip()[:60]
                return False, f"{label}: \"{snippet}\""

    return True, ""


# ── Positive accessibility signal patterns ─────────────────────────────────────
# These are NEW patterns not needed by the hard-reject gates.
# They detect positive signals that raise the eligibility score.

# "Working language is English" / "English is the official language"
_LANG_EN_OFFICIAL = re.compile(
    r"\b(?:working|official|business|primary|main|corporate)\s+language\s+(?:is\s+)?english\b"
    r"|\benglish\s+(?:is\s+(?:the\s+)?)?(?:working|official|business|primary|main)\s+(?:\w+\s+)?language\b"
    r"|\ball\s+(?:communication|meetings?|documents?|work)\s+(?:is\s+|are\s+)?(?:conducted\s+)?in\s+english\b"
    r"|\benglish[-\s](?:speaking|only)\s+(?:office|workplace|environment|company|team)\b",
    re.IGNORECASE,
)

# "Fluent English required" / "excellent English" / "English proficiency"
_LANG_EN_STRONG = re.compile(
    r"\benglish\s+(?:is\s+)?(?:required|mandatory|essential)\b"
    r"|\bproficient\s+in\s+english\b"
    r"|\bfluent\s+(?:in\s+)?english\b"
    r"|\bstrong\s+english\s+(?:communication\s+)?(?:skills?|proficiency)\b"
    r"|\bexcellent\s+english\b"
    r"|\badvanced\s+english\b"
    r"|\benglish\s+proficiency\s+(?:is\s+)?(?:required|essential|expected)\b",
    re.IGNORECASE,
)

# Soft non-English language preferences (for scoring only — gate never fires on these).
# These signal a mild language barrier that doesn't hard-reject but penalizes the score.
_LANG_SOFT_PATTERNS: list[tuple[re.Pattern, str]] = [

    # "French is a plus" / "Italian preferred" / "German is a nice-to-have"
    (re.compile(
        rf"\b{_NON_EN}\s+(?:language\s+)?(?:is\s+)?(?:a\s+)?(?:plus|bonus|advantage|asset|preferred|preferable|desirable|nice[- ]to[- ]have|welcome)\b",
        re.IGNORECASE,
    ), "language_preferred"),

    # "knowledge of French" / "knowledge of the local language"
    (re.compile(
        rf"\bknowledge\s+of\s+(?:the\s+)?(?:local\s+language|{_NON_EN})\b",
        re.IGNORECASE,
    ), "knowledge_of_language"),

    # "local language is a plus / preferred"
    (re.compile(
        r"\blocal\s+language\s+(?:is\s+)?(?:a\s+)?(?:plus|bonus|advantage|preferred|desirable|nice[- ]to[- ]have)\b",
        re.IGNORECASE,
    ), "local_language_preferred"),
]


# "Visa sponsorship available" / "we sponsor visa" / "visa support provided"
_VISA_SPONSORED = re.compile(
    r"\bvisa\s+sponsorship\s+(?:is\s+)?(?:available|provided|offered|possible|supported)\b"
    r"|\b(?:we\s+(?:do\s+)?)?(?:provide|offer|support|sponsor|cover)\s+(?:visa(?:s)?|work\s+(?:authoris?ation|permit))\b"
    r"|\bwill\s+(?:fully\s+)?sponsor\s+(?:(?:your\s+)?visa|work\s+authoris?ation)\b"
    r"|\bsponsorship\s+(?:is\s+)?(?:available|provided|offered)\b"
    r"|\bvisa\s+(?:assistance|support|help)\s+(?:is\s+)?(?:available|provided|offered)\b",
    re.IGNORECASE,
)

# "Relocation package/support provided"
_RELOCATION_SUPPORT = re.compile(
    r"\brelocation\s+(?:package|support|assistance|stipend|allowance|benefit|costs?)\b"
    r"|\bwe\s+(?:offer|provide|support|cover)\s+relocation\b"
    r"|\brelocation\s+(?:costs?\s+)?(?:will\s+be\s+)?covered\b",
    re.IGNORECASE,
)

# "International candidates welcome" / "open to candidates worldwide"
_INTL_WELCOME = re.compile(
    r"\binternational\s+(?:candidates?|students?|applicants?)\s+(?:are\s+)?(?:welcome|encouraged|invited|considered)\b"
    r"|\bopen\s+to\s+(?:candidates?\s+(?:from\s+)?)?(?:worldwide|globally|internationally|all\s+(?:countries|backgrounds|nationalities))\b"
    r"|\bwelcome\s+(?:applications?\s+from\s+)?(?:candidates?\s+(?:from\s+)?)?(?:worldwide|globally|internationally)\b",
    re.IGNORECASE,
)

# "EU citizenship preferred" (soft barrier — gate passes, score penalized)
_EU_PREFERRED_SOFT = re.compile(
    r"\bEU\s+(?:citizenship|citizen|passport|national(?:ity)?|work\s+(?:authoris?ation|permit))\s+"
    r"(?:is\s+)?(?:preferred|a\s+plus|an?\s+advantage|desirable|advantageous)\b"
    r"|\bright\s+to\s+work\s+(?:in\s+\w+\s+)?(?:is\s+)?(?:preferred|a\s+plus|advantageous)\b",
    re.IGNORECASE,
)

# "International team" / "diverse team" / "multicultural environment"
_ENV_INTL_TEAM = re.compile(
    r"\binternational\s+(?:team|environment|company|colleagues?|workforce|office|culture)\b"
    r"|\bdiverse\s+(?:team|workforce|environment|colleagues?|group|culture)\b"
    r"|\bmulticultural\s+(?:team|environment|company|workplace)\b"
    r"|\bglobal\s+(?:team|workforce|environment)\b"
    r"|\bcosmopolitan\s+(?:team|workplace|environment)\b"
    r"|\bpeople\s+from\s+\d+\+?\s+(?:countries|nationalities|backgrounds)\b",
    re.IGNORECASE,
)

# "Global company" / "multinational corporation" / "offices in 30 countries"
_INTL_GLOBAL_COMPANY = re.compile(
    r"\bglobal\s+(?:company|organization|corporation|firm|enterprise|business|group)\b"
    r"|\bmultinational\s+(?:company|organization|corporation|firm|group)\b"
    r"|\bpresence\s+in\s+\d+\+?\s+countries\b"
    r"|\boperates?\s+(?:in|across)\s+(?:\d+\+?\s+)?countries\b"
    r"|\bworldwide\s+(?:offices?|operations?|presence|footprint)\b"
    r"|\bFortune\s+(?:500|100|50|Global\s+\d+)\b",
    re.IGNORECASE,
)

# "Expat-friendly" / "highly international team" / "diverse and inclusive"
_INTL_EXPAT = re.compile(
    r"\bexpat(?:riate)?[-\s](?:friendly|community|network|support)\b"
    r"|\bhighly\s+international\s+(?:team|environment|company)\b"
    r"|\bdiverse\s+and\s+inclusive\s+(?:workplace|environment|team|company)\b",
    re.IGNORECASE,
)


# ── Eligibility score components ───────────────────────────────────────────────
#
# Score tiers:
#
#   Language Accessibility (0–40):
#     hard reject → 0 | c1c2/native soft → 18 | b2 soft → 22 | other soft → 26
#     no signals → 30 | English strong → 35 | English official/working → 40
#
#   Visa Accessibility (0–40):
#     hard reject → 0 | EU preferred soft → 18 | no signals → 28
#     intl welcome → 30 | relocation → 35 | explicit sponsorship → 40
#
#   English Environment (0–10):
#     none → 0 | English required/strong → 5 | intl/diverse team → 7
#     working language English → 10
#
#   International Company Signals (0–10):
#     none → 0 | intl environment/team → 5 | expat-friendly → 7
#     global company/multinational → 8 | visa sponsorship (global signal) → 5

_HIGH_BARRIER_SOFT_LABELS = frozenset({
    "c1c2_language", "native_speaker", "native_language",
    "language_native", "mother_tongue", "mother_tongue_specific",
})


def _score_language_accessibility(desc: str, gate_passed: bool) -> int:
    if not gate_passed:
        return 0

    if _LANG_EN_OFFICIAL.search(desc):
        return 40
    if _LANG_EN_STRONG.search(desc):
        return 35

    # Collect all soft (gate-passing) language signals to find the worst barrier
    softened: set[str] = set()
    for pattern, label in _LANG_PATTERNS:
        for m in pattern.finditer(desc):
            if _is_softened(desc, m.end()):
                softened.add(label)

    if _HIGH_BARRIER_SOFT_LABELS & softened:
        return 18   # near-fluent or native level preferred
    if "b2_language" in softened:
        return 22   # upper-intermediate preferred
    if softened:
        return 26   # some other gate-pattern softened

    # Check soft-preference patterns (never trigger rejection — for scoring only)
    for pattern, _ in _LANG_SOFT_PATTERNS:
        if pattern.search(desc):
            return 26  # explicit language preference noted but not required

    return 30       # no language signals — neutral baseline


def _score_visa_accessibility(desc: str, gate_passed: bool) -> int:
    if not gate_passed:
        return 0

    if _VISA_SPONSORED.search(desc):
        return 40
    if _RELOCATION_SUPPORT.search(desc):
        return 35
    if _INTL_WELCOME.search(desc):
        return 30
    if _EU_PREFERRED_SOFT.search(desc):
        return 18   # EU preferred but not required — soft barrier

    return 28       # no visa signals — neutral baseline


def _score_english_environment(desc: str) -> int:
    if _LANG_EN_OFFICIAL.search(desc):
        return 10
    score = 0
    if _ENV_INTL_TEAM.search(desc):
        score = max(score, 7)
    if _LANG_EN_STRONG.search(desc):
        score = max(score, 5)
    return min(score, 10)


def _score_international_signals(desc: str) -> int:
    has_global = bool(_INTL_GLOBAL_COMPANY.search(desc))
    if has_global:
        # Global company (8 pts) + any secondary signal → bonus 2 pts, capped at 10
        secondary = (
            bool(_INTL_EXPAT.search(desc))
            or bool(_ENV_INTL_TEAM.search(desc))
            or bool(_INTL_WELCOME.search(desc))
            or bool(_VISA_SPONSORED.search(desc))
        )
        return 10 if secondary else 8

    score = 0
    if _INTL_EXPAT.search(desc):
        score = max(score, 7)
    if _ENV_INTL_TEAM.search(desc):
        score = max(score, 5)
    if _INTL_WELCOME.search(desc):
        score = max(score, 5)
    if _VISA_SPONSORED.search(desc):
        score = max(score, 5)
    return score


# ── Company profile blending ───────────────────────────────────────────────────
# Applied ONLY for ELIGIBLE jobs when JD text gives no explicit signal.
# JD-derived scores always win when they carry an explicit signal.

_VISA_NEUTRAL = 28   # _score_visa_accessibility return value when JD has no signal


def _blend_visa(jd_score: int, company_visa: int) -> int:
    """Replace neutral visa score with company's known profile (0-10 → 0-40)."""
    if jd_score != _VISA_NEUTRAL:
        return jd_score
    return round(company_visa * 4)


def _blend_env(jd_score: int, company_eng: int) -> int:
    """When JD has no english-environment signal (score=0), use company knowledge."""
    return jd_score if jd_score > 0 else company_eng


def _blend_intl(jd_score: int, company_intl: int) -> int:
    """When JD has no international-signals score (score=0), use company knowledge."""
    return jd_score if jd_score > 0 else company_intl


def compute_eligibility_score(
    description: str,
    lang_gate_passed: bool,
    visa_gate_passed: bool,
    company_profile=None,   # CompanyEligibilityProfile | None
) -> tuple[int, int, int, int]:
    """
    Compute the four accessibility components.
    Returns (language_accessibility, visa_accessibility, english_environment, international_signals).
    All components are 0 when their gate failed.

    When company_profile is provided (and not a fallback), known company reputation
    fills in dimensions where JD text is silent.
    """
    lang_acc = _score_language_accessibility(description, lang_gate_passed)
    visa_acc = _score_visa_accessibility(description, visa_gate_passed)
    eng_env  = _score_english_environment(description)
    intl_sig = _score_international_signals(description)

    if company_profile is not None and not company_profile.is_fallback:
        visa_acc = _blend_visa(visa_acc, company_profile.visa_friendliness_score)
        eng_env  = _blend_env(eng_env,   company_profile.english_environment_score)
        intl_sig = _blend_intl(intl_sig, company_profile.international_student_score)

    return lang_acc, visa_acc, eng_env, intl_sig


# ── Combined result ────────────────────────────────────────────────────────────

@dataclass
class EligibilityResult:
    language_gate:             str   # "PASS" | "FAIL"
    language_rejection_reason: str
    visa_gate:                 str   # "PASS" | "FAIL"
    visa_rejection_reason:     str
    eligibility_status:        str   # "ELIGIBLE" | "REJECTED"
    # Score fields — 0 for any REJECTED job; computed for ELIGIBLE jobs
    eligibility_score:         int = 0   # 0-100 composite
    language_accessibility:    int = 0   # 0-40
    visa_accessibility:        int = 0   # 0-40
    english_environment:       int = 0   # 0-10
    international_signals:     int = 0   # 0-10

    @property
    def eligible(self) -> bool:
        return self.eligibility_status == "ELIGIBLE"

    @property
    def rejection_reason(self) -> str:
        if self.language_gate == "FAIL":
            return self.language_rejection_reason
        if self.visa_gate == "FAIL":
            return self.visa_rejection_reason
        return ""


def check_eligibility(description: str, company: str = "") -> EligibilityResult:
    """
    Run both gates and return a single EligibilityResult.
    Both gates always run so both rejection reasons are captured for analytics.
    ELIGIBLE jobs receive a 0-100 eligibility_score; REJECTED jobs score 0.

    company: optional company name used to look up the Company Eligibility
             Intelligence profile. When provided, company-known reputation
             fills dimensions where JD text is silent.
    """
    lang_ok, lang_reason = check_language_gate(description)
    visa_ok, visa_reason = check_visa_gate(description)
    eligible = lang_ok and visa_ok

    if eligible:
        company_profile = None
        if company:
            from core.company_eligibility import lookup_company
            company_profile = lookup_company(company)
        lang_acc, visa_acc, eng_env, intl_sig = compute_eligibility_score(
            description, lang_ok, visa_ok, company_profile
        )
        total = lang_acc + visa_acc + eng_env + intl_sig
    else:
        lang_acc = visa_acc = eng_env = intl_sig = total = 0

    return EligibilityResult(
        language_gate             = "PASS" if lang_ok else "FAIL",
        language_rejection_reason = lang_reason,
        visa_gate                 = "PASS" if visa_ok else "FAIL",
        visa_rejection_reason     = visa_reason,
        eligibility_status        = "ELIGIBLE" if eligible else "REJECTED",
        eligibility_score         = total,
        language_accessibility    = lang_acc,
        visa_accessibility        = visa_acc,
        english_environment       = eng_env,
        international_signals     = intl_sig,
    )
