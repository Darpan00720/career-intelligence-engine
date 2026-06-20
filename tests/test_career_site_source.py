"""Tests for CareerSiteSource — JSON-LD JobPosting + RSS parsing. Network mocked."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from agents import career_site_search as css


class _Resp:
    def __init__(self, *, text="", content=b"", status=200):
        self.text = text
        self.content = content or text.encode("utf-8")
        self.status_code = status


_JSONLD_PAGE = """
<html><head>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "AI Product Manager Intern",
 "hiringOrganization": {"@type": "Organization", "name": "Zalando"},
 "jobLocation": {"@type": "Place", "address": {"addressLocality": "Berlin",
   "addressCountry": "DE"}},
 "url": "https://jobs.zalando.com/ai-pm-intern",
 "description": "<p>Join us</p>", "datePosted": "2026-06-10"}
</script>
</head><body>…</body></html>
"""

_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>People Analytics Intern</title>
  <link>https://acme.com/jobs/1</link>
  <description>Great role</description>
  <pubDate>2026-06-09</pubDate></item>
</channel></rss>"""


class TestJsonLd(unittest.TestCase):
    def test_extracts_and_normalizes_jobposting(self):
        with patch.object(css.requests, "get", return_value=_Resp(text=_JSONLD_PAGE)):
            jobs = css.fetch_jsonld("https://jobs.zalando.com", "Zalando")
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["title"], "AI Product Manager Intern")
        self.assertEqual(j["company"], "Zalando")
        self.assertEqual(j["location"], "Berlin, DE")
        self.assertEqual(j["job_board"], "career_site")
        self.assertEqual(j["url"], "https://jobs.zalando.com/ai-pm-intern")
        self.assertIn("Join us", j["description"])

    def test_handles_graph_and_list_wrappers(self):
        graph = {"@graph": [{"@type": "WebSite"},
                            {"@type": "JobPosting", "title": "Role A"}]}
        listed = [{"@type": "JobPosting", "title": "Role B"}]
        self.assertEqual([j["title"] for j in css._iter_jobpostings(graph)], ["Role A"])
        self.assertEqual([j["title"] for j in css._iter_jobpostings(listed)], ["Role B"])

    def test_bad_status_returns_empty(self):
        with patch.object(css.requests, "get", return_value=_Resp(status=404)):
            self.assertEqual(css.fetch_jsonld("https://x", "X"), [])


class TestRss(unittest.TestCase):
    def test_parses_items(self):
        with patch.object(css.requests, "get", return_value=_Resp(content=_RSS.encode())):
            jobs = css.fetch_rss("https://acme.com/feed.xml", "Acme")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "People Analytics Intern")
        self.assertEqual(jobs[0]["url"], "https://acme.com/jobs/1")
        self.assertEqual(jobs[0]["job_board"], "career_site_rss")


class TestSearchCareerSites(unittest.TestCase):
    def test_reads_config_and_dispatches(self):
        from pathlib import Path
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "data"), exist_ok=True)
        with open(os.path.join(tmp, "data", "career_sites.json"), "w") as f:
            json.dump([{"name": "Zalando", "url": "https://z", "type": "jsonld"}], f)
        with patch.object(css.config, "BASE_DIR", Path(tmp)), \
                patch.object(css.requests, "get", return_value=_Resp(text=_JSONLD_PAGE)):
            jobs = css.search_career_sites()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Zalando")

    def test_missing_config_returns_empty(self):
        from pathlib import Path
        with patch.object(css.config, "BASE_DIR", Path("/nonexistent")):
            self.assertEqual(css.search_career_sites(), [])


if __name__ == "__main__":
    unittest.main()
