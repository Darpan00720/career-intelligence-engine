"""Foundation tests: CareerState factory and validation."""
import unittest

from pydantic import ValidationError

from graph.state import new_career_state, validate_state
from schemas.control import Phase, RunStatus


class TestCareerState(unittest.TestCase):
    def test_initial_state_well_formed(self):
        s = new_career_state(run_id="r1", profile_path="p.json")
        self.assertEqual(s["run_id"], "r1")
        self.assertEqual(s["phase"], Phase.INIT)
        self.assertEqual(s["status"], RunStatus.RUNNING)
        self.assertEqual(s["errors"], [])
        self.assertEqual(s["retry_count"], {})

    def test_initial_state_validates(self):
        s = new_career_state(run_id="r1", profile_path="p.json")
        model = validate_state(s)
        self.assertEqual(model.run_id, "r1")

    def test_unknown_key_rejected(self):
        s = new_career_state(run_id="r1", profile_path="p.json")
        s["not_a_field"] = 123
        with self.assertRaises(ValidationError):
            validate_state(s)

    def test_partial_update_validates(self):
        # A node would return a partial dict; it must validate too.
        validate_state({"phase": Phase.SCORING, "audit_log": ["scored 6"]})


if __name__ == "__main__":
    unittest.main()
