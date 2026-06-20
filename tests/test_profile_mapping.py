"""Tests for profile_strategy_node + _map_profile (nested-schema fix).

Covers the four cases from the investigation:
  1. Nested candidate-profile schema  -> real values (not placeholders).
  2. Missing profile path             -> graceful fallback (Unknown Candidate).
  3. Relative profile path            -> resolves via BASE_DIR + nested mapping.
  4. Legacy flat profile schema       -> backward compatible.
"""
import unittest

from graph.nodes import _map_profile, profile_strategy_node


# A representative NESTED profile (mirrors data/candidate_profile.json shape).
_NESTED = {
    "personal": {"name": "Darpan", "last_name": "Jain", "location": "Milan, Italy"},
    "skills": {
        "hr_core": ["Talent Acquisition", "People Analytics"],
        "ai_and_digital": ["AI Strategy", "Prompt Engineering"],
    },
    "target_roles": {
        "track_1_product_and_strategy": ["AI Product Manager Intern", "Product Manager Intern"],
        "track_2_strategic_hr": ["HR Analytics", "People Analytics"],
        "seniority_preference": ["Intern"],            # not a track_* list -> ignored
    },
    "target_geography": {"based_in": "Milan, Italy", "preferred_locations": ["Milan", "London"]},
}

# A LEGACY FLAT profile (the shape the old mapper expected).
_FLAT = {
    "name": "Legacy User",
    "years_experience": 5,
    "skills": ["SQL", "Python"],
    "target_primary": ["product_management"],
    "locations": ["Berlin"],
}


class TestNestedSchema(unittest.TestCase):
    def test_nested_maps_real_values(self):
        p = _map_profile(_NESTED)
        self.assertEqual(p.name, "Darpan Jain")                       # personal.name + last_name
        self.assertEqual(p.target_primary,
                         ["AI Product Manager Intern", "Product Manager Intern"])
        self.assertEqual(p.target_secondary, ["HR Analytics", "People Analytics"])
        self.assertEqual(p.locations, ["Milan", "London"])            # preferred_locations
        self.assertIn("AI Strategy", p.strengths)                     # flattened skills dict

    def test_nested_has_no_placeholders(self):
        p = _map_profile(_NESTED)
        self.assertNotEqual(p.name, "Candidate")
        self.assertNotIn("unknown", p.target_primary)
        self.assertNotIn("EU", p.locations)


class TestMissingProfilePath(unittest.TestCase):
    def test_absolute_missing_path_falls_back(self):
        out = profile_strategy_node({"profile_path": "/nonexistent/none.json"})
        self.assertEqual(out["profile"].name, "Unknown Candidate")    # _FALLBACK_PROFILE
        self.assertEqual(out["target_categories"], ["unknown"])
        self.assertTrue(out.get("errors"))                            # AgentError recorded


class TestRelativeProfilePath(unittest.TestCase):
    def test_relative_path_resolves_and_maps_nested(self):
        # "candidate_profile.json" is resolved to BASE_DIR/data/<name> and read.
        out = profile_strategy_node({"profile_path": "candidate_profile.json"})
        p = out["profile"]
        self.assertEqual(p.name, "Darpan Jain")          # nested name, not "Candidate"
        self.assertNotEqual(p.target_primary, ["unknown"])
        self.assertNotEqual(p.locations, ["EU"])


class TestLegacyFlatSchema(unittest.TestCase):
    def test_flat_schema_still_supported(self):
        p = _map_profile(_FLAT)
        self.assertEqual(p.name, "Legacy User")
        self.assertEqual(p.years_experience, 5)
        self.assertEqual(p.target_primary, ["product_management"])
        self.assertEqual(p.locations, ["Berlin"])
        self.assertEqual(p.strengths, ["SQL", "Python"])

    def test_empty_dict_keeps_min_length_defaults(self):
        # An empty profile still satisfies CareerProfile's min_length constraints.
        p = _map_profile({})
        self.assertEqual(p.name, "Candidate")
        self.assertEqual(p.target_primary, ["unknown"])
        self.assertEqual(p.locations, ["EU"])


class TestTrackBehaviorUnchanged(unittest.TestCase):
    """Constraint 6: the fix must NOT change Track selection.

    The gate lives in profile_strategy_node (graph/nodes.py): Track.A iff
    target_primary[0] is one of the existing tokens, else Track.B. The fix
    changes only the *values* fed to that gate, never the gate itself.
    """

    def _track_for(self, profile_dict: dict):
        import json
        import os
        import tempfile
        fd, p = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(profile_dict, f)
        try:
            out = profile_strategy_node({"profile_path": p})   # absolute path
        finally:
            os.unlink(p)
        return out["track_strategy"].primary_track

    def test_nested_real_profile_stays_track_b(self):
        # Role titles are not Track-A tokens, so the real nested profile derives
        # Track.B — identical to the pre-fix outcome (which derived B from 'unknown').
        from schemas.control import Track
        out = profile_strategy_node({"profile_path": "candidate_profile.json"})
        self.assertEqual(out["track_strategy"].primary_track, Track.B)

    def test_flat_token_profile_still_track_a(self):
        # Gate logic intact: a token in target_primary[0] still selects Track.A.
        from schemas.control import Track
        self.assertEqual(
            self._track_for({"name": "X", "target_primary": ["product_management"],
                             "locations": ["Berlin"]}),
            Track.A)

    def test_flat_nontoken_profile_track_b(self):
        from schemas.control import Track
        self.assertEqual(
            self._track_for({"name": "X", "target_primary": ["random_role"],
                             "locations": ["Berlin"]}),
            Track.B)


if __name__ == "__main__":
    unittest.main()
