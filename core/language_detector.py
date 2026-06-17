"""
Language detection for job description text (Phase 1.5 extension).

Detects whether a JD is written in a non-English language, which signals
high accessibility risk for English-speaking international MBA candidates.

Design:
  - NON_ENGLISH_AUTO_REJECT = False (default): non-English JDs are flagged
    with language_risk='HIGH' and eligibility_review_required=True, but are
    still scored. A human reviewer decides whether to apply.
  - NON_ENGLISH_AUTO_REJECT = True (future): scoring agent skips these JDs
    entirely and sets eligibility_status='REJECTED'.

Uses langdetect (google/language-detection port) with DetectorFactory.seed=0
for fully deterministic output. Only the first LANG_DETECT_SAMPLE_CHARS
characters are fed to the detector — sufficient for language identification
and keeps processing fast.

Graceful degradation: very short descriptions (<LANG_DETECT_MIN_CHARS) and
any LangDetectException return language='Unknown' with LOW risk.
"""
from dataclasses import dataclass

from langdetect import detect, LangDetectException
from langdetect.detector_factory import DetectorFactory

from core import config

# Pin seed before any detection call so results are reproducible across runs
DetectorFactory.seed = 0

# Canonical display names for the target European + major languages
_LANG_CANONICAL: dict[str, str] = {
    "en": "English",
    "it": "Italian",
    "de": "German",
    "fr": "French",
    "nl": "Dutch",
    "es": "Spanish",
    "pt": "Portuguese",
}

# Languages that map to canonical "Other" for risk assessment purposes
# (any ISO 639-1 code NOT in _LANG_CANONICAL → HIGH risk)


@dataclass
class LanguageDetectionResult:
    detected_language:           str   # "English"|"Italian"|"German"|"French"|"Dutch"|"Spanish"|"Portuguese"|"Other"|"Unknown"
    raw_language_code:           str   # ISO 639-1 from langdetect, or "unknown" on failure
    language_risk:               str   # "LOW" | "HIGH"
    eligibility_review_required: bool  # True when HIGH risk and auto-reject is disabled


def detect_jd_language(text: str) -> LanguageDetectionResult:
    """
    Detect the primary language of a job description text.

    Returns "Unknown" / LOW risk for very short text or detection failures.
    """
    cleaned = (text or "").strip()

    if len(cleaned) < config.LANG_DETECT_MIN_CHARS:
        return LanguageDetectionResult(
            detected_language           = "Unknown",
            raw_language_code           = "unknown",
            language_risk               = "LOW",
            eligibility_review_required = False,
        )

    try:
        code      = detect(cleaned[: config.LANG_DETECT_SAMPLE_CHARS])
        canonical = _LANG_CANONICAL.get(code, "Other")
        high_risk = canonical != "English"

        return LanguageDetectionResult(
            detected_language           = canonical,
            raw_language_code           = code,
            language_risk               = "HIGH" if high_risk else "LOW",
            # Human review only needed when NOT auto-rejecting
            eligibility_review_required = high_risk and not config.NON_ENGLISH_AUTO_REJECT,
        )

    except LangDetectException:
        return LanguageDetectionResult(
            detected_language           = "Unknown",
            raw_language_code           = "unknown",
            language_risk               = "LOW",
            eligibility_review_required = False,
        )
