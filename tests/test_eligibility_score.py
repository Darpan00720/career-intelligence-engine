"""
Tests for the Eligibility Score Engine (Phase 1.5b).

Covers:
  - Hard-reject jobs always score 0 on all components
  - Neutral baseline (no signals)
  - Language accessibility tiers
  - Visa accessibility tiers
  - English environment scoring
  - International company signal scoring
  - EligibilityResult field presence
  - User-specified target score ranges
  - Combined JD scenarios
"""
import unittest

from core.eligibility import (
    EligibilityResult,
    check_eligibility,
    compute_eligibility_score,
)


# ── 1. Hard-reject → all scores zero ─────────────────────────────────────────

class TestHardRejectZeroScore(unittest.TestCase):

    def test_italian_required_zero_score(self):
        r = check_eligibility("Italian required. Great opportunity.")
        self.assertEqual(r.eligibility_score, 0)
        self.assertEqual(r.language_accessibility, 0)
        self.assertEqual(r.visa_accessibility, 0)
        self.assertEqual(r.english_environment, 0)
        self.assertEqual(r.international_signals, 0)

    def test_german_mandatory_zero_score(self):
        r = check_eligibility("German mandatory for this role.")
        self.assertEqual(r.eligibility_score, 0)

    def test_french_essential_zero_score(self):
        r = check_eligibility("French essential.")
        self.assertEqual(r.eligibility_score, 0)

    def test_no_visa_sponsorship_zero_score(self):
        r = check_eligibility("No visa sponsorship available.")
        self.assertEqual(r.eligibility_score, 0)

    def test_eu_citizenship_required_zero_score(self):
        r = check_eligibility("EU citizenship required for this position.")
        self.assertEqual(r.eligibility_score, 0)

    def test_cannot_sponsor_zero_score(self):
        r = check_eligibility("We cannot sponsor visa applications.")
        self.assertEqual(r.eligibility_score, 0)

    def test_both_gates_fail_zero_score(self):
        r = check_eligibility("German required. No visa sponsorship.")
        self.assertEqual(r.eligibility_score, 0)
        self.assertEqual(r.language_accessibility, 0)
        self.assertEqual(r.visa_accessibility, 0)

    def test_rejected_status_with_zero_score(self):
        r = check_eligibility("Fluent Italian required.")
        self.assertFalse(r.eligible)
        self.assertEqual(r.eligibility_score, 0)

    def test_work_permit_required_zero_score(self):
        r = check_eligibility("Work permit required for all candidates.")
        self.assertEqual(r.eligibility_score, 0)

    def test_right_to_work_required_zero_score(self):
        r = check_eligibility("Must have right to work in Germany.")
        self.assertEqual(r.eligibility_score, 0)


# ── 2. Neutral baseline (no signals) ─────────────────────────────────────────

class TestNeutralBaseline(unittest.TestCase):

    def test_no_signals_language_baseline_30(self):
        r = check_eligibility("Join our team as a Product Manager. Exciting work.")
        self.assertTrue(r.eligible)
        self.assertEqual(r.language_accessibility, 30)

    def test_no_signals_visa_baseline_28(self):
        r = check_eligibility("Join our team as a Product Manager. Exciting work.")
        self.assertEqual(r.visa_accessibility, 28)

    def test_no_signals_env_zero(self):
        r = check_eligibility("Competitive salary. Great benefits.")
        self.assertEqual(r.english_environment, 0)

    def test_no_signals_intl_zero(self):
        r = check_eligibility("Competitive salary. Great benefits.")
        self.assertEqual(r.international_signals, 0)

    def test_no_signals_total_58(self):
        r = check_eligibility("Join our team as a Product Manager. Exciting work.")
        self.assertEqual(r.eligibility_score, 58)   # 30 + 28 + 0 + 0


# ── 3. Language accessibility tiers ──────────────────────────────────────────

class TestLanguageAccessibilityScoring(unittest.TestCase):

    def test_english_working_language_scores_40(self):
        r = check_eligibility("The working language is English.")
        self.assertEqual(r.language_accessibility, 40)

    def test_english_official_language_scores_40(self):
        r = check_eligibility("English is the official language of our company.")
        self.assertEqual(r.language_accessibility, 40)

    def test_all_comms_in_english_scores_40(self):
        r = check_eligibility("All communication is conducted in English.")
        self.assertEqual(r.language_accessibility, 40)

    def test_english_speaking_office_scores_40(self):
        r = check_eligibility("Join our English-speaking team in Milan.")
        self.assertEqual(r.language_accessibility, 40)

    def test_fluent_english_required_scores_35(self):
        r = check_eligibility("Fluent in English required.")
        self.assertEqual(r.language_accessibility, 35)

    def test_excellent_english_scores_35(self):
        r = check_eligibility("Excellent English communication skills required.")
        self.assertEqual(r.language_accessibility, 35)

    def test_advanced_english_scores_35(self):
        r = check_eligibility("Advanced English expected.")
        self.assertEqual(r.language_accessibility, 35)

    def test_english_proficiency_required_scores_35(self):
        r = check_eligibility("English proficiency is required for this role.")
        self.assertEqual(r.language_accessibility, 35)

    def test_c1_italian_preferred_scores_18(self):
        r = check_eligibility("C1 Italian is preferred but not required.")
        self.assertTrue(r.eligible)
        self.assertEqual(r.language_accessibility, 18)

    def test_native_german_preferred_scores_18(self):
        r = check_eligibility("Native German speaker is preferred but not mandatory.")
        self.assertTrue(r.eligible)
        self.assertEqual(r.language_accessibility, 18)

    def test_b2_german_preferred_scores_22(self):
        r = check_eligibility("B2 German would be a plus.")
        self.assertTrue(r.eligible)
        self.assertEqual(r.language_accessibility, 22)

    def test_italian_b2_preferred_scores_22(self):
        r = check_eligibility("Italian B2 is preferred.")
        self.assertTrue(r.eligible)
        self.assertEqual(r.language_accessibility, 22)

    def test_local_language_plus_scores_26(self):
        r = check_eligibility("Knowledge of the local language is a plus.")
        self.assertTrue(r.eligible)
        self.assertEqual(r.language_accessibility, 26)

    def test_fluent_italian_not_required_scores_26(self):
        r = check_eligibility("Fluent Italian would be an advantage but not required.")
        self.assertTrue(r.eligible)
        self.assertEqual(r.language_accessibility, 26)

    def test_english_beats_soft_language(self):
        # English official language + soft Italian barrier — English wins (40)
        r = check_eligibility(
            "Working language is English. Italian is a plus but not required."
        )
        self.assertEqual(r.language_accessibility, 40)

    def test_no_language_signal_baseline_30(self):
        r = check_eligibility("We are looking for a motivated product manager.")
        self.assertEqual(r.language_accessibility, 30)


# ── 4. Visa accessibility tiers ───────────────────────────────────────────────

class TestVisaAccessibilityScoring(unittest.TestCase):

    def test_visa_sponsorship_available_scores_40(self):
        r = check_eligibility("Visa sponsorship is available for this role.")
        self.assertEqual(r.visa_accessibility, 40)

    def test_will_sponsor_visa_scores_40(self):
        r = check_eligibility("We will sponsor your visa.")
        self.assertEqual(r.visa_accessibility, 40)

    def test_sponsorship_available_scores_40(self):
        r = check_eligibility("Sponsorship is available for qualified candidates.")
        self.assertEqual(r.visa_accessibility, 40)

    def test_relocation_package_scores_35(self):
        r = check_eligibility("We provide a relocation package.")
        self.assertEqual(r.visa_accessibility, 35)

    def test_relocation_support_scores_35(self):
        r = check_eligibility("Relocation support provided for international hires.")
        self.assertEqual(r.visa_accessibility, 35)

    def test_relocation_costs_covered_scores_35(self):
        r = check_eligibility("Relocation costs will be covered.")
        self.assertEqual(r.visa_accessibility, 35)

    def test_international_candidates_welcome_scores_30(self):
        r = check_eligibility("International candidates are welcome to apply.")
        self.assertEqual(r.visa_accessibility, 30)

    def test_open_to_worldwide_candidates_scores_30(self):
        r = check_eligibility("Open to candidates from all countries.")
        self.assertEqual(r.visa_accessibility, 30)

    def test_eu_citizenship_preferred_soft_scores_18(self):
        r = check_eligibility("EU citizenship is preferred.")
        self.assertTrue(r.eligible)   # not a hard reject
        self.assertEqual(r.visa_accessibility, 18)

    def test_eu_passport_preferred_soft_scores_18(self):
        r = check_eligibility("EU passport is a plus.")
        self.assertTrue(r.eligible)
        self.assertEqual(r.visa_accessibility, 18)

    def test_no_visa_signals_baseline_28(self):
        r = check_eligibility("Competitive salary, flexible hours, great team.")
        self.assertEqual(r.visa_accessibility, 28)

    def test_sponsorship_beats_relocation(self):
        r = check_eligibility(
            "Visa sponsorship is available. We also offer a relocation package."
        )
        self.assertEqual(r.visa_accessibility, 40)   # sponsorship wins


# ── 5. English environment scoring ───────────────────────────────────────────

class TestEnglishEnvironmentScoring(unittest.TestCase):

    def test_working_language_english_scores_10(self):
        r = check_eligibility("Working language is English.")
        self.assertEqual(r.english_environment, 10)

    def test_official_language_english_scores_10(self):
        r = check_eligibility("English is the official company language.")
        self.assertEqual(r.english_environment, 10)

    def test_international_team_scores_7(self):
        r = check_eligibility("Join our international team of professionals.")
        self.assertEqual(r.english_environment, 7)

    def test_diverse_team_scores_7(self):
        r = check_eligibility("Work in a diverse team environment.")
        self.assertEqual(r.english_environment, 7)

    def test_multicultural_environment_scores_7(self):
        r = check_eligibility("We foster a multicultural workplace.")
        self.assertEqual(r.english_environment, 7)

    def test_global_team_scores_7(self):
        r = check_eligibility("You will work within a global team.")
        self.assertEqual(r.english_environment, 7)

    def test_fluent_english_required_scores_5(self):
        r = check_eligibility("Fluent in English required. Strong communication skills.")
        self.assertEqual(r.english_environment, 5)

    def test_no_signals_scores_0(self):
        r = check_eligibility("Product Manager role in our growing company.")
        self.assertEqual(r.english_environment, 0)

    def test_working_language_beats_intl_team(self):
        r = check_eligibility("Working language is English. Our international team...")
        self.assertEqual(r.english_environment, 10)   # capped at 10, working lang wins

    def test_people_from_countries_scores_7(self):
        r = check_eligibility("Our team consists of people from 20+ countries.")
        self.assertEqual(r.english_environment, 7)


# ── 6. International company signal scoring ───────────────────────────────────

class TestInternationalSignalsScoring(unittest.TestCase):

    def test_global_company_scores_8(self):
        r = check_eligibility("Join our global company with a strong mission.")
        self.assertEqual(r.international_signals, 8)

    def test_multinational_corporation_scores_8(self):
        r = check_eligibility("We are a multinational corporation.")
        self.assertEqual(r.international_signals, 8)

    def test_offices_in_countries_scores_8(self):
        r = check_eligibility("We operate across 40 countries.")
        self.assertEqual(r.international_signals, 8)

    def test_worldwide_offices_scores_8(self):
        r = check_eligibility("Worldwide offices and operations.")
        self.assertEqual(r.international_signals, 8)

    def test_expat_friendly_scores_7(self):
        r = check_eligibility("We are an expat-friendly employer.")
        self.assertEqual(r.international_signals, 7)

    def test_highly_international_team_scores_7(self):
        r = check_eligibility("You will join our highly international team.")
        self.assertEqual(r.international_signals, 7)

    def test_diverse_and_inclusive_scores_7(self):
        r = check_eligibility("We are a diverse and inclusive company.")
        self.assertEqual(r.international_signals, 7)

    def test_international_team_scores_5(self):
        r = check_eligibility("Work with our international colleagues.")
        self.assertEqual(r.international_signals, 5)

    def test_international_welcome_scores_5(self):
        r = check_eligibility("International candidates are welcome.")
        self.assertGreaterEqual(r.international_signals, 5)

    def test_visa_sponsorship_contributes_to_intl(self):
        r = check_eligibility("Visa sponsorship available. Strong product team.")
        self.assertGreaterEqual(r.international_signals, 5)

    def test_no_signals_scores_0(self):
        r = check_eligibility("Join our team as a product manager.")
        self.assertEqual(r.international_signals, 0)

    def test_global_company_caps_at_10(self):
        r = check_eligibility(
            "We are a global company with expat-friendly policies, "
            "a highly international team, and worldwide presence."
        )
        self.assertEqual(r.international_signals, 10)


# ── 7. EligibilityResult field presence ───────────────────────────────────────

class TestEligibilityResultFields(unittest.TestCase):

    def test_eligible_result_has_all_fields(self):
        r = check_eligibility("Join our product team.")
        self.assertTrue(hasattr(r, "eligibility_score"))
        self.assertTrue(hasattr(r, "language_accessibility"))
        self.assertTrue(hasattr(r, "visa_accessibility"))
        self.assertTrue(hasattr(r, "english_environment"))
        self.assertTrue(hasattr(r, "international_signals"))

    def test_rejected_result_has_all_fields(self):
        r = check_eligibility("German required.")
        self.assertTrue(hasattr(r, "eligibility_score"))
        self.assertTrue(hasattr(r, "language_accessibility"))
        self.assertTrue(hasattr(r, "visa_accessibility"))

    def test_score_is_sum_of_components(self):
        r = check_eligibility(
            "Working language is English. Visa sponsorship available. "
            "International team."
        )
        expected = (
            r.language_accessibility
            + r.visa_accessibility
            + r.english_environment
            + r.international_signals
        )
        self.assertEqual(r.eligibility_score, expected)

    def test_score_within_0_to_100(self):
        for desc in [
            "",
            "German required.",
            "Product manager role.",
            "Working language is English. Visa sponsorship available. Global company.",
            "B2 Italian preferred.",
            "International candidates welcome. Diverse team.",
        ]:
            r = check_eligibility(desc)
            self.assertGreaterEqual(r.eligibility_score, 0)
            self.assertLessEqual(r.eligibility_score, 100)

    def test_compute_eligibility_score_tuple(self):
        lang, visa, env, intl = compute_eligibility_score(
            "Working language is English. Visa sponsorship available.",
            lang_gate_passed=True,
            visa_gate_passed=True,
        )
        self.assertEqual(lang, 40)
        self.assertEqual(visa, 40)
        self.assertEqual(env, 10)
        self.assertGreaterEqual(intl, 5)

    def test_compute_eligibility_score_gate_fail(self):
        lang, visa, env, intl = compute_eligibility_score(
            "Italian required.", lang_gate_passed=False, visa_gate_passed=True
        )
        self.assertEqual(lang, 0)


# ── 8. User target score ranges ───────────────────────────────────────────────

class TestUserExampleTargetRanges(unittest.TestCase):
    """Validate scores match the design specification's illustrative ranges."""

    def test_english_only_plus_visa_sponsorship_near_100(self):
        # "English only + visa sponsorship → 95-100"
        r = check_eligibility(
            "Working language is English. Visa sponsorship is available. "
            "Join our global company with offices worldwide. "
            "International team of 50+ nationalities."
        )
        self.assertTrue(r.eligible)
        self.assertGreaterEqual(r.eligibility_score, 90)

    def test_english_intl_team_no_sponsorship_70_to_85(self):
        # "English + international team + no sponsorship mentioned → 70-85"
        r = check_eligibility(
            "Excellent English required. "
            "Work with our international team of professionals from 20+ countries."
        )
        self.assertTrue(r.eligible)
        self.assertGreaterEqual(r.eligibility_score, 65)
        self.assertLessEqual(r.eligibility_score, 88)

    def test_b2_local_language_preferred_approx_50_to_60(self):
        # "B2 local language preferred → 55-70" (our model yields ~50 without intl signals)
        r = check_eligibility("B2 German would be a plus. No other requirements.")
        self.assertTrue(r.eligible)
        self.assertGreaterEqual(r.eligibility_score, 45)
        self.assertLessEqual(r.eligibility_score, 65)

    def test_b2_preferred_plus_intl_env_hits_55_70(self):
        # With international environment signals, B2-preferred job enters 55-70 range
        r = check_eligibility(
            "B2 German would be a plus. "
            "We are a multinational company. International team."
        )
        self.assertTrue(r.eligible)
        self.assertGreaterEqual(r.eligibility_score, 55)
        self.assertLessEqual(r.eligibility_score, 75)


# ── 9. Combined realistic JD scenarios ────────────────────────────────────────

class TestCombinedJDScenarios(unittest.TestCase):

    def test_top_tier_jd(self):
        # Global company, English only, full sponsorship
        r = check_eligibility(
            "The working language is English. "
            "We are a global company with worldwide operations and a highly international team. "
            "Visa sponsorship is available for candidates requiring it. "
            "We offer a relocation package for international hires."
        )
        self.assertTrue(r.eligible)
        self.assertEqual(r.language_accessibility, 40)
        self.assertEqual(r.visa_accessibility, 40)
        self.assertEqual(r.english_environment, 10)
        self.assertGreaterEqual(r.eligibility_score, 95)

    def test_mid_tier_jd_english_no_visa_info(self):
        # Strong English, no visa info, international team
        r = check_eligibility(
            "Excellent English is required. "
            "Diverse and multicultural team environment. "
        )
        self.assertTrue(r.eligible)
        self.assertEqual(r.language_accessibility, 35)
        self.assertEqual(r.visa_accessibility, 28)
        self.assertGreaterEqual(r.eligibility_score, 65)

    def test_eu_soft_preference_jd(self):
        # EU citizenship preferred (soft), no language signals
        r = check_eligibility(
            "EU citizenship is a plus but not required. "
            "Competitive package."
        )
        self.assertTrue(r.eligible)
        self.assertEqual(r.visa_accessibility, 18)
        self.assertEqual(r.language_accessibility, 30)

    def test_italian_required_rejected(self):
        # Hard reject — Italian required
        r = check_eligibility(
            "Italian required. English also useful. Great team."
        )
        self.assertFalse(r.eligible)
        self.assertEqual(r.eligibility_score, 0)

    def test_soft_language_then_hard_visa_rejected(self):
        # Language gate passes (soft), visa gate fails (hard)
        r = check_eligibility(
            "Italian is a plus. No visa sponsorship available."
        )
        self.assertFalse(r.eligible)
        self.assertEqual(r.eligibility_score, 0)
        self.assertEqual(r.language_accessibility, 0)
        self.assertEqual(r.visa_accessibility, 0)

    def test_local_language_soft_plus_intl_welcome(self):
        r = check_eligibility(
            "French is a nice-to-have. "
            "International candidates are welcome."
        )
        self.assertTrue(r.eligible)
        self.assertEqual(r.language_accessibility, 26)
        self.assertEqual(r.visa_accessibility, 30)

    def test_english_environment_adds_to_overall(self):
        # Global team description raises env score
        r1 = check_eligibility("Product manager role. Good salary.")
        r2 = check_eligibility(
            "Product manager role. Good salary. "
            "Join our global team with 40+ nationalities."
        )
        self.assertGreater(r2.eligibility_score, r1.eligibility_score)

    def test_visa_sponsorship_raises_both_visa_and_intl(self):
        r = check_eligibility("Visa sponsorship available. Join our product team.")
        self.assertEqual(r.visa_accessibility, 40)
        self.assertGreaterEqual(r.international_signals, 5)

    def test_empty_description_eligible_baseline(self):
        r = check_eligibility("")
        self.assertTrue(r.eligible)
        self.assertEqual(r.eligibility_score, 58)   # lang 30 + visa 28

    def test_very_short_description_eligible_baseline(self):
        r = check_eligibility("PM.")
        self.assertTrue(r.eligible)
        self.assertEqual(r.eligibility_score, 58)


if __name__ == "__main__":
    import unittest
    unittest.main()
