"""Foundation tests: state reducers."""
import unittest

from graph.reducers import extend, merge_by_job_id, merge_dict, take_latest
from schemas.jobs import ClassifiedJob
from schemas.control import Track


class TestExtend(unittest.TestCase):
    def test_appends(self):
        self.assertEqual(extend(["a"], ["b", "c"]), ["a", "b", "c"])

    def test_none_safe(self):
        self.assertEqual(extend(None, None), [])
        self.assertEqual(extend(None, ["x"]), ["x"])
        self.assertEqual(extend(["x"], None), ["x"])


class TestMergeByJobId(unittest.TestCase):
    def test_upsert_replaces_same_id_appends_new(self):
        left = [ClassifiedJob(job_id=1, role_category="unknown", track=Track.UNCLASSIFIED)]
        right = [
            ClassifiedJob(job_id=1, role_category="business_strategy", track=Track.A),
            ClassifiedJob(job_id=2, role_category="product_management", track=Track.A),
        ]
        out = merge_by_job_id(left, right)
        self.assertEqual(len(out), 2)
        by_id = {j.job_id: j for j in out}
        self.assertEqual(by_id[1].role_category, "business_strategy")  # replaced
        self.assertEqual(by_id[2].role_category, "product_management")  # appended

    def test_works_with_dicts(self):
        out = merge_by_job_id([{"job_id": 1, "v": "a"}], [{"job_id": 1, "v": "b"}])
        self.assertEqual(out, [{"job_id": 1, "v": "b"}])

    def test_none_safe(self):
        self.assertEqual(merge_by_job_id(None, None), [])


class TestMergeDict(unittest.TestCase):
    def test_merges(self):
        self.assertEqual(merge_dict({"a": 1}, {"b": 2, "a": 3}), {"a": 3, "b": 2})

    def test_none_safe(self):
        self.assertEqual(merge_dict(None, None), {})


class TestTakeLatest(unittest.TestCase):
    def test_keeps_update_or_existing(self):
        self.assertEqual(take_latest("old", "new"), "new")
        self.assertEqual(take_latest("old", None), "old")


if __name__ == "__main__":
    unittest.main()
