"""
Tests for the Phase 1.5 Eligibility Gate (core/eligibility.py).

Covers:
  - Language gate hard-reject patterns (all user-specified examples)
  - Language gate pass-through signals
  - Visa gate hard-reject patterns
  - Soft-negator logic (C1 Italian is a plus → PASS)
  - Combined eligibility result
  - Edge cases (empty description, mixed signals)

Run with:  python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.eligibility import (
    EligibilityResult,
    check_eligibility,
    check_language_gate,
    check_visa_gate,
)


# helper -----------------------------------------------------------------------

def _lang_fail(desc: str) -> bool:
    ok, _ = check_language_gate(desc)
    return not ok

def _lang_pass(desc: str) -> bool:
    ok, _ = check_language_gate(desc)
    return ok

def _visa_fail(desc: str) -> bool:
    ok, _ = check_visa_gate(desc)
    return not ok

def _visa_pass(desc: str) -> bool:
    ok, _ = check_visa_gate(desc)
    return ok

def _lang_reason(desc: str) -> str:
    _, reason = check_language_gate(desc)
    return reason

def _visa_reason(desc: str) -> str:
    _, reason = check_visa_gate(desc)
    return reason


# ── Language gate: hard-reject examples from the user spec ────────────────────

class TestLanguageGateHardReject(unittest.TestCase):

    def test_italian_required(self):
        self.assertTrue(_lang_fail("Italian required for this role."))

    def test_fluent_italian_required(self):
        self.assertTrue(_lang_fail("Fluent Italian required to communicate with local clients."))

    def test_native_italian(self):
        self.assertTrue(_lang_fail("We are looking for a native Italian speaker."))

    def test_german_required(self):
        self.assertTrue(_lang_fail("German required. All meetings are held in German."))

    def test_fluent_german(self):
        self.assertTrue(_lang_fail("Fluent German is essential for this position."))

    def test_native_german(self):
        # "preferred" softens "Native German" → PASS; use mandatory to hard-reject
        self.assertTrue(_lang_fail("Native German speaker is mandatory."))

    def test_french_required(self):
        self.assertTrue(_lang_fail("French required — the team operates in French."))

    def test_fluent_french(self):
        self.assertTrue(_lang_fail("Fluent French is mandatory."))

    def test_native_french(self):
        self.assertTrue(_lang_fail("We need a native French speaker."))

    def test_dutch_required(self):
        self.assertTrue(_lang_fail("Dutch required for daily operations."))

    def test_spanish_required(self):
        self.assertTrue(_lang_fail("Spanish required for client-facing work."))

    def test_portuguese_required(self):
        self.assertTrue(_lang_fail("Portuguese required. Team is based in Lisbon."))

    def test_local_language_mandatory(self):
        self.assertTrue(_lang_fail("Local language mandatory. No exceptions."))

    def test_mother_tongue(self):
        self.assertTrue(_lang_fail("Mother tongue level Italian is required."))

    def test_native_speaker(self):
        self.assertTrue(_lang_fail("Native speaker required for this customer-facing role."))

    def test_c1_italian(self):
        self.assertTrue(_lang_fail("C1 Italian is required. The team works in Italian."))

    def test_c2_italian(self):
        self.assertTrue(_lang_fail("You must have C2 Italian proficiency."))

    def test_italian_c1(self):
        self.assertTrue(_lang_fail("Italian C1 minimum required."))

    def test_c1_german(self):
        self.assertTrue(_lang_fail("C1 German required for stakeholder communication."))

    def test_c2_german(self):
        self.assertTrue(_lang_fail("C2 German is mandatory for this role."))

    def test_c1_french(self):
        self.assertTrue(_lang_fail("C1 French or higher is required."))

    def test_c2_french(self):
        self.assertTrue(_lang_fail("C2 French required — all documents are in French."))

    def test_b2_italian(self):
        self.assertTrue(_lang_fail("B2 Italian required to interact with local offices."))

    def test_italian_b2(self):
        self.assertTrue(_lang_fail("Italian B2 level is mandatory."))

    def test_madrelingua_italiana(self):
        self.assertTrue(_lang_fail("Ricerchiamo candidati madrelingua italiana."))

    def test_muttersprache_deutsch(self):
        self.assertTrue(_lang_fail("Voraussetzung: Muttersprache Deutsch."))

    def test_local_language_required(self):
        self.assertTrue(_lang_fail("Local language is required. No exceptions."))

    def test_fluent_in_dutch(self):
        self.assertTrue(_lang_fail("Must be fluent in Dutch for daily collaboration."))

    def test_german_language_prerequisite(self):
        self.assertTrue(_lang_fail("German language is a prerequisite for this role."))

    def test_italian_speakers_only(self):
        self.assertTrue(_lang_fail("Italian speakers only need apply."))


# ── Language gate: pass-through signals from the user spec ────────────────────

class TestLanguageGatePass(unittest.TestCase):

    def test_english_required(self):
        self.assertTrue(_lang_pass("English required. All meetings are held in English."))

    def test_english_only(self):
        self.assertTrue(_lang_pass("English only environment. No other language needed."))

    def test_english_speaking_environment(self):
        self.assertTrue(_lang_pass("We are an English speaking environment."))

    def test_local_language_is_a_plus(self):
        self.assertTrue(_lang_pass("Local language is a plus but not required."))

    def test_local_language_preferred(self):
        self.assertTrue(_lang_pass("Local language preferred but not mandatory."))

    def test_multilingual_environment(self):
        self.assertTrue(_lang_pass("We work in a multilingual environment."))

    def test_international_team(self):
        self.assertTrue(_lang_pass("Join our international team based in London."))

    def test_italian_is_a_plus(self):
        self.assertTrue(_lang_pass("Italian is a plus but all work is done in English."))

    def test_german_preferred_not_required(self):
        self.assertTrue(_lang_pass("German preferred but not required for this role."))

    def test_native_english_speaker(self):
        self.assertTrue(_lang_pass(
            "We are looking for a native English speaker who can lead global projects."
        ))

    def test_native_speaker_of_english(self):
        self.assertTrue(_lang_pass("Native speaker of English required."))

    def test_proficiency_in_english_required(self):
        self.assertTrue(_lang_pass("Proficiency in English required. Additional languages a bonus."))

    def test_empty_description(self):
        self.assertTrue(_lang_pass(""))

    def test_short_description(self):
        self.assertTrue(_lang_pass("Product intern."))


# ── Language gate: soft-negator behaviour ────────────────────────────────────

class TestLanguageSoftNegators(unittest.TestCase):

    def test_c1_italian_is_a_plus(self):
        # Soft negator immediately after — should PASS
        self.assertTrue(_lang_pass("C1 Italian is a plus but not required."))

    def test_german_c1_would_be_a_plus(self):
        self.assertTrue(_lang_pass("German C1 would be a plus."))

    def test_french_preferred(self):
        # "French required" + softened by "preferred" following — different wording
        # Actually "French preferred" has no "required" — should PASS regardless
        self.assertTrue(_lang_pass("French preferred for client interaction."))

    def test_italian_nice_to_have(self):
        self.assertTrue(_lang_pass(
            "Italian B2 — nice to have but the role is 100% in English."
        ))

    def test_hard_reject_not_softened_by_distant_plus(self):
        # "Italian required" in sentence 1, unrelated "plus" many chars later
        desc = (
            "Italian required for this role. "
            "We offer competitive salary. "
            "International experience would be a plus."
        )
        self.assertTrue(_lang_fail(desc))

    def test_c1_italian_not_required(self):
        self.assertTrue(_lang_pass("C1 Italian — not required but would help for some meetings."))


# ── Visa gate: hard-reject patterns ───────────────────────────────────────────

class TestVisaGateHardReject(unittest.TestCase):

    def test_eu_citizenship_required(self):
        self.assertTrue(_visa_fail("EU citizenship required for this role."))

    def test_eu_passport_required(self):
        self.assertTrue(_visa_fail("EU passport required. Cannot sponsor visas."))

    def test_must_have_work_authorization(self):
        self.assertTrue(_visa_fail(
            "Candidates must already have work authorization to be eligible."
        ))

    def test_existing_work_authorization_required(self):
        self.assertTrue(_visa_fail("Existing work authorization required. We do not sponsor."))

    def test_work_authorization_required(self):
        self.assertTrue(_visa_fail("Work authorization required to work in Switzerland."))

    def test_right_to_work_required(self):
        self.assertTrue(_visa_fail("Right to work required. Visa sponsorship not available."))

    def test_must_have_right_to_work(self):
        self.assertTrue(_visa_fail("You must have the right to work in Germany."))

    def test_cannot_sponsor_visa(self):
        self.assertTrue(_visa_fail("We cannot sponsor visa for this position."))

    def test_no_visa_sponsorship(self):
        self.assertTrue(_visa_fail("No visa sponsorship. Candidates must have EU work rights."))

    def test_visa_sponsorship_not_available(self):
        self.assertTrue(_visa_fail("Visa sponsorship not available for this role."))

    def test_sponsorship_unavailable(self):
        self.assertTrue(_visa_fail("Sponsorship unavailable. Apply only if you have EU work rights."))

    def test_work_permit_required(self):
        self.assertTrue(_visa_fail(
            "Work permit required. Only applicants with valid work permits will be considered."
        ))

    def test_swiss_work_authorization_required(self):
        self.assertTrue(_visa_fail("Swiss work authorization required for this position."))

    def test_will_not_sponsor(self):
        self.assertTrue(_visa_fail("We will not sponsor any work visas for this role."))

    def test_do_not_sponsor(self):
        self.assertTrue(_visa_fail("We do not sponsor visa applications."))

    def test_eu_residents_only(self):
        self.assertTrue(_visa_fail("EU residents only. Must have right to work in the EU."))

    def test_open_to_eu_citizens_only(self):
        self.assertTrue(_visa_fail("Open to EU citizens only."))

    def test_must_be_eu_citizen(self):
        self.assertTrue(_visa_fail("Applicants must be an EU citizen or hold permanent residence."))

    def test_candidates_must_have_authorization(self):
        self.assertTrue(_visa_fail(
            "Candidates must have existing authorization to work in the Netherlands."
        ))


# ── Visa gate: pass-through (positive signals, no rejection) ──────────────────

class TestVisaGatePass(unittest.TestCase):

    def test_visa_sponsorship_available(self):
        self.assertTrue(_visa_pass("We offer visa sponsorship for qualified candidates."))

    def test_relocation_support(self):
        self.assertTrue(_visa_pass(
            "We provide relocation support and visa assistance for international hires."
        ))

    def test_work_permit_support_provided(self):
        self.assertTrue(_visa_pass("Work permit support provided for successful candidates."))

    def test_international_team(self):
        self.assertTrue(_visa_pass("We are an international team welcoming candidates worldwide."))

    def test_global_company_no_visa_language(self):
        self.assertTrue(_visa_pass(
            "Join our fast-growing team. We welcome applications from all nationalities."
        ))

    def test_empty_description(self):
        self.assertTrue(_visa_pass(""))

    def test_eu_blue_card_available(self):
        # Positive mention of EU Blue Card (company offering it, not requiring pre-existing)
        self.assertTrue(_visa_pass("We can support EU Blue Card applications for qualified candidates."))

    def test_mentions_right_to_work_positively(self):
        # "sponsoring right to work" — company offering it
        self.assertTrue(_visa_pass(
            "We are experienced in sponsoring right to work for talented international candidates."
        ))


# ── Combined eligibility ──────────────────────────────────────────────────────

class TestCombinedEligibility(unittest.TestCase):

    def test_clean_jd_is_eligible(self):
        result = check_eligibility(
            "Join our AI Product team in London. English required. "
            "Visa sponsorship available. International team."
        )
        self.assertTrue(result.eligible)
        self.assertEqual(result.language_gate, "PASS")
        self.assertEqual(result.visa_gate, "PASS")
        self.assertEqual(result.eligibility_status, "ELIGIBLE")

    def test_language_fail_marks_rejected(self):
        result = check_eligibility("C1 Italian required. Great AI strategy team.")
        self.assertFalse(result.eligible)
        self.assertEqual(result.language_gate, "FAIL")
        self.assertEqual(result.eligibility_status, "REJECTED")
        # Some language pattern fires; exact label depends on pattern order
        self.assertNotEqual(result.language_rejection_reason, "")
        self.assertIn("Italian", result.language_rejection_reason)

    def test_visa_fail_marks_rejected(self):
        result = check_eligibility(
            "Great internship opportunity. No visa sponsorship available."
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.visa_gate, "FAIL")
        self.assertEqual(result.eligibility_status, "REJECTED")

    def test_both_gates_fail_both_recorded(self):
        result = check_eligibility(
            "C1 German required. No visa sponsorship. EU citizenship required."
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.language_gate, "FAIL")
        self.assertEqual(result.visa_gate, "FAIL")
        self.assertNotEqual(result.language_rejection_reason, "")
        self.assertNotEqual(result.visa_rejection_reason, "")

    def test_rejection_reason_property_returns_language_first(self):
        # Use a form that fires native_language, not language_required first
        result = check_eligibility(
            "We require a native Italian speaker. No visa sponsorship."
        )
        self.assertIn("native", result.rejection_reason.lower())

    def test_eligible_property_true_when_both_pass(self):
        result = EligibilityResult(
            language_gate="PASS",
            language_rejection_reason="",
            visa_gate="PASS",
            visa_rejection_reason="",
            eligibility_status="ELIGIBLE",
        )
        self.assertTrue(result.eligible)

    def test_eligible_property_false_when_rejected(self):
        result = EligibilityResult(
            language_gate="FAIL",
            language_rejection_reason="c1c2_language: Italian C1",
            visa_gate="PASS",
            visa_rejection_reason="",
            eligibility_status="REJECTED",
        )
        self.assertFalse(result.eligible)


# ── Rejection reason content ──────────────────────────────────────────────────

class TestRejectionReasonContent(unittest.TestCase):

    def test_language_reason_contains_label(self):
        # "C1 Italian required" — language_required pattern fires first (Italian required)
        reason = _lang_reason("C1 Italian required for the role.")
        self.assertNotEqual(reason, "")
        self.assertIn("Italian", reason)

    def test_language_reason_contains_c1c2_label(self):
        # Use a phrase with no "required" so only c1c2 pattern fires
        reason = _lang_reason("You must have C1 Italian to communicate with local offices.")
        self.assertIn("c1c2_language", reason)

    def test_language_reason_contains_snippet(self):
        # Use a phrase where only fluent_language can fire (no required/essential)
        reason = _lang_reason("Candidate must be fluent Italian.")
        self.assertIn("fluent", reason.lower())

    def test_visa_reason_contains_label(self):
        reason = _visa_reason("No visa sponsorship available.")
        self.assertIn("no_visa_sponsorship", reason)

    def test_visa_reason_contains_snippet(self):
        reason = _visa_reason("EU citizenship required.")
        self.assertIn("EU citizenship required", reason)

    def test_pass_returns_empty_reason(self):
        _, reason = check_language_gate("English only environment.")
        self.assertEqual(reason, "")

    def test_visa_pass_returns_empty_reason(self):
        _, reason = check_visa_gate("We welcome international applicants.")
        self.assertEqual(reason, "")


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases(unittest.TestCase):

    def test_none_like_empty_string(self):
        self.assertTrue(_lang_pass(""))
        self.assertTrue(_visa_pass(""))

    def test_very_short_text(self):
        self.assertTrue(_lang_pass("Intern."))
        self.assertTrue(_visa_pass("Intern."))

    def test_case_insensitive_language(self):
        self.assertTrue(_lang_fail("ITALIAN REQUIRED."))
        self.assertTrue(_lang_fail("fluent ITALIAN is essential."))

    def test_case_insensitive_visa(self):
        self.assertTrue(_visa_fail("NO VISA SPONSORSHIP."))
        self.assertTrue(_visa_fail("EU CITIZENSHIP REQUIRED."))

    def test_mixed_signals_language_fails(self):
        # Has both English environment AND a hard language requirement
        desc = (
            "We are an English-speaking international team. "
            "However, Italian C1 is required to liaise with our Milan office."
        )
        self.assertTrue(_lang_fail(desc))

    def test_language_plus_visa_both_fail(self):
        desc = "Fluent German required. Work authorization required. No sponsorship."
        lang_ok, _ = check_language_gate(desc)
        visa_ok, _ = check_visa_gate(desc)
        self.assertFalse(lang_ok)
        self.assertFalse(visa_ok)

    def test_jd_written_in_italian_alone_does_not_trigger_gate(self):
        # The JD being IN Italian is handled elsewhere; gate checks requirements in text
        # A JD in Italian might not explicitly say "Italian required"
        desc = "Stiamo cercando un Product Manager Intern per il nostro team."
        # No explicit "required" → gate should PASS (language detection is a separate check)
        lang_ok, reason = check_language_gate(desc)
        self.assertTrue(lang_ok)  # No "required/mandatory/C1/C2" etc. in this text

    def test_work_permit_support_not_rejected(self):
        # "work permit" in a positive context (company providing it)
        self.assertTrue(_visa_pass(
            "We provide full relocation support including work permit assistance."
        ))

    def test_right_to_work_sponsorship_not_rejected(self):
        self.assertTrue(_visa_pass(
            "We are happy to sponsor right to work applications for top candidates."
        ))

    def test_apostrophe_variants_in_cannot(self):
        self.assertTrue(_visa_fail("We can't sponsor visas for this role."))
        self.assertTrue(_visa_fail("We won't sponsor any work visas."))

    def test_multiple_languages_one_required(self):
        # German optional, but Italian required
        desc = "German is a plus. Italian required for client-facing work."
        self.assertTrue(_lang_fail(desc))

    def test_multiple_languages_none_required(self):
        desc = "German is a plus. Italian is a plus. English is our working language."
        self.assertTrue(_lang_pass(desc))


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestLanguageGateHardReject,
        TestLanguageGatePass,
        TestLanguageSoftNegators,
        TestVisaGateHardReject,
        TestVisaGatePass,
        TestCombinedEligibility,
        TestRejectionReasonContent,
        TestEdgeCases,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
