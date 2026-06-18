"""Resume Intelligence Platform (v5.5).

Turns a resume (plain text, PDF or DOCX) into a normalized, confidence-scored
career profile.

  ResumeParser           — source → raw text + detected language (PDF/DOCX behind
                           optional libs; plain text always works offline)
  SkillsExtractor        — hard / soft / transferable skill detection
  ExperienceExtractor    — roles, employers, date ranges, normalized tenure
  EducationExtractor      — degrees + institutions
  CertificationExtractor — certifications / licenses
  ResumeNormalizer       — merge extractors → normalized profile, fill/flag
                           missing data, compute an overall confidence score,
                           optionally persist to ``career_profiles``

Everything here is deterministic and dependency-free on the default path; PDF /
DOCX text extraction degrades gracefully when the optional libraries are absent.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field

from core import database, tenancy
from core.career_graph import slugify
from core.language_detector import detect_jd_language

# ── Vocabularies (seed; extend via SkillsExtractor(vocab=...)) ──────────────────

HARD_SKILLS = {
    "python", "sql", "excel", "tableau", "power bi", "machine learning",
    "data analysis", "product management", "agile", "scrum", "java", "aws",
    "financial modeling", "a/b testing", "roadmapping", "stakeholder management",
}
SOFT_SKILLS = {
    "leadership", "communication", "teamwork", "problem solving",
    "negotiation", "presentation", "collaboration", "adaptability",
}
TRANSFERABLE_SKILLS = {
    "project management", "analytical thinking", "strategy", "research",
    "mentoring", "budgeting",
}

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
     "nov", "dec"], start=1)}
_DATE_RANGE = re.compile(
    r"(?P<m1>[A-Za-z]{3,9})?\s*(?P<y1>(?:19|20)\d{2})\s*[-–—to]+\s*"
    r"(?:(?P<present>present|current|now)|(?P<m2>[A-Za-z]{3,9})?\s*(?P<y2>(?:19|20)\d{2}))",
    re.IGNORECASE)
_DEGREE = re.compile(
    r"\b(ph\.?d|doctorate|m\.?b\.?a|m\.?sc|master(?:'s)?|b\.?sc|bachelor(?:'s)?|"
    r"b\.?a|b\.?eng|diploma)\b", re.IGNORECASE)
_CERT_HINTS = ("certified", "certificate", "certification", "license", "licensed",
               "pmp", "cfa", "scrum master", "aws certified")


# ── Parsing ─────────────────────────────────────────────────────────────────────

@dataclass
class ParsedResume:
    raw_text: str
    language: str = "Unknown"
    language_code: str = "unknown"
    source_format: str = "text"
    confidence: float = 0.0


class ResumeParser:
    """Source → text. Plain text/`.txt` always works; PDF/DOCX use optional libs."""

    def parse(self, source: str, *, source_format: str | None = None) -> ParsedResume:
        fmt = source_format or self._sniff(source)
        if fmt == "pdf":
            text = self._read_pdf(source)
        elif fmt == "docx":
            text = self._read_docx(source)
        elif fmt == "file":
            with open(source, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
            fmt = "text"
        else:
            text = source
            fmt = "text"
        return self.parse_text(text, source_format=fmt)

    def parse_text(self, text: str, *, source_format: str = "text") -> ParsedResume:
        lang = detect_jd_language(text or "")
        # Confidence: longer + language-identified text parses more reliably.
        length_conf = min(1.0, len((text or "").split()) / 120.0)
        lang_conf = 1.0 if lang.detected_language not in ("Unknown", "Other") else 0.5
        return ParsedResume(
            raw_text=text or "", language=lang.detected_language,
            language_code=lang.raw_language_code, source_format=source_format,
            confidence=round(0.5 * length_conf + 0.5 * lang_conf, 4))

    @staticmethod
    def _sniff(source: str) -> str:
        low = source.lower()
        if low.endswith(".pdf"):
            return "pdf"
        if low.endswith(".docx"):
            return "docx"
        if low.endswith(".txt"):
            return "file"
        return "text"

    @staticmethod
    def _read_pdf(path: str) -> str:  # pragma: no cover - exercised only with pypdf installed
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    @staticmethod
    def _read_docx(path: str) -> str:  # pragma: no cover - exercised only with python-docx installed
        import docx
        document = docx.Document(path)
        return "\n".join(p.text for p in document.paragraphs)


# ── Entity extractors ────────────────────────────────────────────────────────────

@dataclass
class Extraction:
    value: str
    confidence: float = 0.0
    meta: dict = field(default_factory=dict)


class SkillsExtractor:
    def __init__(self, *, hard=None, soft=None, transferable=None):
        self.hard = {s.lower() for s in (hard or HARD_SKILLS)}
        self.soft = {s.lower() for s in (soft or SOFT_SKILLS)}
        self.transferable = {s.lower() for s in (transferable or TRANSFERABLE_SKILLS)}

    def extract(self, text: str) -> list[Extraction]:
        low = (text or "").lower()
        found: list[Extraction] = []
        for category, vocab in (("hard", self.hard), ("soft", self.soft),
                                ("transferable", self.transferable)):
            for term in vocab:
                hits = low.count(term)
                if hits:
                    # repeated mentions → higher confidence (capped)
                    found.append(Extraction(term, round(min(1.0, 0.6 + 0.2 * hits), 4),
                                            {"category": category, "mentions": hits}))
        found.sort(key=lambda e: (-e.confidence, e.value))
        return found


class ExperienceExtractor:
    def extract(self, text: str) -> list[Extraction]:
        out: list[Extraction] = []
        for m in _DATE_RANGE.finditer(text or ""):
            y1 = int(m.group("y1"))
            mo1 = _MONTHS.get((m.group("m1") or "")[:3].lower(), 1)
            if m.group("present"):
                # open-ended; caller can clamp to "now" — use a stable horizon.
                y2, mo2 = y1, mo1  # placeholder, refined by normalize() against present
                present = True
            else:
                y2 = int(m.group("y2"))
                mo2 = _MONTHS.get((m.group("m2") or "")[:3].lower(), 12)
                present = False
            months = max(0, (y2 - y1) * 12 + (mo2 - mo1)) if not present else 0
            out.append(Extraction(m.group(0).strip(), 0.8,
                                  {"start_year": y1, "end_year": None if present else y2,
                                   "months": months, "present": present}))
        return out

    def total_experience_months(self, extractions: list[Extraction], *,
                                present_year: int = 2026) -> int:
        total = 0
        for e in extractions:
            if e.meta.get("present"):
                total += max(0, (present_year - e.meta["start_year"]) * 12)
            else:
                total += e.meta.get("months", 0)
        return total


class EducationExtractor:
    def extract(self, text: str) -> list[Extraction]:
        out: list[Extraction] = []
        for line in (text or "").splitlines():
            m = _DEGREE.search(line)
            if m:
                out.append(Extraction(m.group(0), 0.75, {"line": line.strip()}))
        return out


class CertificationExtractor:
    def extract(self, text: str) -> list[Extraction]:
        out: list[Extraction] = []
        for line in (text or "").splitlines():
            low = line.lower()
            if any(h in low for h in _CERT_HINTS):
                out.append(Extraction(line.strip(), 0.7))
        return out


# ── Normalizer ───────────────────────────────────────────────────────────────────

@dataclass
class CareerProfile:
    profile_id: str
    full_name: str
    language: str
    skills: list[dict]
    experience_months: int
    education: list[str]
    certifications: list[str]
    confidence: float
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id, "full_name": self.full_name,
            "language": self.language, "skills": self.skills,
            "experience_months": self.experience_months, "education": self.education,
            "certifications": self.certifications, "confidence": self.confidence,
            "missing": self.missing,
        }


class ResumeNormalizer:
    def __init__(self):
        self.parser = ResumeParser()
        self.skills = SkillsExtractor()
        self.experience = ExperienceExtractor()
        self.education = EducationExtractor()
        self.certs = CertificationExtractor()

    def normalize(self, source: str, *, full_name: str = "", source_format=None,
                  present_year: int = 2026) -> CareerProfile:
        parsed = self.parser.parse(source, source_format=source_format)
        text = parsed.raw_text
        skill_ex = self.skills.extract(text)
        exp_ex = self.experience.extract(text)
        edu_ex = self.education.extract(text)
        cert_ex = self.certs.extract(text)

        skills = [{"skill": slugify(e.value), "name": e.value,
                   "category": e.meta.get("category", "hard"), "confidence": e.confidence}
                  for e in skill_ex]
        exp_months = self.experience.total_experience_months(exp_ex, present_year=present_year)

        missing = []
        if not skills:
            missing.append("skills")
        if exp_months == 0:
            missing.append("experience")
        if not edu_ex:
            missing.append("education")

        # Confidence blends parse quality with completeness of extracted sections.
        present_sections = sum(bool(x) for x in (skills, exp_ex, edu_ex, cert_ex))
        completeness = present_sections / 4.0
        confidence = round(0.5 * parsed.confidence + 0.5 * completeness, 4)

        return CareerProfile(
            profile_id=uuid.uuid4().hex, full_name=full_name, language=parsed.language,
            skills=skills, experience_months=exp_months,
            education=[e.value for e in edu_ex],
            certifications=[c.value for c in cert_ex],
            confidence=confidence, missing=missing)

    def save(self, profile: CareerProfile, *, user_id: str = "",
             tenant_id: str | None = None) -> str:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO career_profiles "
                "(profile_id, tenant_id, user_id, full_name, data, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (profile.profile_id, tenant_id, user_id, profile.full_name,
                 json.dumps(profile.to_dict()), profile.confidence))
        return profile.profile_id

    def load(self, profile_id: str, *, tenant_id: str | None = None) -> dict | None:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT data FROM career_profiles WHERE profile_id = ? AND tenant_id = ?",
                (profile_id, tenant_id)).fetchone()
        return json.loads(row["data"]) if row else None
