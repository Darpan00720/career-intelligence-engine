"""
Tests for the unified seniority filter in search_agent.py.

Both Track A (product/AI/strategy/digital) and Track B (HR/people) now use the same rule:
  ACCEPT if title contains an internship-style signal (intern, graduate, trainee, …).
  REJECT if title contains a disqualifying signal (analyst, associate, manager, …) and no accept.
  REJECT if title contains neither signal.
  Accept always overrides reject in the same title.

Run with:  python3 -m unittest discover -s tests -v
Or:        python3 tests/test_search_filters.py
"""
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.search_agent import (
    _check_seniority_by_track,
    _assign_track,
    _INTERN_ACCEPT,
    _SENIORITY_REJECT,
    _TRACK_A,
    _TRACK_B,
    _process_job,
)


# ── User-specified ACCEPT examples ────────────────────────────────────────────

class TestUserAcceptExamples(unittest.TestCase):
    """Every example the user listed under ACCEPT must pass for its natural track."""

    def _ok(self, title: str, track: str) -> bool:
        ok, _ = _check_seniority_by_track(title, "", track)
        return ok

    def test_ai_product_intern_track_a(self):
        self.assertTrue(self._ok("AI Product Intern", "A"))

    def test_product_management_internship_track_a(self):
        self.assertTrue(self._ok("Product Management Internship", "A"))

    def test_intern_strategy_operations_track_a(self):
        self.assertTrue(self._ok("Intern Strategy & Operations", "A"))

    def test_talent_acquisition_intern_track_b(self):
        self.assertTrue(self._ok("Talent Acquisition Intern", "B"))

    def test_hr_analytics_intern_track_b(self):
        # "analytics" ≠ "analyst" — no reject signal fires
        self.assertTrue(self._ok("HR Analytics Intern", "B"))

    def test_people_operations_graduate_internship_track_b(self):
        self.assertTrue(self._ok("People Operations Graduate Internship", "B"))

    def test_praktikum_gtm_strategy_track_a(self):
        self.assertTrue(self._ok("Praktikum Go-to-Market Strategy", "A"))

    def test_stage_strategie_et_operations_track_a(self):
        self.assertTrue(self._ok("Stage - Stratégie et Operations", "A"))


# ── User-specified REJECT examples ────────────────────────────────────────────

class TestUserRejectExamples(unittest.TestCase):
    """Every example the user listed under REJECT must fail for its natural track."""

    def _ok(self, title: str, track: str) -> bool:
        ok, _ = _check_seniority_by_track(title, "", track)
        return ok

    def test_product_data_analyst_track_a(self):
        self.assertFalse(self._ok("Product Data Analyst", "A"))

    def test_strategy_operations_associate_track_a(self):
        self.assertFalse(self._ok("Strategy & Operations Associate", "A"))

    def test_talent_acquisition_operations_analyst_track_b(self):
        self.assertFalse(self._ok("Talent Acquisition Operations Analyst", "B"))

    def test_hr_data_management_associate_track_b(self):
        self.assertFalse(self._ok("HR Data Management Associate", "B"))

    def test_people_analytics_specialist_track_b(self):
        self.assertFalse(self._ok("People Analytics Specialist", "B"))

    def test_junior_product_manager_track_a(self):
        self.assertFalse(self._ok("Junior Product Manager", "A"))

    def test_product_manager_track_a(self):
        self.assertFalse(self._ok("Product Manager", "A"))

    def test_associate_product_operations_specialist_track_a(self):
        self.assertFalse(self._ok("Associate Product Operations Specialist", "A"))


# ── Accept signals — all keywords, both tracks ───────────────────────────────

class TestAcceptSignals(unittest.TestCase):
    """Each accept keyword alone is sufficient to pass, in both tracks."""

    def _ok(self, title: str) -> tuple[bool, bool]:
        a, _ = _check_seniority_by_track(title, "", "A")
        b, _ = _check_seniority_by_track(title, "", "B")
        return a, b

    def _both(self, title: str) -> bool:
        return all(self._ok(title))

    def test_intern(self):
        self.assertTrue(self._both("AI Strategy Intern"))

    def test_internship(self):
        self.assertTrue(self._both("Product Management Internship"))

    def test_working_student_space(self):
        self.assertTrue(self._both("Working Student AI Strategy"))

    def test_working_student_hyphen(self):
        self.assertTrue(self._both("Working-Student Digital Transformation"))

    def test_werkstudent(self):
        self.assertTrue(self._both("Werkstudent HR Analytics"))

    def test_graduate(self):
        self.assertTrue(self._both("Graduate Product Manager"))

    def test_graduate_program(self):
        self.assertTrue(self._both("Graduate Program People Analytics"))

    def test_trainee(self):
        self.assertTrue(self._both("Business Strategy Trainee"))

    def test_apprentice(self):
        self.assertTrue(self._both("People Analytics Apprentice"))

    def test_apprenticeship(self):
        self.assertTrue(self._both("Digital Transformation Apprenticeship"))

    def test_stage(self):
        self.assertTrue(self._both("Stage - Stratégie et Opérations"))

    def test_stagiaire(self):
        self.assertTrue(self._both("Stagiaire Product Manager"))

    def test_praktikum(self):
        self.assertTrue(self._both("Praktikum Go-to-Market Strategy"))

    def test_praktikant(self):
        self.assertTrue(self._both("AI Strategy Praktikant"))

    def test_tirocinio(self):
        self.assertTrue(self._both("Tirocinio People Analytics"))

    def test_tirocinante(self):
        self.assertTrue(self._both("Tirocinante HR Analytics"))

    def test_reason_code_is_intern_accept(self):
        _, reason = _check_seniority_by_track("Product Intern", "", "A")
        self.assertEqual(reason, "intern_accept")

    def test_reason_code_same_both_tracks(self):
        _, r_a = _check_seniority_by_track("HR Trainee", "", "A")
        _, r_b = _check_seniority_by_track("HR Trainee", "", "B")
        self.assertEqual(r_a, r_b)
        self.assertEqual(r_a, "intern_accept")


# ── Reject signals — all keywords, both tracks ───────────────────────────────

class TestRejectSignals(unittest.TestCase):
    """Each reject keyword alone is sufficient to block a title, in both tracks."""

    def _ok(self, title: str) -> tuple[bool, bool]:
        a, _ = _check_seniority_by_track(title, "", "A")
        b, _ = _check_seniority_by_track(title, "", "B")
        return a, b

    def _both_fail(self, title: str) -> bool:
        return not any(self._ok(title))

    def test_analyst(self):
        self.assertTrue(self._both_fail("HR Data Analyst"))

    def test_associate(self):
        self.assertTrue(self._both_fail("Strategy & Operations Associate"))

    def test_specialist(self):
        self.assertTrue(self._both_fail("People Analytics Specialist"))

    def test_coordinator(self):
        self.assertTrue(self._both_fail("Talent Acquisition Coordinator"))

    def test_junior(self):
        self.assertTrue(self._both_fail("Junior Product Manager"))

    def test_manager(self):
        self.assertTrue(self._both_fail("Product Manager"))

    def test_lead(self):
        self.assertTrue(self._both_fail("Talent Acquisition Lead"))

    def test_principal(self):
        self.assertTrue(self._both_fail("Principal Product Manager"))

    def test_director(self):
        self.assertTrue(self._both_fail("Director of People Analytics"))

    def test_head_of(self):
        self.assertTrue(self._both_fail("Head of Workforce Planning"))

    def test_head_alone(self):
        self.assertTrue(self._both_fail("Head AI Strategy"))

    def test_vp(self):
        self.assertTrue(self._both_fail("VP Talent Acquisition"))

    def test_vice_president_space(self):
        self.assertTrue(self._both_fail("Vice President HR Analytics"))

    def test_vice_president_hyphen(self):
        self.assertTrue(self._both_fail("Vice-President Business Strategy"))

    def test_chief(self):
        self.assertTrue(self._both_fail("Chief People Officer"))

    def test_partner(self):
        self.assertTrue(self._both_fail("Strategy Partner"))

    def test_reason_code_is_seniority_reject(self):
        _, reason = _check_seniority_by_track("HR Analytics Analyst", "", "B")
        self.assertEqual(reason, "seniority_reject")

    def test_analytics_does_not_trigger_analyst_reject(self):
        # "analytics" ≠ "analyst" — word boundary must not fire
        ok_a, _ = _check_seniority_by_track("HR Analytics Intern", "", "B")
        self.assertTrue(ok_a)


# ── No-signal rejection ───────────────────────────────────────────────────────

class TestNoSignalRejection(unittest.TestCase):
    """Titles with neither accept nor reject signal are rejected (default deny)."""

    def _ok(self, title: str, track: str) -> bool:
        ok, _ = _check_seniority_by_track(title, "", track)
        return ok

    def _reason(self, title: str, track: str) -> str:
        _, reason = _check_seniority_by_track(title, "", track)
        return reason

    def test_plain_product_manager_no_signal_track_a(self):
        # "manager" IS a reject signal, so this fires seniority_reject not no_accept_signal
        ok, reason = _check_seniority_by_track("Product Manager", "", "A")
        self.assertFalse(ok)
        self.assertEqual(reason, "seniority_reject")

    def test_bare_role_name_no_signals_track_a(self):
        # no accept OR reject signal → no_accept_signal
        ok, reason = _check_seniority_by_track("Business Strategy Consultant", "", "A")
        self.assertFalse(ok)
        self.assertEqual(reason, "no_accept_signal")

    def test_bare_role_name_no_signals_track_b(self):
        ok, reason = _check_seniority_by_track("Workforce Planner", "", "B")
        self.assertFalse(ok)
        self.assertEqual(reason, "no_accept_signal")

    def test_reason_code_no_accept_signal(self):
        self.assertEqual(self._reason("Digital Transformation Consultant", "A"), "no_accept_signal")


# ── Accept overrides reject ───────────────────────────────────────────────────

class TestAcceptOverridesReject(unittest.TestCase):
    """When both accept and reject signals are present, accept wins."""

    def _ok(self, title: str, track: str) -> bool:
        ok, _ = _check_seniority_by_track(title, "", track)
        return ok

    def test_manager_intern_track_a(self):
        # "intern" accept wins over "manager" reject
        self.assertTrue(self._ok("Product Manager Intern", "A"))

    def test_manager_intern_track_b(self):
        self.assertTrue(self._ok("HR Analytics Manager Intern", "B"))

    def test_analyst_intern_track_b(self):
        self.assertTrue(self._ok("HR Analytics Analyst Intern", "B"))

    def test_director_graduate_track_a(self):
        # "graduate" wins over "director"
        self.assertTrue(self._ok("Director of Product Graduate Program", "A"))

    def test_head_trainee_track_b(self):
        self.assertTrue(self._ok("Head of People Analytics Trainee", "B"))

    def test_vp_working_student_track_a(self):
        self.assertTrue(self._ok("VP AI Strategy Working Student", "A"))

    def test_senior_intern_reason_is_intern_accept(self):
        # "senior" is not in reject list, "intern" is accept
        _, reason = _check_seniority_by_track("Senior People Analytics Intern", "", "B")
        self.assertEqual(reason, "intern_accept")

    def test_junior_graduate_track_b(self):
        # "junior" IS a reject signal; "graduate" accept wins
        self.assertTrue(self._ok("Junior HR Analytics Graduate", "B"))

    def test_associate_stage_track_b(self):
        # "associate" reject; "stage" accept wins
        self.assertTrue(self._ok("Stage Associate Product Manager", "A"))


# ── Unclassified fallback ─────────────────────────────────────────────────────

class TestUnclassifiedFallback(unittest.TestCase):
    """Unclassified track delegates to the existing generic _check_seniority()."""

    def _ok(self, title: str, desc: str = "") -> bool:
        ok, _ = _check_seniority_by_track(title, desc, "unclassified")
        return ok

    def test_intern_accepted(self):
        self.assertTrue(self._ok("Marketing Intern"))

    def test_graduate_accepted(self):
        self.assertTrue(self._ok("Graduate Marketing Analyst"))

    def test_junior_accepted(self):
        # generic _check_seniority still allows junior + accept signal
        self.assertTrue(self._ok("Junior Marketing Manager"))

    def test_director_rejected(self):
        self.assertFalse(self._ok("Director of Marketing"))

    def test_vp_rejected(self):
        self.assertFalse(self._ok("VP Marketing"))

    def test_senior_no_signal_rejected(self):
        self.assertFalse(self._ok("Senior Marketing Manager"))


# ── digital_transformation belongs to Track A ─────────────────────────────────

class TestDigitalTransformationTrackA(unittest.TestCase):

    def test_digital_transformation_in_track_a_set(self):
        self.assertIn("digital_transformation", _TRACK_A)

    def test_digital_transformation_not_in_track_b(self):
        self.assertNotIn("digital_transformation", _TRACK_B)

    def test_assign_track_returns_a(self):
        self.assertEqual(_assign_track("digital_transformation"), "A")

    def test_intern_accepted(self):
        ok, _ = _check_seniority_by_track("Digital Transformation Intern", "", "A")
        self.assertTrue(ok)

    def test_trainee_accepted(self):
        ok, _ = _check_seniority_by_track("Digital Transformation Trainee", "", "A")
        self.assertTrue(ok)

    def test_working_student_accepted(self):
        ok, _ = _check_seniority_by_track("Digital Transformation Working Student", "", "A")
        self.assertTrue(ok)

    def test_analyst_rejected(self):
        ok, _ = _check_seniority_by_track("Digital Transformation Analyst", "", "A")
        self.assertFalse(ok)

    def test_manager_rejected(self):
        ok, _ = _check_seniority_by_track("Digital Transformation Manager", "", "A")
        self.assertFalse(ok)

    def test_consultant_rejected_no_signal(self):
        ok, reason = _check_seniority_by_track("Digital Transformation Consultant", "", "A")
        self.assertFalse(ok)
        self.assertEqual(reason, "no_accept_signal")


# ── Track set membership ──────────────────────────────────────────────────────

class TestTrackSetMembership(unittest.TestCase):

    def test_track_a_contents(self):
        expected = {"product_management", "ai_strategy", "business_strategy", "digital_transformation"}
        self.assertEqual(_TRACK_A, expected)

    def test_track_b_contents(self):
        expected = {"people_analytics", "hr_analytics", "workforce_planning", "talent_acquisition"}
        self.assertEqual(_TRACK_B, expected)

    def test_no_overlap(self):
        self.assertEqual(_TRACK_A & _TRACK_B, set())

    def test_product_management_is_a(self):
        self.assertEqual(_assign_track("product_management"), "A")

    def test_ai_strategy_is_a(self):
        self.assertEqual(_assign_track("ai_strategy"), "A")

    def test_business_strategy_is_a(self):
        self.assertEqual(_assign_track("business_strategy"), "A")

    def test_digital_transformation_is_a(self):
        self.assertEqual(_assign_track("digital_transformation"), "A")

    def test_people_analytics_is_b(self):
        self.assertEqual(_assign_track("people_analytics"), "B")

    def test_hr_analytics_is_b(self):
        self.assertEqual(_assign_track("hr_analytics"), "B")

    def test_workforce_planning_is_b(self):
        self.assertEqual(_assign_track("workforce_planning"), "B")

    def test_talent_acquisition_is_b(self):
        self.assertEqual(_assign_track("talent_acquisition"), "B")

    def test_unknown_is_unclassified(self):
        self.assertEqual(_assign_track("unknown"), "unclassified")

    def test_empty_is_unclassified(self):
        self.assertEqual(_assign_track(""), "unclassified")


# ── Regex sanity checks ────────────────────────────────────────────────────────

class TestRegexSanity(unittest.TestCase):
    """Guard against false-positive and false-negative regex matches."""

    def test_analytics_not_analyst(self):
        # "analytics" must NOT trigger _SENIORITY_REJECT
        self.assertIsNone(_SENIORITY_REJECT.search("hr analytics"))

    def test_analyst_triggers_reject(self):
        self.assertIsNotNone(_SENIORITY_REJECT.search("hr analyst"))

    def test_intern_triggers_accept(self):
        self.assertIsNotNone(_INTERN_ACCEPT.search("product intern"))

    def test_internal_does_not_trigger_intern_accept(self):
        # "internal" contains "intern" but word boundary must block it
        self.assertIsNone(_INTERN_ACCEPT.search("internal communications"))

    def test_internship_triggers_accept(self):
        self.assertIsNotNone(_INTERN_ACCEPT.search("6-month internship"))

    def test_stage_triggers_accept(self):
        self.assertIsNotNone(_INTERN_ACCEPT.search("stage - stratégie et opérations"))

    def test_working_student_space_triggers_accept(self):
        self.assertIsNotNone(_INTERN_ACCEPT.search("working student ai strategy"))

    def test_working_student_hyphen_triggers_accept(self):
        self.assertIsNotNone(_INTERN_ACCEPT.search("working-student digital transformation"))

    def test_head_of_triggers_reject(self):
        self.assertIsNotNone(_SENIORITY_REJECT.search("head of product"))

    def test_vice_president_triggers_reject(self):
        self.assertIsNotNone(_SENIORITY_REJECT.search("vice president hr"))

    def test_vice_hyphen_president_triggers_reject(self):
        self.assertIsNotNone(_SENIORITY_REJECT.search("vice-president strategy"))

    def test_graduate_program_triggers_accept(self):
        self.assertIsNotNone(_INTERN_ACCEPT.search("graduate program people analytics"))

    def test_graduate_alone_triggers_accept(self):
        self.assertIsNotNone(_INTERN_ACCEPT.search("hr analytics graduate"))

    def test_partner_triggers_reject(self):
        self.assertIsNotNone(_SENIORITY_REJECT.search("strategy partner"))

    def test_partnership_does_not_trigger_partner_reject(self):
        # "partnerships" must not match \bpartner\b — word boundary blocks the plural
        self.assertIsNone(re.search(r"\bpartner\b", "partnerships", re.IGNORECASE))
        # full reject regex must also leave "partnerships" alone (no other reject words)
        self.assertIsNone(_SENIORITY_REJECT.search("business partnerships"))
        self.assertIsNone(_SENIORITY_REJECT.search("strategic partnerships"))

    def test_coordinator_triggers_reject(self):
        self.assertIsNotNone(_SENIORITY_REJECT.search("talent acquisition coordinator"))

    def test_specialist_triggers_reject(self):
        self.assertIsNotNone(_SENIORITY_REJECT.search("people analytics specialist"))


# ── Runner ────────────────────────────────────────────────────────────────────

import re  # needed for test_partnership_does_not_trigger_partner_reject inline check

class TestSearchAcquisitionRules(unittest.TestCase):
    def _job(self, *, location="Amsterdam, Netherlands", description="Great English-speaking internship."):
        return {
            "title": "AI Product Intern",
            "company": "Acme",
            "location": location,
            "url": "https://example.com/job",
            "description": description,
            "job_board": "unit",
        }

    def test_rejects_mandatory_non_english_language(self):
        stats = {}
        with patch.dict("os.environ", {"GEO_COUNTRIES": "it,nl"}):
            jid = _process_job(
                self._job(description="English team, but Dutch required for daily operations."),
                [],
                stats,
            )
        self.assertIsNone(jid)
        self.assertEqual(stats.get("rejected_language"), 1)
        self.assertIn("language_required", stats.get("by_language_reason", {}))

    def test_allows_non_english_preference(self):
        stats = {}
        with patch.dict("os.environ", {"GEO_COUNTRIES": "it,nl"}), \
                patch("agents.search_agent.database.get_job_by_hash", return_value=None), \
                patch("agents.search_agent.database.get_job_by_url", return_value=None), \
                patch("agents.search_agent.database.insert_job", return_value=42), \
                patch("agents.search_agent.database.update_job_classification") as update_cls:
            jid = _process_job(
                self._job(description=(
                    "This is an English-speaking AI product internship. "
                    "Italian is a plus but not required for the role."
                )),
                [],
                stats,
            )
        self.assertEqual(jid, 42)
        self.assertEqual(stats.get("accepted"), 1)
        update_cls.assert_called_once()

    def test_rejects_outside_focus_when_configured(self):
        # GEO_COUNTRIES=it,nl (set by run_graph.py / .env) rejects out-of-focus.
        stats = {}
        with patch.dict("os.environ", {"GEO_COUNTRIES": "it,nl"}):
            jid = _process_job(self._job(location="Berlin, Germany"), [], stats)
        self.assertIsNone(jid)
        self.assertEqual(stats.get("rejected_geography"), 1)

    def test_keeps_eu_wide_when_focus_unset(self):
        # No GEO_COUNTRIES → legacy EU-wide: Berlin is accepted.
        stats = {}
        with patch.dict("os.environ", {}, clear=True), \
                patch("agents.search_agent.database.get_job_by_hash", return_value=None), \
                patch("agents.search_agent.database.get_job_by_url", return_value=None), \
                patch("agents.search_agent.database.insert_job", return_value=7), \
                patch("agents.search_agent.database.update_job_classification"):
            jid = _process_job(self._job(location="Berlin, Germany"), [], stats)
        self.assertEqual(jid, 7)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestUserAcceptExamples,
        TestUserRejectExamples,
        TestAcceptSignals,
        TestRejectSignals,
        TestNoSignalRejection,
        TestAcceptOverridesReject,
        TestUnclassifiedFallback,
        TestDigitalTransformationTrackA,
        TestTrackSetMembership,
        TestRegexSanity,
        TestSearchAcquisitionRules,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
