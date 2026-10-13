#!/usr/bin/env python3
"""
Unit and Integration Tests for RTOMS Activity Graph & GitHub Contribution Statistics Generator.

Tests:
1. Representative WakaTime responses (both API summaries and public share JSON)
2. Empty activity days and zero handling
3. Missing or malformed API responses
4. Authentication failures (401, 403) and rate limits (429)
5. Date handling across 14-day window
6. SVG generation and XML validity (including Daily Dev Time title and Contribution panel)
7. No-change detection avoiding unnecessary commits
8. Credential protection and masking
9. README image path validity
10. GitHub contribution totals and period ranges
11. Current streak calculations (active today, ongoing from yesterday, broken)
12. Longest streak calculations across multi-day periods
13. Empty contribution calendars
14. GitHub GraphQL API error handling (401, 403, 429, malformed response)
"""

import datetime
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import xml.etree.ElementTree as ET

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from generate_activity_graph import (
    APIResponseError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    GitHubAPIError,
    RateLimitError,
    ValidationError,
    compute_activity_hash,
    create_initial_placeholder_contributions,
    fetch_github_contributions,
    fetch_wakatime_data,
    format_duration,
    format_streak_range,
    generate_svg,
    get_existing_svg_hash,
    parse_github_contributions,
    parse_wakatime_data,
    sanitize_for_log,
    update_graph_file,
    validate_svg,
)


class TestWakaTimeActivityGraph(unittest.TestCase):

    def setUp(self):
        self.ref_date = datetime.date(2026, 10, 13)

        # 1. Representative WakaTime summaries response
        self.sample_api_summaries = {
            "data": [
                {
                    "range": {"date": "2026-09-30"},
                    "grand_total": {"total_seconds": 3600.0, "hours": 1, "minutes": 0},
                },
                {
                    "range": {"date": "2026-10-01"},
                    "grand_total": {"total_seconds": 7200.0, "hours": 2, "minutes": 0},
                },
                {
                    "range": {"date": "2026-10-02"},
                    "grand_total": {"total_seconds": 10800.0, "hours": 3, "minutes": 0},
                },
                # Note: Oct 03 missing - tests empty activity day handling
                {
                    "range": {"date": "2026-10-04"},
                    "grand_total": {"total_seconds": 1800.0, "hours": 0, "minutes": 30},
                },
                {
                    "range": {"date": "2026-10-05"},
                    "grand_total": {"total_seconds": 14400.0, "hours": 4, "minutes": 0},
                },
                {
                    "range": {"date": "2026-10-06"},
                    "grand_total": {"total_seconds": 9000.0, "hours": 2, "minutes": 30},
                },
                {
                    "range": {"date": "2026-10-07"},
                    "grand_total": {"total_seconds": 5400.0, "hours": 1, "minutes": 30},
                },
                {
                    "range": {"date": "2026-10-08"},
                    "grand_total": {"total_seconds": 0.0, "hours": 0, "minutes": 0},
                },
                {
                    "range": {"date": "2026-10-09"},
                    "grand_total": {"total_seconds": 16200.0, "hours": 4, "minutes": 30},
                },
                {
                    "range": {"date": "2026-10-10"},
                    "grand_total": {"total_seconds": 7200.0, "hours": 2, "minutes": 0},
                },
                {
                    "range": {"date": "2026-10-11"},
                    "grand_total": {"total_seconds": 8100.0, "hours": 2, "minutes": 15},
                },
                {
                    "range": {"date": "2026-10-12"},
                    "grand_total": {"total_seconds": 12600.0, "hours": 3, "minutes": 30},
                },
                {
                    "range": {"date": "2026-10-13"},
                    "grand_total": {"total_seconds": 19800.0, "hours": 5, "minutes": 30},
                },
            ]
        }

        # 2. Public Share JSON schema
        self.sample_share_json = {
            "data": [
                {"date": "2026-10-01", "total_seconds": 3600},
                {"date": "2026-10-02", "total_seconds": 7200},
                {"date": "2026-10-13", "total_seconds": 18000},
            ]
        }

        # 3. Representative GitHub GraphQL Contributions Response
        self.sample_github_graphql = {
            "data": {
                "user": {
                    "contributionsCollection": {
                        "contributionCalendar": {
                            "totalContributions": 154,
                            "weeks": [
                                {
                                    "contributionDays": [
                                        {"date": "2025-10-12", "contributionCount": 0},
                                        {"date": "2025-10-13", "contributionCount": 2},
                                        {"date": "2025-10-14", "contributionCount": 0},
                                    ]
                                },
                                {
                                    "contributionDays": [
                                        {"date": "2026-09-20", "contributionCount": 0},
                                        {"date": "2026-09-21", "contributionCount": 5},
                                        {"date": "2026-09-22", "contributionCount": 3},
                                        {"date": "2026-09-23", "contributionCount": 4},
                                        {"date": "2026-09-24", "contributionCount": 2},
                                        {"date": "2026-09-25", "contributionCount": 0},
                                    ]
                                },
                                {
                                    "contributionDays": [
                                        {"date": "2026-10-11", "contributionCount": 1},
                                        {"date": "2026-10-12", "contributionCount": 2},
                                        {"date": "2026-10-13", "contributionCount": 1},
                                        {"date": "2026-10-14", "contributionCount": 3},
                                    ]
                                },
                            ],
                        }
                    }
                }
            }
        }

    # ----------------------------------------------------
    # TEST 1: Representative WakaTime Response Parsing
    # ----------------------------------------------------
    def test_parse_representative_response(self):
        parsed = parse_wakatime_data(self.sample_api_summaries, window_days=14, reference_date=self.ref_date)
        self.assertEqual(len(parsed["days"]), 14)
        self.assertEqual(parsed["window_days"], 14)
        self.assertEqual(parsed["start_date"], "2026-09-30")
        self.assertEqual(parsed["end_date"], "2026-10-13")

        self.assertEqual(parsed["today_seconds"], 19800.0)
        self.assertEqual(parsed["today_formatted"], "5h 30m")
        self.assertGreater(parsed["total_seconds"], 0)
        self.assertIn("h", parsed["total_formatted"])
        self.assertIn("h", parsed["daily_avg_formatted"])

    # ----------------------------------------------------
    # TEST 2: Empty Activity Days & Zero Handling
    # ----------------------------------------------------
    def test_missing_and_empty_days_handled_as_zero(self):
        parsed = parse_wakatime_data(self.sample_api_summaries, window_days=14, reference_date=self.ref_date)
        oct_03 = next(d for d in parsed["days"] if d["date"] == "2026-10-03")
        self.assertEqual(oct_03["seconds"], 0.0)
        self.assertEqual(oct_03["hours"], 0.0)
        self.assertEqual(oct_03["formatted"], "0m")

        oct_08 = next(d for d in parsed["days"] if d["date"] == "2026-10-08")
        self.assertEqual(oct_08["seconds"], 0.0)
        self.assertEqual(oct_08["formatted"], "0m")

    def test_entirely_empty_dataset(self):
        empty_data = {"data": []}
        parsed = parse_wakatime_data(empty_data, window_days=14, reference_date=self.ref_date)
        self.assertEqual(len(parsed["days"]), 14)
        self.assertEqual(parsed["total_seconds"], 0.0)
        self.assertEqual(parsed["total_formatted"], "0m")
        self.assertEqual(parsed["daily_avg_formatted"], "0m")
        self.assertEqual(parsed["today_formatted"], "0m")
        for d in parsed["days"]:
            self.assertEqual(d["seconds"], 0.0)
            self.assertEqual(d["hours"], 0.0)

    # ----------------------------------------------------
    # TEST 3: Share embed JSON format support
    # ----------------------------------------------------
    def test_parse_share_embed_format(self):
        parsed = parse_wakatime_data(self.sample_share_json, window_days=14, reference_date=self.ref_date)
        self.assertEqual(len(parsed["days"]), 14)
        oct_13 = next(d for d in parsed["days"] if d["date"] == "2026-10-13")
        self.assertEqual(oct_13["seconds"], 18000.0)
        self.assertEqual(oct_13["formatted"], "5h")

    # ----------------------------------------------------
    # TEST 4: Missing or Malformed API Responses
    # ----------------------------------------------------
    def test_malformed_missing_data_key(self):
        with self.assertRaises(APIResponseError):
            parse_wakatime_data({"invalid": 123}, window_days=14)

    def test_malformed_non_list_data(self):
        with self.assertRaises(APIResponseError):
            parse_wakatime_data({"data": "not a list"}, window_days=14)

    # ----------------------------------------------------
    # TEST 5: Authentication and Error Handling (WakaTime)
    # ----------------------------------------------------
    def test_missing_credentials_raises_configuration_error(self):
        with self.assertRaises(ConfigurationError):
            fetch_wakatime_data(api_key=None, json_url=None)

    @patch("urllib.request.urlopen")
    def test_auth_failure_401_raises_authentication_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://wakatime.com",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=None,
        )
        with self.assertRaises(AuthenticationError):
            fetch_wakatime_data(api_key="invalid_test_key")

    @patch("urllib.request.urlopen")
    def test_auth_failure_403_raises_authorization_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://wakatime.com",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=None,
        )
        with self.assertRaises(AuthorizationError):
            fetch_wakatime_data(api_key="restricted_key")

    @patch("urllib.request.urlopen")
    def test_rate_limit_429_raises_rate_limit_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://wakatime.com",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=None,
        )
        with self.assertRaises(RateLimitError):
            fetch_wakatime_data(api_key="valid_key")

    # ----------------------------------------------------
    # TEST 6: SVG Generation and XML Validity
    # ----------------------------------------------------
    def test_svg_generation_valid_xml(self):
        parsed = parse_wakatime_data(self.sample_api_summaries, window_days=14, reference_date=self.ref_date)
        gh_parsed = parse_github_contributions(self.sample_github_graphql)
        svg = generate_svg(parsed, contrib_data=gh_parsed)

        root = ET.fromstring(svg)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertEqual(root.attrib.get("viewBox"), "0 0 840 490")

        # Visual components: Daily Dev Time and WakaTime stats
        self.assertIn("Daily Dev Time", svg)
        self.assertIn("14-DAY TOTAL", svg)
        self.assertIn("DAILY AVERAGE", svg)
        self.assertIn("TODAY", svg)
        self.assertIn("data-activity-hash", root.attrib)

        # Contribution statistics panel components
        self.assertIn("TOTAL CONTRIBUTIONS", svg)
        self.assertIn("CURRENT STREAK", svg)
        self.assertIn("LONGEST STREAK", svg)
        self.assertIn("154", svg)

        # Tooltips
        title_tags = root.findall(".//{http://www.w3.org/2000/svg}title")
        self.assertEqual(len(title_tags), 14)
        for t in title_tags:
            self.assertRegex(t.text, r"\d{4}-\d{2}-\d{2}: .+")

    def test_svg_validation_rejects_nan_or_bad_xml(self):
        with self.assertRaises(ValidationError):
            validate_svg("<svg>invalid <unclosed></svg>")
        with self.assertRaises(ValidationError):
            validate_svg('<svg viewBox="0 0 100 100"><text>NaN</text></svg>')

    # ----------------------------------------------------
    # TEST 7: No-Change Case & Commit Avoidance
    # ----------------------------------------------------
    def test_no_change_skips_file_rewrite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svg_path = os.path.join(tmpdir, "activity.svg")
            parsed = parse_wakatime_data(self.sample_api_summaries, window_days=14, reference_date=self.ref_date)
            gh_parsed = parse_github_contributions(self.sample_github_graphql)

            svg_1 = generate_svg(parsed, contrib_data=gh_parsed, timestamp_override="2026-10-13 10:00 UTC")

            written = update_graph_file(svg_path, svg_1, force=False)
            self.assertTrue(written)

            svg_2 = generate_svg(parsed, contrib_data=gh_parsed, timestamp_override="2026-10-13 11:00 UTC")
            written_second = update_graph_file(svg_path, svg_2, force=False)
            self.assertFalse(written_second)

            written_forced = update_graph_file(svg_path, svg_2, force=True)
            self.assertTrue(written_forced)

    # ----------------------------------------------------
    # TEST 8: Credential Sanitization
    # ----------------------------------------------------
    def test_credential_sanitization(self):
        secret = "waka_sec_a1b2c3d4e5f6789012345678"
        sanitized = sanitize_for_log(f"Fetching with key {secret}")
        self.assertNotIn("a1b2c3d4e5f6789012345678", sanitized)
        self.assertIn("***REDACTED***", sanitized)

    # ----------------------------------------------------
    # TEST 9: Duration Formatting Helper
    # ----------------------------------------------------
    def test_format_duration(self):
        self.assertEqual(format_duration(0), "0m")
        self.assertEqual(format_duration(30), "< 1m")
        self.assertEqual(format_duration(1800), "30m")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(5400), "1h 30m")
        self.assertEqual(format_duration(7200), "2h")
        self.assertEqual(format_duration(8100), "2h 15m")

    # ----------------------------------------------------
    # TEST 10: README Image Path and Integration
    # ----------------------------------------------------
    def test_readme_exists_and_relative_assets_path(self):
        readme_path = os.path.join(REPO_ROOT, "README.md")
        self.assertTrue(os.path.exists(readme_path), "README.md must exist in repository root")
        target_svg = os.path.join(REPO_ROOT, "assets", "coding-activity.svg")
        self.assertTrue(os.path.abspath(target_svg).startswith(REPO_ROOT))

    # ----------------------------------------------------
    # TEST 11: GitHub Contributions Parsing & Totals
    # ----------------------------------------------------
    def test_github_contributions_totals_and_period(self):
        gh_parsed = parse_github_contributions(self.sample_github_graphql)
        self.assertEqual(gh_parsed["total"], 154)
        self.assertIn("Oct 12, 2025", gh_parsed["period_range"])
        self.assertIn("Present", gh_parsed["period_range"])

    # ----------------------------------------------------
    # TEST 12: Current & Longest Streak Calculations
    # ----------------------------------------------------
    def test_github_streak_calculations(self):
        gh_parsed = parse_github_contributions(self.sample_github_graphql)
        # Current streak: Oct 11 to Oct 14 (4 days)
        self.assertEqual(gh_parsed["current_streak"], 4)
        self.assertEqual(gh_parsed["current_range"], "Oct 11 - Oct 14")

        # Longest streak: 4 days (Sep 21-24 or Oct 11-14)
        self.assertEqual(gh_parsed["longest_streak"], 4)
        self.assertIn(gh_parsed["longest_range"], ["Sep 21 - Sep 24", "Oct 11 - Oct 14"])

    def test_github_streak_ongoing_from_yesterday(self):
        # Scenario: Today (Oct 15) has 0 contributions so far, but yesterday (Oct 14) had 2.
        # Active streak should be preserved as 4 days ending yesterday!
        payload = {
            "data": {
                "user": {
                    "contributionsCollection": {
                        "contributionCalendar": {
                            "totalContributions": 10,
                            "weeks": [
                                {
                                    "contributionDays": [
                                        {"date": "2026-10-11", "contributionCount": 1},
                                        {"date": "2026-10-12", "contributionCount": 1},
                                        {"date": "2026-10-13", "contributionCount": 1},
                                        {"date": "2026-10-14", "contributionCount": 1},
                                        {"date": "2026-10-15", "contributionCount": 0},  # today in progress
                                    ]
                                }
                            ]
                        }
                    }
                }
            }
        }
        res = parse_github_contributions(payload)
        self.assertEqual(res["current_streak"], 4)
        self.assertEqual(res["current_range"], "Oct 11 - Oct 14")

    def test_github_streak_broken(self):
        # Scenario: Both yesterday and today have 0 contributions -> streak is broken (0 days)
        payload = {
            "data": {
                "user": {
                    "contributionsCollection": {
                        "contributionCalendar": {
                            "totalContributions": 5,
                            "weeks": [
                                {
                                    "contributionDays": [
                                        {"date": "2026-10-11", "contributionCount": 1},
                                        {"date": "2026-10-12", "contributionCount": 0},
                                        {"date": "2026-10-13", "contributionCount": 0},
                                    ]
                                }
                            ]
                        }
                    }
                }
            }
        }
        res = parse_github_contributions(payload)
        self.assertEqual(res["current_streak"], 0)
        self.assertEqual(res["current_range"], "None")

    # ----------------------------------------------------
    # TEST 13: Empty Contribution Calendar
    # ----------------------------------------------------
    def test_github_empty_contributions(self):
        payload = {
            "data": {
                "user": {
                    "contributionsCollection": {
                        "contributionCalendar": {
                            "totalContributions": 0,
                            "weeks": []
                        }
                    }
                }
            }
        }
        res = parse_github_contributions(payload)
        self.assertEqual(res["total"], 0)
        self.assertEqual(res["current_streak"], 0)
        self.assertEqual(res["longest_streak"], 0)
        self.assertEqual(res["period_range"], "None")

    # ----------------------------------------------------
    # TEST 14: GitHub API Error Handling
    # ----------------------------------------------------
    def test_github_missing_token_raises_configuration_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("generate_activity_graph.get_fallback_github_token", return_value=None):
                with self.assertRaises(ConfigurationError):
                    fetch_github_contributions(token=None)

    @patch("generate_activity_graph._open_url_safely")
    def test_github_api_401_raises_authentication_error(self, mock_open):
        import urllib.error
        mock_open.side_effect = urllib.error.HTTPError(
            url="https://api.github.com/graphql",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=None,
        )
        with self.assertRaises(AuthenticationError):
            fetch_github_contributions(token="invalid_token")

    @patch("generate_activity_graph._open_url_safely")
    def test_github_api_429_raises_rate_limit_error(self, mock_open):
        import urllib.error
        mock_open.side_effect = urllib.error.HTTPError(
            url="https://api.github.com/graphql",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=None,
        )
        with self.assertRaises(RateLimitError):
            fetch_github_contributions(token="valid_token")

    @patch("generate_activity_graph._open_url_safely")
    def test_github_api_graphql_errors(self, mock_open):
        mock_open.return_value = json.dumps({"errors": [{"message": "Field 'user' does not exist"}]}).encode("utf-8")
        with self.assertRaises(GitHubAPIError):
            fetch_github_contributions(token="test_token")


if __name__ == "__main__":
    unittest.main()
