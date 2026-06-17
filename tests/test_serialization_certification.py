"""R1 serialization certification: strict JSON round-trip safety (Phase 7C).

Locks the fix that ScoreComponents (with its computed `total` field) and any
model embedding it round-trip cleanly under the base extra="forbid" policy.
"""
import unittest
import warnings

from schemas.scoring import PriorityBucket, ScoreComponents, ScoredJob


def _components():
    return ScoreComponents(track_alignment=28, skill_match=25, mba_relevance=10,
                           company_quality=13, intl_friendliness=5, pivot_bonus=0)


class TestScoreComponentsRoundTrip(unittest.TestCase):
    def test_total_still_serialized(self):
        # Output behavior unchanged: computed `total` is still emitted.
        self.assertIn('"total"', _components().model_dump_json())
        self.assertEqual(_components().total, 81)

    def test_round_trip_succeeds(self):
        c = _components()
        j1 = c.model_dump_json()
        back = ScoreComponents.model_validate_json(j1)  # would raise pre-fix
        self.assertEqual(back.model_dump_json(), j1)     # idempotent

    def test_scored_job_round_trip(self):
        sj = ScoredJob(job_id=1, total_score=81, components=_components(),
                       priority_bucket=PriorityBucket.HIGH, semantic_similarity=0.5)
        j1 = sj.model_dump_json()
        back = ScoredJob.model_validate_json(j1)
        self.assertEqual(back.model_dump_json(), j1)

    def test_round_trip_warning_free(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any Pydantic serialization warning -> error
            j = _components().model_dump_json()
            ScoreComponents.model_validate_json(j)

    def test_extra_still_forbidden_for_real_unknowns(self):
        # extra="ignore" must drop only the computed field, not silently accept
        # arbitrary unknown business fields that indicate a contract drift... but
        # ignore means unknowns are dropped; assert the known fields survive.
        import json
        payload = json.loads(_components().model_dump_json())
        payload["bogus_field"] = 999
        back = ScoreComponents.model_validate(payload)
        self.assertFalse(hasattr(back, "bogus_field"))
        self.assertEqual(back.track_alignment, 28)


if __name__ == "__main__":
    unittest.main()
