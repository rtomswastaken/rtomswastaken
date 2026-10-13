#!/usr/bin/env python3
"""
Unit and Integration Tests for RTOMS WakaTime Activity Graph Generator.

Tests:
1. Representative WakaTime responses (both API summaries and public share JSON)
2. Empty activity days and zero handling
3. Missing or malformed API responses
4. Authentication failures (401, 403) and rate limits (429)
5. Date handling across 14-day window
6. SVG generation and XML validity
7. No-change detection avoiding unnecessary commits
8. Credential protection and masking
9. README image path validity
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

# Add scripts directory to path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from generate_activity_graph import (
    APIResponseError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    RateLimitError,
    ValidationError,
    compute_activity_hash,
    fetch_wakatime_data,
    format_duration,
    generate_svg,
    get_existing_svg_hash,
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

    # ----------------------------------------------------
    # TEST 1: Representative WakaTime Response Parsing
    # ----------------------------------------------------
    def test_parse_representative_response(self):
        parsed = parse_wakatime_data(self.sample_api_summaries, window_days=14, reference_date=self.ref_date)
        self.assertEqual(len(parsed["days"]), 14)
        self.assertEqual(parsed["window_days"], 14)
        self.assertEqual(parsed["start_date"], "2026-09-30")
        self.assertEqual(parsed["end_date"], "2026-10-13")

        # Today's hours (Oct 13): 19800s = 5.5h = "5h 30m"
        self.assertEqual(parsed["today_seconds"], 19800.0)
        self.assertEqual(parsed["today_formatted"], "5h 30m")

        # Total seconds: sum of all days
        self.assertGreater(parsed["total_seconds"], 0)
        self.assertIn("h", parsed["total_formatted"])
        self.assertIn("h", parsed["daily_avg_formatted"])

    # ----------------------------------------------------
    # TEST 2: Empty Activity Days & Zero Handling
    # ----------------------------------------------------
    def test_missing_and_empty_days_handled_as_zero(self):
        parsed = parse_wakatime_data(self.sample_api_summaries, window_days=14, reference_date=self.ref_date)
        # Oct 03 was missing from data list -> should be 0.0 seconds
        oct_03 = next(d for d in parsed["days"] if d["date"] == "2026-10-03")
        self.assertEqual(oct_03["seconds"], 0.0)
        self.assertEqual(oct_03["hours"], 0.0)
        self.assertEqual(oct_03["formatted"], "0m")

        # Oct 08 was explicitly 0 seconds in payload
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
    # TEST 5: Authentication and Error Handling
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
        svg = generate_svg(parsed)

        # Parse with XML parser to assert validity
        root = ET.fromstring(svg)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertEqual(root.attrib.get("viewBox"), "0 0 840 330")

        # Check required visual components
        self.assertIn("Daily Dev Time", svg)
        self.assertIn("14-DAY TOTAL", svg)
        self.assertIn("DAILY AVERAGE", svg)
        self.assertIn("TODAY", svg)
        self.assertIn("data-activity-hash", root.attrib)

        # Check for tooltips (title tags inside data dots)
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
            svg_1 = generate_svg(parsed, timestamp_override="2026-10-13 10:00 UTC")

            # First write: must succeed
            written = update_graph_file(svg_path, svg_1, force=False)
            self.assertTrue(written)

            # Second write with identical activity data: must be skipped
            svg_2 = generate_svg(parsed, timestamp_override="2026-10-13 11:00 UTC")
            written_second = update_graph_file(svg_path, svg_2, force=False)
            self.assertFalse(written_second)

            # Force flag overrides skip
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
        # Relative image target
        target_svg = os.path.join(REPO_ROOT, "assets", "coding-activity.svg")
        self.assertTrue(os.path.abspath(target_svg).startswith(REPO_ROOT))


if __name__ == "__main__":
    unittest.main()
