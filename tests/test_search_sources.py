"""Tests for the expanded job sources: Apify public boards + new ATS connectors.

All network is mocked — no live HTTP and no Apify calls.
"""
import os
import unittest
from unittest.mock import MagicMock, patch

from agents import apify_search, search_agent


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise search_agent.requests.RequestException(f"HTTP {self.status_code}")


# ── Apify public boards ─────────────────────────────────────────────────────────

class TestApifySearch(unittest.TestCase):
    def test_no_key_degrades_to_empty(self):
        env = {k: v for k, v in os.environ.items() if k not in ("APIFY_API_KEY", "APIFY_TOKEN")}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(apify_search.search_public_boards({}), [])

    def test_normalizes_actor_items(self):
        items = [{"title": "PM Intern", "companyName": "Acme", "location": "Milan, Italy",
                  "jobUrl": "https://x/1", "description": "desc", "postedAt": "2026-06-10"}]
        with patch.object(apify_search, "_run_actor", return_value=items):
            jobs = apify_search.fetch_linkedin("PM Intern", "Milan")
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["title"], "PM Intern")
        self.assertEqual(j["company"], "Acme")
        self.assertEqual(j["job_board"], "linkedin")
        self.assertEqual(j["url"], "https://x/1")

    def test_query_terms_from_nested_profile(self):
        profile = {
            "target_roles": {
                "track_1_product_and_strategy": ["AI PM Intern", "Product Manager Intern"],
                "track_2_strategic_hr": ["HR Analytics"],
                "search_keyword_variants": ["People Analytics Intern"],
            },
            "target_geography": {"preferred_locations": ["Milan", "London"]},
        }
        kws, loc = apify_search._query_terms(profile)
        self.assertEqual(loc, "Milan")
        self.assertLessEqual(len(kws), 3)
        self.assertIn("AI PM Intern", kws)


# ── New ATS connectors ──────────────────────────────────────────────────────────

class TestAtsConnectors(unittest.TestCase):
    def test_ashby_normalizes(self):
        payload = {"jobs": [{"title": "AI Product Intern",
                             "location": {"name": "Berlin, Germany"},
                             "jobUrl": "https://ashby/1",
                             "descriptionHtml": "<p>Great role</p>",
                             "publishedDate": "2026-06-09"}]}
        with patch.object(search_agent.requests, "get", return_value=_Resp(payload)):
            jobs = search_agent.fetch_ashby("Acme", "acme")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "AI Product Intern")
        self.assertEqual(jobs[0]["location"], "Berlin, Germany")
        self.assertEqual(jobs[0]["job_board"], "ashby")
        self.assertIn("Great role", jobs[0]["description"])

    def test_smartrecruiters_normalizes(self):
        payload = {"content": [{"name": "Strategy Intern",
                                "location": {"city": "Milan", "country": "it"},
                                "ref": "https://sr/1", "releasedDate": "2026-06-08"}]}
        with patch.object(search_agent.requests, "get", return_value=_Resp(payload)):
            jobs = search_agent.fetch_smartrecruiters("Globex", "globex")
        self.assertEqual(jobs[0]["title"], "Strategy Intern")
        self.assertEqual(jobs[0]["location"], "Milan, it")
        self.assertEqual(jobs[0]["job_board"], "smartrecruiters")

    def test_404_returns_empty(self):
        with patch.object(search_agent.requests, "get", return_value=_Resp({}, status=404)):
            self.assertEqual(search_agent.fetch_ashby("X", "x"), [])

    def test_dispatch_routes_by_provider(self):
        payload = {"jobs": [{"title": "Role", "location": "Milan", "jobUrl": "u",
                             "descriptionHtml": "d"}]}
        with patch.object(search_agent.requests, "get", return_value=_Resp(payload)):
            jobs = search_agent.fetch_company(
                {"ats_provider": "ashby", "ats_id": "acme", "name": "Acme"})
        self.assertEqual(jobs[0]["job_board"], "ashby")
        # unknown provider → no fetch
        self.assertEqual(search_agent.fetch_company({"ats_provider": "nope"}), [])


if __name__ == "__main__":
    unittest.main()
