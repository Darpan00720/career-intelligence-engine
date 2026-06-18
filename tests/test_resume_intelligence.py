"""Resume intelligence tests — parsing, extraction, normalization, confidence."""
import os
import tempfile
import unittest

from tests._career_support import CareerDB
from core.resume_intelligence import (
    CertificationExtractor,
    EducationExtractor,
    ExperienceExtractor,
    ResumeNormalizer,
    ResumeParser,
    SkillsExtractor,
)

RESUME = """John Doe
Senior Product Analyst

Experience
Acme Corp — Product Analyst, Jan 2018 - Present
Globex — Data Analyst, Mar 2015 - Dec 2017

Skills: Python, SQL, Tableau, product management, leadership, stakeholder management

Education
MBA, POLITECNICO di Milano
BSc Computer Science

Certifications
AWS Certified Solutions Architect
Certified Scrum Master
"""


class TestParser(unittest.TestCase):
    def test_parse_text_detects_language_and_confidence(self):
        p = ResumeParser().parse_text(RESUME)
        self.assertEqual(p.language, "English")
        self.assertGreater(p.confidence, 0.0)

    def test_parse_txt_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write(RESUME)
            path = fh.name
        try:
            p = ResumeParser().parse(path)
            self.assertEqual(p.source_format, "text")
            self.assertIn("Acme", p.raw_text)
        finally:
            os.unlink(path)

    def test_format_sniffing(self):
        self.assertEqual(ResumeParser._sniff("cv.pdf"), "pdf")
        self.assertEqual(ResumeParser._sniff("cv.docx"), "docx")
        self.assertEqual(ResumeParser._sniff("just text"), "text")


class TestExtractors(unittest.TestCase):
    def test_skills_extraction_categorized(self):
        out = SkillsExtractor().extract(RESUME)
        cats = {e.value: e.meta["category"] for e in out}
        self.assertEqual(cats.get("python"), "hard")
        self.assertEqual(cats.get("leadership"), "soft")
        self.assertTrue(all(0 < e.confidence <= 1.0 for e in out))

    def test_experience_normalization(self):
        ex = ExperienceExtractor()
        spans = ex.extract(RESUME)
        self.assertEqual(len(spans), 2)
        # Mar 2015 - Dec 2017 = 33 months; Jan 2018 - present counted to present_year
        months = ex.total_experience_months(spans, present_year=2026)
        self.assertGreater(months, 33)

    def test_education_and_certifications(self):
        self.assertTrue(EducationExtractor().extract(RESUME))
        certs = CertificationExtractor().extract(RESUME)
        self.assertGreaterEqual(len(certs), 2)


class TestNormalizer(CareerDB):
    def test_normalize_full_resume(self):
        prof = ResumeNormalizer().normalize(RESUME, full_name="John Doe")
        self.assertEqual(prof.full_name, "John Doe")
        self.assertTrue(prof.skills)
        self.assertGreater(prof.experience_months, 0)
        self.assertEqual(prof.missing, [])
        self.assertGreater(prof.confidence, 0.5)

    def test_missing_data_flagged(self):
        prof = ResumeNormalizer().normalize("Just a name, nothing useful here.")
        self.assertIn("skills", prof.missing)
        self.assertIn("experience", prof.missing)
        self.assertLess(prof.confidence, 0.6)

    def test_save_and_load_roundtrip(self):
        norm = ResumeNormalizer()
        prof = norm.normalize(RESUME, full_name="John Doe")
        pid = norm.save(prof, user_id="u1")
        loaded = norm.load(pid)
        self.assertEqual(loaded["full_name"], "John Doe")
        self.assertEqual(loaded["profile_id"], pid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
