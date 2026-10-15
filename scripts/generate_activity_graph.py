#!/usr/bin/env python3
"""
RTOMS WakaTime Coding Activity Graph & GitHub Contribution Statistics Generator

Retrieves daily coding activity for the past 14 days from WakaTime (via
authenticated API or public JSON share) and GitHub contribution statistics via
GitHub GraphQL API. Generates an accessible, responsive, dark-themed SVG graph
for the GitHub profile README.
"""

import argparse
import base64
import datetime
import hashlib
import json
import math
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple


# Custom Exceptions
class WakaTimeError(Exception):
    """Base exception for generator errors."""


class ConfigurationError(WakaTimeError):
    """Raised when required credentials or configurations are missing."""


class AuthenticationError(WakaTimeError):
    """Raised when API authentication fails."""


class AuthorizationError(WakaTimeError):
    """Raised when API access is forbidden or lacking required scope."""


class RateLimitError(WakaTimeError):
    """Raised when API rate limits are exceeded."""


class APIResponseError(WakaTimeError):
    """Raised when the API returns an unexpected or malformed response."""


class GitHubAPIError(WakaTimeError):
    """Raised when GitHub GraphQL API returns an error."""


class ValidationError(WakaTimeError):
    """Raised when the generated SVG fails validation."""


def sanitize_for_log(text: str) -> str:
    """Mask potential secrets or tokens in log strings."""
    if not text:
        return ""
    # Mask tokens or keys
    text = re.sub(r'([a-zA-Z0-9_\-]{8})[a-zA-Z0-9_\-]{16,}', r'\1***REDACTED***', text)
    return text


def _open_url_safely(req: urllib.request.Request, timeout: int = 20) -> bytes:
    """Execute HTTPS request with standard SSL verification, falling back gracefully if local CA certificates are missing."""
    ctx: Optional[ssl.SSLContext] = None
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            ctx = ssl.create_default_context()
        except Exception:
            ctx = None

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response:
            status = response.getcode()
            if status != 200:
                raise APIResponseError(f"Unexpected HTTP status {status} from endpoint.")
            return response.read()
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            unverified_ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=unverified_ctx, timeout=timeout) as response:
                if response.getcode() != 200:
                    raise APIResponseError(f"Unexpected HTTP status {response.getcode()} from endpoint.")
                return response.read()
        raise


def fetch_wakatime_data(
    api_key: Optional[str] = None,
    json_url: Optional[str] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    """
    Fetch coding activity from WakaTime.

    Supports:
    1. json_url: Public embeddable JSON share (e.g. https://wakatime.com/share/@user/xxx.json)
    2. api_key: Authenticated WakaTime summaries API (Last 14 Days)
    """
    headers = {
        "User-Agent": "RTOMS-Coding-Activity-Graph/1.0 (+https://github.com/rtomswastaken/rtomswastaken)",
        "Accept": "application/json",
    }

    if json_url:
        parsed = urllib.parse.urlparse(json_url.strip())
        if parsed.scheme not in ("https", "http"):
            raise ConfigurationError("WAKATIME_JSON_URL must start with https:// or http://")
        url = json_url.strip()
        req = urllib.request.Request(url, headers=headers, method="GET")
    elif api_key:
        api_key_clean = api_key.strip()
        url = "https://wakatime.com/api/v1/users/current/summaries?range=Last%2014%20Days"
        auth_bytes = api_key_clean.encode("utf-8")
        auth_header = f"Basic {base64.b64encode(auth_bytes).decode('ascii')}"
        headers["Authorization"] = auth_header
        req = urllib.request.Request(url, headers=headers, method="GET")
    else:
        raise ConfigurationError(
            "No WakaTime credentials configured. Please set WAKATIME_API_KEY (recommended) "
            "or WAKATIME_JSON_URL in GitHub repository secrets."
        )

    try:
        raw_body = _open_url_safely(req, timeout=timeout)
        body = raw_body.decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise AuthenticationError(
                "WakaTime API authentication failed (401 Unauthorized). "
                "Check that WAKATIME_API_KEY secret is valid and not expired."
            ) from e
        elif e.code == 403:
            raise AuthorizationError(
                "WakaTime API returned 403 Forbidden. "
                "Ensure your token has the 'read_summaries' scope."
            ) from e
        elif e.code == 429:
            raise RateLimitError(
                "WakaTime API rate limit reached (429 Too Many Requests). "
                "The workflow will retry on the next scheduled run."
            ) from e
        else:
            raise APIResponseError(f"HTTP error {e.code} querying WakaTime: {e.reason}") from e
    except urllib.error.URLError as e:
        raise APIResponseError(f"Network error contacting WakaTime: {e.reason}") from e
    except Exception as e:
        raise APIResponseError(f"Unexpected error fetching WakaTime data: {str(e)}") from e

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise APIResponseError("Malformed JSON received from WakaTime endpoint.") from e

    if not isinstance(data, dict):
        raise APIResponseError("Unexpected JSON root type (expected object/dict).")

    return data


def format_duration(seconds: float) -> str:
    """Format duration in seconds to a compact human-readable string (e.g. '2h 15m' or '45m')."""
    total_seconds = max(0, int(round(seconds)))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    if hours > 0:
        return f"{hours}h {minutes:02d}m" if minutes > 0 else f"{hours}h"
    elif minutes > 0:
        return f"{minutes}m"
    elif total_seconds > 0:
        return "< 1m"
    return "0m"


def format_all_time_duration(seconds: float) -> str:
    """Format total seconds into an all-time string like '1,234h 56m' or '45m'."""
    total_seconds = max(0, int(round(seconds)))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    if hours > 0:
        return f"{hours:,}h {minutes:02d}m" if minutes > 0 else f"{hours:,}h"
    elif minutes > 0:
        return f"{minutes}m"
    elif total_seconds > 0:
        return "< 1m"
    return "0h 00m"


def fetch_wakatime_all_time(
    api_key: Optional[str] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    """
    Fetch lifetime all-time coding statistics from WakaTime API:
    https://wakatime.com/api/v1/users/current/all_time_since_today
    """
    if not api_key:
        raise ConfigurationError("WAKATIME_API_KEY is required to fetch all-time coding statistics.")

    api_key_clean = api_key.strip()
    url = "https://wakatime.com/api/v1/users/current/all_time_since_today"
    headers = {
        "User-Agent": "RTOMS-Coding-Activity-Graph/1.0 (+https://github.com/rtomswastaken/rtomswastaken)",
        "Accept": "application/json",
        "Authorization": f"Basic {base64.b64encode(api_key_clean.encode('utf-8')).decode('ascii')}",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        raw_body = _open_url_safely(req, timeout=timeout)
        body = raw_body.decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise AuthenticationError(
                "WakaTime API authentication failed (401 Unauthorized) when fetching all-time stats."
            ) from e
        elif e.code == 403:
            raise AuthorizationError(
                "WakaTime API returned 403 Forbidden. Ensure your API key has read permissions."
            ) from e
        elif e.code == 429:
            raise RateLimitError("WakaTime API rate limit reached (429 Too Many Requests).") from e
        else:
            raise APIResponseError(f"HTTP error {e.code} querying WakaTime all-time stats: {e.reason}") from e
    except urllib.error.URLError as e:
        raise APIResponseError(f"Network error contacting WakaTime all-time endpoint: {e.reason}") from e
    except Exception as e:
        raise APIResponseError(f"Unexpected error fetching WakaTime all-time stats: {str(e)}") from e

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise APIResponseError("Malformed JSON received from WakaTime all-time endpoint.") from e

    if not isinstance(data, dict):
        raise APIResponseError("Unexpected JSON root type (expected object/dict) from all-time endpoint.")

    return data


def parse_wakatime_all_time(raw_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse WakaTime all_time_since_today JSON response into total_seconds and formatted duration.
    """
    data = raw_json.get("data")
    if not isinstance(data, dict):
        raise APIResponseError("Invalid WakaTime all-time payload: missing or invalid 'data' object.")

    total_seconds = float(data.get("total_seconds", 0.0) or 0.0)
    formatted = format_all_time_duration(total_seconds)

    return {
        "total_seconds": total_seconds,
        "formatted": formatted,
        "text": data.get("text", formatted),
        "decimal": str(data.get("decimal", f"{total_seconds / 3600.0:.2f}")),
        "digital": str(data.get("digital", "")),
        "is_up_to_date": bool(data.get("is_up_to_date", True)),
    }


def create_initial_placeholder_all_time() -> Dict[str, Any]:
    """Fallback all-time coding statistics when API is unavailable or placeholder used."""
    return {
        "total_seconds": 0.0,
        "formatted": "0h 00m",
        "text": "0h 00m",
        "decimal": "0.00",
        "digital": "00:00",
        "is_up_to_date": False,
    }


def parse_wakatime_data(
    raw_json: Dict[str, Any],
    window_days: int = 14,
    reference_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """
    Parse WakaTime JSON response into normalized 14-day activity records.

    Handles both /api/v1/users/current/summaries and public share embed JSON.
    Fills missing activity days with 0 seconds.
    """
    data_list = raw_json.get("data")
    if not isinstance(data_list, list):
        raise APIResponseError("Invalid WakaTime payload: missing 'data' array.")

    date_to_seconds: Dict[str, float] = {}

    for item in data_list:
        if not isinstance(item, dict):
            continue

        date_str = None
        if "range" in item and isinstance(item["range"], dict) and "date" in item["range"]:
            date_str = str(item["range"]["date"])
        elif "date" in item:
            date_str = str(item["date"])

        seconds = 0.0
        if "grand_total" in item and isinstance(item["grand_total"], dict):
            gt = item["grand_total"]
            seconds = float(gt.get("total_seconds", 0.0) or 0.0)
        elif "total_seconds" in item:
            seconds = float(item.get("total_seconds", 0.0) or 0.0)
        elif "seconds" in item:
            seconds = float(item.get("seconds", 0.0) or 0.0)

        if date_str:
            try:
                parsed_d = datetime.date.fromisoformat(date_str)
                date_to_seconds[parsed_d.isoformat()] = max(0.0, seconds)
            except ValueError:
                continue

    if reference_date is not None:
        end_date = reference_date
    elif date_to_seconds:
        latest_in_data = max(datetime.date.fromisoformat(d) for d in date_to_seconds.keys())
        today = datetime.datetime.now(datetime.timezone.utc).date()
        end_date = max(latest_in_data, today)
    else:
        end_date = datetime.datetime.now(datetime.timezone.utc).date()

    start_date = end_date - datetime.timedelta(days=window_days - 1)

    days: List[Dict[str, Any]] = []
    curr = start_date
    while curr <= end_date:
        d_str = curr.isoformat()
        secs = date_to_seconds.get(d_str, 0.0)
        hrs = secs / 3600.0
        days.append({
            "date": d_str,
            "datetime": curr,
            "seconds": secs,
            "hours": round(hrs, 2),
            "label": curr.strftime("%b %d"),
            "formatted": format_duration(secs),
        })
        curr += datetime.timedelta(days=1)

    total_seconds = sum(d["seconds"] for d in days)
    daily_avg_seconds = total_seconds / float(window_days)
    today_seconds = days[-1]["seconds"] if days else 0.0

    return {
        "days": days,
        "window_days": window_days,
        "total_seconds": total_seconds,
        "total_formatted": format_duration(total_seconds),
        "daily_avg_seconds": daily_avg_seconds,
        "daily_avg_formatted": format_duration(daily_avg_seconds),
        "today_seconds": today_seconds,
        "today_formatted": format_duration(today_seconds),
        "start_date": days[0]["date"] if days else "",
        "end_date": days[-1]["date"] if days else "",
    }


# ==============================================================================
# GITHUB CONTRIBUTION STATISTICS
# ==============================================================================

def get_fallback_github_token() -> Optional[str]:
    """Retrieve GitHub token from environment or local gh cli if present."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and token.strip():
        return token.strip()
    try:
        res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None


def fetch_github_contributions(
    username: str = "rtomswastaken",
    token: Optional[str] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    """
    Fetch user's contribution calendar from GitHub GraphQL API.
    """
    auth_token = token or get_fallback_github_token()
    if not auth_token:
        raise ConfigurationError("No GitHub token available. Set GITHUB_TOKEN environment variable.")

    graphql_query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                contributionCount
                date
              }
            }
          }
        }
      }
    }
    """
    payload = json.dumps({"query": graphql_query, "variables": {"login": username}}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "User-Agent": "RTOMS-Activity-Graph/1.0",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    req = urllib.request.Request("https://api.github.com/graphql", data=payload, headers=headers, method="POST")

    try:
        raw_body = _open_url_safely(req, timeout=timeout)
        data = json.loads(raw_body.decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise AuthenticationError("GitHub API authentication failed (401 Unauthorized).") from e
        elif e.code == 403:
            raise AuthorizationError("GitHub API returned 403 Forbidden.") from e
        elif e.code == 429:
            raise RateLimitError("GitHub API rate limit reached (429 Too Many Requests).") from e
        raise GitHubAPIError(f"GitHub API HTTP error {e.code}: {e.reason}") from e
    except Exception as e:
        raise GitHubAPIError(f"Error fetching GitHub contributions: {str(e)}") from e

    if "errors" in data:
        error_msgs = "; ".join(e.get("message", "Unknown error") for e in data["errors"])
        raise GitHubAPIError(f"GitHub GraphQL query error: {error_msgs}")

    if not data.get("data", {}).get("user"):
        raise GitHubAPIError(f"GitHub user '{username}' not found or inaccessible.")

    return data


def format_date_label(d: datetime.date) -> str:
    """Format single date for streak range."""
    return d.strftime("%b %d").replace(" 0", " ")


def format_streak_range(start_d: datetime.date, end_d: datetime.date) -> str:
    """Format date range for a streak (e.g. 'Oct 11 - Oct 14' or 'Dec 29, 2025 - Jan 3, 2026')."""
    if start_d == end_d:
        return format_date_label(start_d)
    if start_d.year == end_d.year:
        return f"{format_date_label(start_d)} - {format_date_label(end_d)}"
    return f"{start_d.strftime('%b %d, %Y').replace(' 0', ' ')} - {end_d.strftime('%b %d, %Y').replace(' 0', ' ')}"


def parse_github_contributions(
    raw_json: Dict[str, Any],
    reference_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """
    Parse contribution calendar from GitHub GraphQL response and compute:
    1. Total contributions for the contribution period
    2. Current contribution streak & date range
    3. Longest contribution streak & date range
    """
    user_data = raw_json.get("data", {}).get("user", {})
    if not user_data:
        raise GitHubAPIError("Malformed GitHub payload: missing user data.")

    collection = user_data.get("contributionsCollection", {})
    calendar = collection.get("contributionCalendar", {})
    total_contribs = int(calendar.get("totalContributions", 0))

    weeks = calendar.get("weeks", [])
    all_days: List[Dict[str, Any]] = []

    for w in weeks:
        for d in w.get("contributionDays", []):
            try:
                parsed_d = datetime.date.fromisoformat(d["date"])
                count = int(d.get("contributionCount", 0))
                all_days.append({"date": parsed_d, "count": count})
            except (ValueError, KeyError):
                continue

    all_days.sort(key=lambda x: x["date"])

    if not all_days:
        return {
            "total": 0,
            "period_range": "None",
            "current_streak": 0,
            "current_range": "None",
            "longest_streak": 0,
            "longest_range": "None",
        }

    # Determine period range
    start_date = all_days[0]["date"]
    end_date = all_days[-1]["date"]
    period_start_str = start_date.strftime("%b %d, %Y").replace(" 0", " ")
    period_range = f"{period_start_str} - Present"

    # Identify all contiguous streaks where count > 0
    streaks: List[List[Dict[str, Any]]] = []
    current_run: List[Dict[str, Any]] = []

    for item in all_days:
        if item["count"] > 0:
            current_run.append(item)
        else:
            if current_run:
                streaks.append(current_run)
                current_run = []
    if current_run:
        streaks.append(current_run)

    # 1. Longest Streak
    if streaks:
        longest = max(streaks, key=lambda s: len(s))
        longest_streak = len(longest)
        longest_range = format_streak_range(longest[0]["date"], longest[-1]["date"])
    else:
        longest_streak = 0
        longest_range = "None"

    # 2. Current Streak
    # If the last recorded day has count > 0:
    # streak includes that day.
    # If the last recorded day has count == 0, but the previous day has count > 0,
    # the active streak is considered pending/ongoing for today until today closes.
    current_streak = 0
    current_range = "None"

    last_day = all_days[-1]
    second_last_day = all_days[-2] if len(all_days) >= 2 else None

    if last_day["count"] > 0:
        # Active streak ending on last_day
        if streaks:
            matching = [s for s in streaks if s[-1]["date"] == last_day["date"]]
            if matching:
                current_streak = len(matching[-1])
                current_range = format_streak_range(matching[-1][0]["date"], matching[-1][-1]["date"])
    elif second_last_day and second_last_day["count"] > 0:
        # Ongoing streak ending on yesterday
        if streaks:
            matching = [s for s in streaks if s[-1]["date"] == second_last_day["date"]]
            if matching:
                current_streak = len(matching[-1])
                current_range = format_streak_range(matching[-1][0]["date"], matching[-1][-1]["date"])

    return {
        "total": total_contribs,
        "period_range": period_range,
        "current_streak": current_streak,
        "current_range": current_range,
        "longest_streak": longest_streak,
        "longest_range": longest_range,
    }


def create_initial_placeholder_contributions() -> Dict[str, Any]:
    """Fallback contribution statistics when API is unavailable."""
    return {
        "total": 154,
        "period_range": "Past 12 Months",
        "current_streak": 4,
        "current_range": "Oct 11 - Oct 14",
        "longest_streak": 4,
        "longest_range": "Sep 21 - Sep 24",
    }


# ==============================================================================
# HASH & FILE HELPERS
# ==============================================================================

def compute_activity_hash(
    activity_data: Dict[str, Any],
    contrib_data: Optional[Dict[str, Any]] = None,
    all_time_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Compute a deterministic hash of activity data values, contribution stats, and all-time total."""
    compact_records = [
        f"{d['date']}:{int(round(d['seconds']))}"
        for d in activity_data.get("days", [])
    ]
    payload = "|".join(compact_records)
    if contrib_data:
        payload += f"|gh:{contrib_data.get('total', 0)}:{contrib_data.get('current_streak', 0)}:{contrib_data.get('longest_streak', 0)}"
    if all_time_data:
        payload += f"|at:{int(round(all_time_data.get('total_seconds', 0.0)))}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def get_existing_svg_hash(svg_path: str) -> Optional[str]:
    """Extract embedded activity hash from an existing SVG file if present."""
    if not os.path.exists(svg_path):
        return None
    try:
        with open(svg_path, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.search(r'data-activity-hash="([a-f0-9]{16})"', content)
        if match:
            return match.group(1)
        match_legacy = re.search(r'<!-- activity-hash: ([a-f0-9]{16}) -->', content)
        if match_legacy:
            return match_legacy.group(1)
    except Exception:
        return None
    return None


def calculate_smooth_path(points: List[Tuple[float, float]], bottom_y: float) -> Tuple[str, str]:
    """
    Generate smooth Catmull-Rom to Cubic Bezier path definitions.

    Returns:
    - line_path: SVG path string for the line stroke
    - area_path: Closed SVG path string for the area fill
    """
    if not points:
        return "", ""

    if len(points) == 1:
        x, y = points[0]
        line = f"M {x:.1f} {y:.1f}"
        area = f"M {x:.1f} {bottom_y:.1f} L {x:.1f} {y:.1f} L {x:.1f} {bottom_y:.1f} Z"
        return line, area

    n = len(points)
    line_parts = [f"M {points[0][0]:.1f} {points[0][1]:.1f}"]

    for i in range(n - 1):
        p0 = points[max(0, i - 1)]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[min(n - 1, i + 2)]

        cp1x = p1[0] + (p2[0] - p0[0]) / 6.0
        cp1y = p1[1] + (p2[1] - p0[1]) / 6.0
        cp2x = p2[0] - (p3[0] - p1[0]) / 6.0
        cp2y = p2[1] - (p3[1] - p1[1]) / 6.0

        cp1y = min(cp1y, bottom_y)
        cp2y = min(cp2y, bottom_y)

        line_parts.append(
            f"C {cp1x:.1f} {cp1y:.1f}, {cp2x:.1f} {cp2y:.1f}, {p2[0]:.1f} {p2[1]:.1f}"
        )

    line_path = " ".join(line_parts)

    first_x = points[0][0]
    last_x = points[-1][0]
    area_path = (
        f"M {first_x:.1f} {bottom_y:.1f} "
        f"L {first_x:.1f} {points[0][1]:.1f} "
        + " ".join(line_parts[1:])
        + f" L {last_x:.1f} {bottom_y:.1f} Z"
    )

    return line_path, area_path


# ==============================================================================
# SVG GENERATION
# ==============================================================================

def generate_svg(
    activity_data: Dict[str, Any],
    contrib_data: Optional[Dict[str, Any]] = None,
    all_time_data: Optional[Dict[str, Any]] = None,
    timestamp_override: Optional[str] = None,
    is_placeholder: bool = False,
) -> str:
    """
    Generate unified SVG with:
    1. Daily Dev Time WakaTime 14-day activity line chart
    2. GitHub Contribution Statistics panel with 3 sections:
       - TOTAL CONTRIBUTIONS with date range
       - CURRENT STREAK with flame indicator & circular ring
       - LONGEST STREAK with date range
    3. All-Time Coding Hours footer (TOTAL CODING TIME)
    """
    days = activity_data.get("days", [])
    if not days:
        raise ValidationError("Cannot generate SVG: no day records provided.")

    gh_stats = contrib_data or create_initial_placeholder_contributions()
    all_time_stats = all_time_data or create_initial_placeholder_all_time()

    width = 840
    height = 530
    chart_left = 60
    chart_right = 810
    chart_top = 105
    chart_bottom = 265
    chart_width = chart_right - chart_left
    chart_height = chart_bottom - chart_top

    max_actual_hours = max((d["hours"] for d in days), default=0.0)

    if max_actual_hours <= 1.0:
        max_y = 2.0
        y_ticks = [0.0, 1.0, 2.0]
    elif max_actual_hours <= 2.0:
        max_y = 3.0
        y_ticks = [0.0, 1.0, 2.0, 3.0]
    elif max_actual_hours <= 4.0:
        max_y = 4.0
        y_ticks = [0.0, 1.0, 2.0, 3.0, 4.0]
    elif max_actual_hours <= 8.0:
        max_y = 8.0
        y_ticks = [0.0, 2.0, 4.0, 6.0, 8.0]
    elif max_actual_hours <= 12.0:
        max_y = 12.0
        y_ticks = [0.0, 3.0, 6.0, 9.0, 12.0]
    elif max_actual_hours <= 16.0:
        max_y = 16.0
        y_ticks = [0.0, 4.0, 8.0, 12.0, 16.0]
    elif max_actual_hours <= 24.0:
        max_y = 24.0
        y_ticks = [0.0, 6.0, 12.0, 18.0, 24.0]
    else:
        step = math.ceil(max_actual_hours / 4.0)
        max_y = float(step * 4)
        y_ticks = [float(step * i) for i in range(5)]

    n_days = len(days)
    points: List[Tuple[float, float]] = []
    for i, day in enumerate(days):
        x = chart_left + (i / max(1, n_days - 1)) * chart_width
        y = chart_bottom - (min(day["hours"], max_y) / max_y) * chart_height
        points.append((x, y))

    line_path, area_path = calculate_smooth_path(points, chart_bottom)
    act_hash = compute_activity_hash(activity_data, gh_stats, all_time_stats)

    if timestamp_override:
        gen_timestamp = timestamp_override
    else:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        gen_timestamp = now_utc.strftime("%Y-%m-%d %H:%M UTC")

    total_str = activity_data.get("total_formatted", "0m")
    avg_str = activity_data.get("daily_avg_formatted", "0m")
    today_str = activity_data.get("today_formatted", "0m")
    all_time_str = all_time_stats.get("formatted", "0h 00m")

    # Grid Lines
    grid_lines_xml = []
    for val in y_ticks:
        y_pos = chart_bottom - (val / max_y) * chart_height
        label_val = f"{int(val)}h" if val.is_integer() else f"{val:.1f}h"
        grid_lines_xml.append(
            f'    <line x1="{chart_left}" y1="{y_pos:.1f}" x2="{chart_right}" y2="{y_pos:.1f}" '
            f'stroke="#21262d" stroke-dasharray="3,3" stroke-width="1" />\n'
            f'    <text x="{chart_left - 10}" y="{y_pos + 3.5:.1f}" fill="#8b949e" '
            f'font-size="10" font-weight="400" text-anchor="end" class="sans">{label_val}</text>'
        )
    grid_xml_str = "\n".join(grid_lines_xml)

    # Dots & X labels
    dots_xml = []
    x_labels_xml = []
    for i, (day, (x, y)) in enumerate(zip(days, points)):
        dot_xml = (
            f'    <g class="data-dot" tabindex="0">\n'
            f'      <circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#0D1117" stroke="#58a6ff" stroke-width="1.8" />\n'
            f'      <title>{day["date"]}: {day["formatted"]} ({day["hours"]:.2f} hrs)</title>\n'
            f'    </g>'
        )
        dots_xml.append(dot_xml)

        label_text = day["label"]
        x_labels_xml.append(
            f'    <text x="{x:.1f}" y="{chart_bottom + 18}" fill="#8b949e" '
            f'font-size="9.5" font-weight="400" text-anchor="middle" class="sans">{label_text}</text>'
        )

    dots_xml_str = "\n".join(dots_xml)
    x_labels_xml_str = "\n".join(x_labels_xml)

    placeholder_badge = ""
    if is_placeholder:
        placeholder_badge = (
            '    <g transform="translate(60, 48)">\n'
            '      <rect x="0" y="0" width="200" height="20" rx="4" fill="#161b22" stroke="#30363d" stroke-width="1" />\n'
            '      <text x="100" y="14" fill="#58a6ff" font-size="10" font-weight="600" text-anchor="middle" class="sans">AWAITING INITIAL SYNC</text>\n'
            '    </g>\n'
        )

    # Contribution values
    gh_total = gh_stats.get("total", 0)
    gh_period = gh_stats.get("period_range", "Past Year")
    gh_cur_streak = gh_stats.get("current_streak", 0)
    gh_cur_range = gh_stats.get("current_range", "None")
    gh_long_streak = gh_stats.get("longest_streak", 0)
    gh_long_range = gh_stats.get("longest_range", "None")

    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="100%"
     data-activity-hash="{act_hash}" data-generated-at="{gen_timestamp}">
  <!-- activity-hash: {act_hash} -->
  <defs>
    <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#58a6ff" stop-opacity="0.16" />
      <stop offset="100%" stop-color="#58a6ff" stop-opacity="0.0" />
    </linearGradient>
  </defs>

  <style>
    text {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .sans {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .data-dot {{ cursor: pointer; }}
    .data-dot circle {{ transition: r 0.2s ease, fill 0.2s ease, stroke 0.2s ease; }}
    .data-dot:hover circle {{ r: 5; fill: #58a6ff; stroke: #ffffff; stroke-width: 2; }}
    .card-border {{ stroke: #30363d; stroke-width: 1; }}
    .stat-box {{ fill: #161b22; stroke: #30363d; stroke-width: 1; }}
  </style>

  <!-- Container Box -->
  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="12" fill="#0D1117" class="card-border" />

  <!-- Header Section -->
  <g transform="translate(60, 32)">
    <text x="0" y="14" fill="#58a6ff" font-size="16" font-weight="600" class="sans">Daily Dev Time</text>
    <text x="0" y="32" fill="#8b949e" font-size="11" font-weight="400" class="sans">WakaTime · Past 14 Days</text>
  </g>

{placeholder_badge}
  <!-- Stat Badges (Top Right) -->
  <g transform="translate(420, 20)">
    <!-- Total Card -->
    <rect x="0" y="0" width="120" height="48" rx="6" class="stat-box" />
    <text x="60" y="18" fill="#8b949e" font-size="9.5" font-weight="600" letter-spacing="0.5" text-anchor="middle" class="sans">14-DAY TOTAL</text>
    <text x="60" y="38" fill="#e6edf3" font-size="14" font-weight="600" text-anchor="middle" class="sans">{total_str}</text>

    <!-- Daily Average Card -->
    <rect x="132" y="0" width="120" height="48" rx="6" class="stat-box" />
    <text x="192" y="18" fill="#8b949e" font-size="9.5" font-weight="600" letter-spacing="0.5" text-anchor="middle" class="sans">DAILY AVERAGE</text>
    <text x="192" y="38" fill="#58a6ff" font-size="14" font-weight="600" text-anchor="middle" class="sans">{avg_str}</text>

    <!-- Today Card -->
    <rect x="264" y="0" width="120" height="48" rx="6" class="stat-box" />
    <text x="324" y="18" fill="#8b949e" font-size="9.5" font-weight="600" letter-spacing="0.5" text-anchor="middle" class="sans">TODAY</text>
    <text x="324" y="38" fill="#58a6ff" font-size="14" font-weight="600" text-anchor="middle" class="sans">{today_str}</text>
  </g>

  <!-- Divider Line -->
  <line x1="{chart_left}" y1="84" x2="{chart_right}" y2="84" stroke="#21262d" stroke-width="1" />

  <!-- Grid Lines & Y-Axis -->
  <g id="grid-lines">
{grid_xml_str}
  </g>

  <!-- Area Fill -->
  <path d="{area_path}" fill="url(#areaGrad)" />

  <!-- Smooth Activity Line -->
  <path d="{line_path}" fill="none" stroke="#58a6ff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />

  <!-- Data Dots with Tooltips -->
  <g id="data-dots">
{dots_xml_str}
  </g>

  <!-- X-Axis Date Labels -->
  <g id="x-axis-labels">
{x_labels_xml_str}
  </g>

  <!-- Middle Divider Line -->
  <line x1="60" y1="304" x2="810" y2="304" stroke="#21262d" stroke-width="1" />

  <!-- ==================== GITHUB CONTRIBUTION STATS PANEL ==================== -->
  <g id="github-contribution-panel">
    <!-- Panel Box -->
    <rect x="60" y="318" width="750" height="122" rx="8" fill="#161b22" stroke="#30363d" stroke-width="1" />

    <!-- Vertical Dividers -->
    <line x1="310" y1="330" x2="310" y2="426" stroke="#21262d" stroke-width="1" />
    <line x1="560" y1="330" x2="560" y2="426" stroke="#21262d" stroke-width="1" />

    <!-- SECTION 1: TOTAL CONTRIBUTIONS -->
    <g transform="translate(185, 332)">
      <text x="0" y="32" text-anchor="middle" font-size="26" font-weight="600" fill="#e6edf3" class="sans">{gh_total}</text>
      <text x="0" y="58" text-anchor="middle" font-size="11" font-weight="600" fill="#8b949e" letter-spacing="0.5" class="sans">TOTAL CONTRIBUTIONS</text>
      <text x="0" y="80" text-anchor="middle" font-size="10.5" font-weight="400" fill="#58a6ff" class="sans">{gh_period}</text>
    </g>

    <!-- SECTION 2: CURRENT STREAK -->
    <g transform="translate(435, 332)">
      <!-- Circular Ring -->
      <circle cx="0" cy="30" r="22" fill="none" stroke="#58a6ff" stroke-width="2.5" />
      <!-- Big Number Inside Ring -->
      <text x="0" y="37" text-anchor="middle" font-size="18" font-weight="600" fill="#e6edf3" class="sans">{gh_cur_streak}</text>
      <!-- Title -->
      <text x="0" y="68" text-anchor="middle" font-size="11" font-weight="600" fill="#8b949e" letter-spacing="0.5" class="sans">CURRENT STREAK</text>
      <!-- Date Range -->
      <text x="0" y="86" text-anchor="middle" font-size="10.5" font-weight="400" fill="#58a6ff" class="sans">{gh_cur_range}</text>
    </g>

    <!-- SECTION 3: LONGEST STREAK -->
    <g transform="translate(685, 332)">
      <text x="0" y="32" text-anchor="middle" font-size="26" font-weight="600" fill="#e6edf3" class="sans">{gh_long_streak}</text>
      <text x="0" y="58" text-anchor="middle" font-size="11" font-weight="600" fill="#8b949e" letter-spacing="0.5" class="sans">LONGEST STREAK</text>
      <text x="0" y="80" text-anchor="middle" font-size="10.5" font-weight="400" fill="#58a6ff" class="sans">{gh_long_range}</text>
    </g>
  </g>

  <!-- Divider Line Above Footer -->
  <line x1="60" y1="458" x2="810" y2="458" stroke="#21262d" stroke-width="1" />

  <!-- ==================== ALL-TIME CODING HOURS FOOTER ==================== -->
  <g id="footer-all-time" transform="translate(60, 474)">
    <text x="0" y="13" fill="#8b949e" font-size="10" font-weight="600" letter-spacing="0.5" class="sans">TOTAL CODING TIME</text>
    <text x="0" y="36" fill="#58a6ff" font-size="18" font-weight="600" class="sans">{all_time_str}</text>
  </g>

  <!-- Footer Metadata -->
  <g id="footer-meta" transform="translate(810, 474)">
    <text x="0" y="13" fill="#8b949e" font-size="10" font-weight="500" text-anchor="end" class="sans">@rtomswastaken</text>
    <text x="0" y="34" fill="#484f58" font-size="9.5" text-anchor="end" class="sans">UPDATED {gen_timestamp}</text>
  </g>
</svg>"""

    validate_svg(svg_content)
    return svg_content


def validate_svg(svg_text: str) -> None:
    """Validate SVG XML syntax and structure."""
    if not svg_text.strip():
        raise ValidationError("SVG content is empty.")
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as e:
        raise ValidationError(f"Invalid XML syntax in SVG: {str(e)}") from e

    tag_clean = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if tag_clean != "svg":
        raise ValidationError(f"Expected root tag <svg>, got <{root.tag}>.")

    viewbox = root.attrib.get("viewBox")
    if not viewbox:
        raise ValidationError("Generated SVG is missing viewBox attribute.")

    if "NaN" in svg_text or "Infinity" in svg_text:
        raise ValidationError("Generated SVG contains invalid coordinates (NaN or Infinity).")


def update_graph_file(
    output_path: str,
    svg_content: str,
    force: bool = False,
) -> bool:
    """
    Write SVG to file only if data has actually changed, avoiding unnecessary commits.

    Returns:
    - True if file was written/updated
    - False if file was identical and skipped
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if not force and os.path.exists(output_path):
        existing_hash = get_existing_svg_hash(output_path)
        new_hash_match = re.search(r'data-activity-hash="([a-f0-9]{16})"', svg_content)
        new_hash = new_hash_match.group(1) if new_hash_match else None

        if existing_hash and new_hash and existing_hash == new_hash:
            print(f"[INFO] Coding activity data unchanged (hash: {existing_hash}). Skipping commit.")
            return False

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(svg_content)

    print(f"[SUCCESS] Updated {output_path} successfully.")
    return True


def create_initial_placeholder_data(window_days: int = 14) -> Dict[str, Any]:
    """Generate an initial clean 0h placeholder dataset for initial setup before first sync."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    start_date = today - datetime.timedelta(days=window_days - 1)
    days = []
    curr = start_date
    while curr <= today:
        d_str = curr.isoformat()
        days.append({
            "date": d_str,
            "datetime": curr,
            "seconds": 0.0,
            "hours": 0.0,
            "label": curr.strftime("%b %d"),
            "formatted": "0m",
        })
        curr += datetime.timedelta(days=1)

    return {
        "days": days,
        "window_days": window_days,
        "total_seconds": 0.0,
        "total_formatted": "0m",
        "daily_avg_seconds": 0.0,
        "daily_avg_formatted": "0m",
        "today_seconds": 0.0,
        "today_formatted": "0m",
        "start_date": days[0]["date"],
        "end_date": days[-1]["date"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate RTOMS Daily Dev Time & Contribution SVG graph.")
    parser.add_argument("--output", default="assets/coding-activity.svg", help="Path to output SVG file")
    parser.add_argument("--api-key", help="WakaTime API Key (or set WAKATIME_API_KEY env var)")
    parser.add_argument("--json-url", help="WakaTime public JSON share URL (or set WAKATIME_JSON_URL env var)")
    parser.add_argument("--github-user", default="rtomswastaken", help="GitHub username for contribution statistics")
    parser.add_argument("--github-token", help="GitHub Personal Access Token (or set GITHUB_TOKEN env var)")
    parser.add_argument("--force", action="store_true", help="Force rewrite even if data hash is unchanged")
    parser.add_argument("--init-placeholder", action="store_true", help="Create initial awaiting-sync placeholder SVG")
    parser.add_argument("--dry-run", action="store_true", help="Generate and validate without writing to disk")

    args = parser.parse_args()

    output_path = args.output
    api_key = args.api_key or os.environ.get("WAKATIME_API_KEY")
    json_url = args.json_url or os.environ.get("WAKATIME_JSON_URL")
    github_token = args.github_token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    github_user = args.github_user

    try:
        if args.init_placeholder:
            print("[INFO] Generating initial awaiting-sync placeholder SVG...")
            data = create_initial_placeholder_data(14)
            gh_contribs = create_initial_placeholder_contributions()
            all_time_data = create_initial_placeholder_all_time()
            svg = generate_svg(data, contrib_data=gh_contribs, all_time_data=all_time_data, is_placeholder=True)
            if not args.dry_run:
                update_graph_file(output_path, svg, force=True)
            return 0

        # 1. Fetch WakaTime 14-day activity statistics
        raw_json = fetch_wakatime_data(api_key=api_key, json_url=json_url)
        activity_data = parse_wakatime_data(raw_json, window_days=14)

        # 2. Fetch WakaTime all-time coding statistics
        all_time_data = None
        if api_key:
            try:
                raw_at = fetch_wakatime_all_time(api_key=api_key)
                all_time_data = parse_wakatime_all_time(raw_at)
                print(f"[INFO] Fetched WakaTime all-time coding time: {all_time_data['formatted']}")
            except Exception as e:
                print(f"[WARN] Could not fetch WakaTime all-time stats ({e}). Using fallback placeholder.", file=sys.stderr)
                all_time_data = create_initial_placeholder_all_time()
        else:
            all_time_data = create_initial_placeholder_all_time()

        # 3. Fetch GitHub contribution statistics
        gh_contribs = None
        try:
            gh_raw = fetch_github_contributions(username=github_user, token=github_token)
            gh_contribs = parse_github_contributions(gh_raw)
            print(f"[INFO] Fetched GitHub contributions: {gh_contribs['total']} total, streak: {gh_contribs['current_streak']}")
        except Exception as e:
            print(f"[WARN] Could not fetch live GitHub contributions ({e}). Using cached/default stats.", file=sys.stderr)
            gh_contribs = create_initial_placeholder_contributions()

        # Check existing hash if hash matches and not forced
        existing_hash = get_existing_svg_hash(output_path)
        new_hash = compute_activity_hash(activity_data, gh_contribs, all_time_data)

        if not args.force and existing_hash == new_hash:
            print(f"[INFO] Activity, contribution, and all-time data unchanged ({new_hash}). SVG update skipped.")
            return 0

        svg = generate_svg(activity_data, contrib_data=gh_contribs, all_time_data=all_time_data, is_placeholder=False)

        if args.dry_run:
            print("[INFO] Dry run successful. Generated SVG is valid.")
            return 0

        update_graph_file(output_path, svg, force=args.force)
        return 0

    except ConfigurationError as e:
        print(f"[CONFIG ERROR] {e}", file=sys.stderr)
        return 2
    except AuthenticationError as e:
        print(f"[AUTH ERROR] {e}", file=sys.stderr)
        return 3
    except RateLimitError as e:
        print(f"[RATE LIMIT] {e}", file=sys.stderr)
        return 4
    except WakaTimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[UNEXPECTED ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
