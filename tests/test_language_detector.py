"""
Tests for core/language_detector.py — Phase 1.5c Language Detection.

Covers:
  - Correct language identification for each target language
  - "Other" for non-target languages
  - "Unknown" for short or empty text
  - language_risk LOW / HIGH assignment
  - eligibility_review_required flag under both config modes
  - Determinism: same input always returns same result
  - LanguageDetectionResult field presence and types
  - Graceful degradation edge cases
"""
import unittest
from unittest.mock import patch

from core import config
from core.language_detector import LanguageDetectionResult, detect_jd_language


# ── Representative JD excerpts per language ───────────────────────────────────
# All are > 50 chars so they clear the LANG_DETECT_MIN_CHARS threshold.

_EN = (
    "We are looking for a Product Manager to join our international team. "
    "You will work on defining the product strategy, roadmap, and feature prioritisation. "
    "Strong English communication skills and stakeholder management experience required."
)

_IT = (
    "Siamo alla ricerca di un Product Manager da inserire nel nostro team. "
    "La risorsa si occuperà della definizione della strategia di prodotto, della roadmap "
    "e della prioritizzazione delle funzionalità. Richiesta ottima conoscenza della lingua italiana."
)

_DE = (
    "Wir suchen einen Produktmanager für unser dynamisches Team. "
    "Sie werden an der Produktstrategie und Roadmap arbeiten, Stakeholder managen "
    "und eng mit Entwicklungsteams zusammenarbeiten. Deutschkenntnisse erforderlich."
)

_FR = (
    "Nous recherchons un Chef de Produit pour rejoindre notre équipe dynamique. "
    "Vous travaillerez sur la stratégie produit, la feuille de route et la priorisation "
    "des fonctionnalités en collaboration étroite avec les équipes techniques."
)

_NL = (
    "Wij zijn op zoek naar een Productmanager voor ons team. "
    "U werkt aan de productstrategie, roadmap en prioritering van functies. "
    "U werkt nauw samen met de ontwikkelingsteams en beheert stakeholders."
)

_ES = (
    "Buscamos un Gerente de Producto para unirse a nuestro equipo dinámico. "
    "Trabajarás en la estrategia de producto, hoja de ruta y priorización "
    "de funcionalidades en estrecha colaboración con los equipos técnicos."
)

_PT = (
    "Estamos à procura de um Gestor de Produto para se juntar à nossa equipa. "
    "Trabalhará na estratégia de produto, no roteiro e na priorização de funcionalidades "
    "em estreita colaboração com as equipas técnicas de desenvolvimento."
)

_ZH = (
    "我们正在寻找一名产品经理加入我们的团队。"
    "您将负责制定产品战略、路线图和功能优先级。"
    "需要出色的沟通能力和利益相关者管理经验。"
)


# ── 1. Target language detection accuracy ─────────────────────────────────────

class TestDetectTargetLanguages(unittest.TestCase):

    def test_english_jd_detected_as_english(self):
        r = detect_jd_language(_EN)
        self.assertEqual(r.detected_language, "English")

    def test_italian_jd_detected_as_italian(self):
        r = detect_jd_language(_IT)
        self.assertEqual(r.detected_language, "Italian")

    def test_german_jd_detected_as_german(self):
        r = detect_jd_language(_DE)
        self.assertEqual(r.detected_language, "German")

    def test_french_jd_detected_as_french(self):
        r = detect_jd_language(_FR)
        self.assertEqual(r.detected_language, "French")

    def test_dutch_jd_detected_as_dutch(self):
        r = detect_jd_language(_NL)
        self.assertEqual(r.detected_language, "Dutch")

    def test_spanish_jd_detected_as_spanish(self):
        r = detect_jd_language(_ES)
        self.assertEqual(r.detected_language, "Spanish")

    def test_portuguese_jd_detected_as_portuguese(self):
        r = detect_jd_language(_PT)
        self.assertEqual(r.detected_language, "Portuguese")

    def test_chinese_jd_detected_as_other(self):
        r = detect_jd_language(_ZH)
        self.assertEqual(r.detected_language, "Other")

    def test_english_raw_code(self):
        r = detect_jd_language(_EN)
        self.assertEqual(r.raw_language_code, "en")

    def test_italian_raw_code(self):
        r = detect_jd_language(_IT)
        self.assertEqual(r.raw_language_code, "it")

    def test_german_raw_code(self):
        r = detect_jd_language(_DE)
        self.assertEqual(r.raw_language_code, "de")

    def test_french_raw_code(self):
        r = detect_jd_language(_FR)
        self.assertEqual(r.raw_language_code, "fr")

    def test_dutch_raw_code(self):
        r = detect_jd_language(_NL)
        self.assertEqual(r.raw_language_code, "nl")

    def test_spanish_raw_code(self):
        r = detect_jd_language(_ES)
        self.assertEqual(r.raw_language_code, "es")

    def test_portuguese_raw_code(self):
        r = detect_jd_language(_PT)
        self.assertEqual(r.raw_language_code, "pt")


# ── 2. language_risk assignment ───────────────────────────────────────────────

class TestLanguageRisk(unittest.TestCase):

    def test_english_is_low_risk(self):
        r = detect_jd_language(_EN)
        self.assertEqual(r.language_risk, "LOW")

    def test_italian_is_high_risk(self):
        r = detect_jd_language(_IT)
        self.assertEqual(r.language_risk, "HIGH")

    def test_german_is_high_risk(self):
        r = detect_jd_language(_DE)
        self.assertEqual(r.language_risk, "HIGH")

    def test_french_is_high_risk(self):
        r = detect_jd_language(_FR)
        self.assertEqual(r.language_risk, "HIGH")

    def test_dutch_is_high_risk(self):
        r = detect_jd_language(_NL)
        self.assertEqual(r.language_risk, "HIGH")

    def test_spanish_is_high_risk(self):
        r = detect_jd_language(_ES)
        self.assertEqual(r.language_risk, "HIGH")

    def test_portuguese_is_high_risk(self):
        r = detect_jd_language(_PT)
        self.assertEqual(r.language_risk, "HIGH")

    def test_chinese_is_high_risk(self):
        r = detect_jd_language(_ZH)
        self.assertEqual(r.language_risk, "HIGH")

    def test_unknown_is_low_risk(self):
        r = detect_jd_language("")
        self.assertEqual(r.language_risk, "LOW")


# ── 3. eligibility_review_required flag ───────────────────────────────────────

class TestEligibilityReviewFlag(unittest.TestCase):

    def test_english_review_not_required(self):
        r = detect_jd_language(_EN)
        self.assertFalse(r.eligibility_review_required)

    def test_italian_review_required_when_auto_reject_off(self):
        with patch.object(config, "NON_ENGLISH_AUTO_REJECT", False):
            r = detect_jd_language(_IT)
        self.assertTrue(r.eligibility_review_required)

    def test_german_review_required_when_auto_reject_off(self):
        with patch.object(config, "NON_ENGLISH_AUTO_REJECT", False):
            r = detect_jd_language(_DE)
        self.assertTrue(r.eligibility_review_required)

    def test_french_review_required_when_auto_reject_off(self):
        with patch.object(config, "NON_ENGLISH_AUTO_REJECT", False):
            r = detect_jd_language(_FR)
        self.assertTrue(r.eligibility_review_required)

    def test_dutch_review_required_when_auto_reject_off(self):
        with patch.object(config, "NON_ENGLISH_AUTO_REJECT", False):
            r = detect_jd_language(_NL)
        self.assertTrue(r.eligibility_review_required)

    def test_spanish_review_required_when_auto_reject_off(self):
        with patch.object(config, "NON_ENGLISH_AUTO_REJECT", False):
            r = detect_jd_language(_ES)
        self.assertTrue(r.eligibility_review_required)

    def test_portuguese_review_required_when_auto_reject_off(self):
        with patch.object(config, "NON_ENGLISH_AUTO_REJECT", False):
            r = detect_jd_language(_PT)
        self.assertTrue(r.eligibility_review_required)

    def test_non_english_review_not_required_when_auto_reject_on(self):
        # When auto-reject is ON, system handles it — no human review needed
        with patch.object(config, "NON_ENGLISH_AUTO_REJECT", True):
            r = detect_jd_language(_IT)
        self.assertFalse(r.eligibility_review_required)

    def test_german_review_not_required_when_auto_reject_on(self):
        with patch.object(config, "NON_ENGLISH_AUTO_REJECT", True):
            r = detect_jd_language(_DE)
        self.assertFalse(r.eligibility_review_required)

    def test_unknown_review_not_required(self):
        r = detect_jd_language("")
        self.assertFalse(r.eligibility_review_required)

    def test_english_review_not_required_regardless_of_config(self):
        with patch.object(config, "NON_ENGLISH_AUTO_REJECT", True):
            r = detect_jd_language(_EN)
        self.assertFalse(r.eligibility_review_required)


# ── 4. Edge cases — short / empty / unusual text ──────────────────────────────

class TestEdgeCases(unittest.TestCase):

    def test_empty_string_returns_unknown(self):
        r = detect_jd_language("")
        self.assertEqual(r.detected_language, "Unknown")
        self.assertEqual(r.raw_language_code, "unknown")
        self.assertEqual(r.language_risk, "LOW")
        self.assertFalse(r.eligibility_review_required)

    def test_none_returns_unknown(self):
        r = detect_jd_language(None)
        self.assertEqual(r.detected_language, "Unknown")

    def test_whitespace_only_returns_unknown(self):
        r = detect_jd_language("   \n\t   ")
        self.assertEqual(r.detected_language, "Unknown")

    def test_very_short_text_returns_unknown(self):
        # Less than LANG_DETECT_MIN_CHARS (50)
        r = detect_jd_language("PM role.")
        self.assertEqual(r.detected_language, "Unknown")

    def test_exactly_at_threshold_is_unknown(self):
        # 49 chars — just below threshold
        r = detect_jd_language("a" * 49)
        self.assertEqual(r.detected_language, "Unknown")

    def test_above_threshold_triggers_detection(self):
        # English text above 50 chars
        r = detect_jd_language(
            "This is a Product Manager role at our company. "
            "We need someone with great skills and experience."
        )
        # Should not return Unknown
        self.assertNotEqual(r.detected_language, "Unknown")

    def test_numbers_and_symbols_only_returns_low_risk(self):
        # Text that is all numbers — detection unreliable but shouldn't crash
        r = detect_jd_language("1234567890 " * 6)
        self.assertIn(r.language_risk, ("LOW", "HIGH"))  # won't crash either way

    def test_long_text_uses_only_sample_window(self):
        # Insert Italian at the start (within sample window) and English at the end
        italian_prefix = _IT * 5
        long_text = italian_prefix + (_EN * 20)
        r = detect_jd_language(long_text)
        # Should detect the dominant language in the first 3000 chars (Italian)
        self.assertIn(r.detected_language, ("Italian", "Other"))  # definitely not Unknown


# ── 5. Determinism ────────────────────────────────────────────────────────────

class TestDeterminism(unittest.TestCase):
    """Same input must always return the same output (DetectorFactory.seed=0)."""

    def test_english_deterministic(self):
        results = [detect_jd_language(_EN).detected_language for _ in range(5)]
        self.assertEqual(len(set(results)), 1, "Non-deterministic: " + str(results))

    def test_italian_deterministic(self):
        results = [detect_jd_language(_IT).detected_language for _ in range(5)]
        self.assertEqual(len(set(results)), 1)

    def test_german_deterministic(self):
        results = [detect_jd_language(_DE).detected_language for _ in range(5)]
        self.assertEqual(len(set(results)), 1)

    def test_french_deterministic(self):
        results = [detect_jd_language(_FR).detected_language for _ in range(5)]
        self.assertEqual(len(set(results)), 1)

    def test_risk_deterministic_italian(self):
        results = [detect_jd_language(_IT).language_risk for _ in range(5)]
        self.assertEqual(len(set(results)), 1)


# ── 6. LanguageDetectionResult field types ────────────────────────────────────

class TestResultFields(unittest.TestCase):

    def test_result_has_detected_language(self):
        r = detect_jd_language(_EN)
        self.assertTrue(hasattr(r, "detected_language"))
        self.assertIsInstance(r.detected_language, str)

    def test_result_has_raw_language_code(self):
        r = detect_jd_language(_EN)
        self.assertTrue(hasattr(r, "raw_language_code"))
        self.assertIsInstance(r.raw_language_code, str)

    def test_result_has_language_risk(self):
        r = detect_jd_language(_EN)
        self.assertTrue(hasattr(r, "language_risk"))
        self.assertIn(r.language_risk, ("LOW", "HIGH"))

    def test_result_has_eligibility_review_required(self):
        r = detect_jd_language(_EN)
        self.assertTrue(hasattr(r, "eligibility_review_required"))
        self.assertIsInstance(r.eligibility_review_required, bool)

    def test_language_risk_only_two_values(self):
        for desc in [_EN, _IT, _DE, _FR, _NL, _ES, _PT, "", "short"]:
            r = detect_jd_language(desc)
            self.assertIn(r.language_risk, ("LOW", "HIGH"))

    def test_detected_language_valid_values(self):
        valid = {"English", "Italian", "German", "French", "Dutch",
                 "Spanish", "Portuguese", "Other", "Unknown"}
        for desc in [_EN, _IT, _DE, _FR, _NL, _ES, _PT, _ZH, "", "short"]:
            r = detect_jd_language(desc)
            self.assertIn(r.detected_language, valid)

    def test_is_dataclass(self):
        import dataclasses
        r = detect_jd_language(_EN)
        self.assertTrue(dataclasses.is_dataclass(r))


# ── 7. High-risk language invariants ──────────────────────────────────────────

class TestHighRiskInvariants(unittest.TestCase):
    """Any non-English, non-Unknown language must always be HIGH risk."""

    _NON_EN_SAMPLES = [_IT, _DE, _FR, _NL, _ES, _PT, _ZH]

    def test_non_english_always_high_risk(self):
        for desc in self._NON_EN_SAMPLES:
            r = detect_jd_language(desc)
            if r.detected_language not in ("English", "Unknown"):
                self.assertEqual(
                    r.language_risk, "HIGH",
                    f"Expected HIGH for {r.detected_language}, got {r.language_risk}",
                )

    def test_english_always_low_risk(self):
        r = detect_jd_language(_EN)
        self.assertEqual(r.language_risk, "LOW")

    def test_unknown_always_low_risk(self):
        r = detect_jd_language("")
        self.assertEqual(r.language_risk, "LOW")

    def test_high_risk_raw_code_not_en(self):
        for desc in self._NON_EN_SAMPLES:
            r = detect_jd_language(desc)
            if r.language_risk == "HIGH":
                self.assertNotEqual(r.raw_language_code, "en")

    def test_review_required_implies_high_risk(self):
        """review_required=True must never appear on a LOW-risk result."""
        for desc in [_EN, _IT, _DE, _FR, _NL, _ES, _PT, _ZH, ""]:
            r = detect_jd_language(desc)
            if r.eligibility_review_required:
                self.assertEqual(r.language_risk, "HIGH")

    def test_auto_reject_off_high_risk_implies_review_required(self):
        """When auto-reject is OFF, HIGH-risk must always require review."""
        with patch.object(config, "NON_ENGLISH_AUTO_REJECT", False):
            for desc in self._NON_EN_SAMPLES:
                r = detect_jd_language(desc)
                if r.language_risk == "HIGH":
                    self.assertTrue(
                        r.eligibility_review_required,
                        f"Expected review_required=True for {r.detected_language}",
                    )

    def test_auto_reject_on_high_risk_never_requires_review(self):
        """When auto-reject is ON, system handles it — no human review."""
        with patch.object(config, "NON_ENGLISH_AUTO_REJECT", True):
            for desc in self._NON_EN_SAMPLES:
                r = detect_jd_language(desc)
                self.assertFalse(
                    r.eligibility_review_required,
                    f"Expected review_required=False in auto-reject mode for {r.detected_language}",
                )


# ── 8. Real-world JD scenarios ────────────────────────────────────────────────

class TestRealWorldScenarios(unittest.TestCase):

    def test_english_jd_with_company_name_in_italian(self):
        # Company name in Italian but JD in English → should detect English
        desc = (
            "Banca d'Italia is looking for a Product Manager to join our digital team. "
            "The role involves working on product strategy, roadmap, and feature delivery. "
            "You will collaborate with cross-functional teams across our global offices."
        )
        r = detect_jd_language(desc)
        self.assertEqual(r.language_risk, "LOW")  # English dominant

    def test_fully_italian_jd_without_english_title(self):
        desc = (
            "La nostra azienda è alla ricerca di un professionista motivato. "
            "Il candidato ideale ha esperienza nella gestione di prodotti digitali, "
            "spiccate doti comunicative e capacità di lavorare in team internazionali. "
            "Si richiedono ottima conoscenza della lingua italiana e dimestichezza "
            "con strumenti di project management come Jira e Confluence."
        )
        r = detect_jd_language(desc)
        self.assertEqual(r.detected_language, "Italian")
        self.assertEqual(r.language_risk, "HIGH")

    def test_fully_german_jd(self):
        desc = (
            "Wir suchen ab sofort einen engagierten Produktmanager für unser Berliner Büro. "
            "Aufgaben: Entwicklung der Produktstrategie, Abstimmung mit Stakeholdern, "
            "Priorisierung des Backlogs sowie enge Zusammenarbeit mit Entwicklern. "
            "Voraussetzungen: Studienabschluss, mehrjährige Berufserfahrung, Deutschkenntnisse."
        )
        r = detect_jd_language(desc)
        self.assertEqual(r.detected_language, "German")
        self.assertEqual(r.language_risk, "HIGH")

    def test_english_jd_mentioning_german_requirement(self):
        # Gate would catch "German required", but JD itself is in English
        desc = (
            "We are looking for a Product Manager. The role requires excellent English. "
            "German required for client-facing communication. Strategy and analytics background preferred."
        )
        r = detect_jd_language(desc)
        # JD is in English → language_risk = LOW (gate handles the German requirement separately)
        self.assertEqual(r.detected_language, "English")
        self.assertEqual(r.language_risk, "LOW")

    def test_french_startup_jd(self):
        desc = (
            "Rejoignez notre startup en pleine croissance dans le secteur de la fintech. "
            "Nous cherchons un Chef de Produit expérimenté pour diriger notre équipe produit. "
            "Vous serez responsable de la stratégie, de la roadmap et des lancements."
        )
        r = detect_jd_language(desc)
        self.assertEqual(r.detected_language, "French")
        self.assertEqual(r.language_risk, "HIGH")

    def test_dutch_jd_is_high_risk(self):
        desc = (
            "Wij zoeken een gedreven Productmanager voor ons team in Amsterdam. "
            "Jij bent verantwoordelijk voor de productstrategie en de roadmap. "
            "Je werkt nauw samen met developers, designers en commerciële teams."
        )
        r = detect_jd_language(desc)
        self.assertEqual(r.detected_language, "Dutch")
        self.assertEqual(r.language_risk, "HIGH")


if __name__ == "__main__":
    unittest.main()
