#!/usr/bin/env python3
"""
RTOMS WakaTime Coding Activity Graph Generator

Retrieves daily coding activity for the past 14 days from WakaTime (via
authenticated API or public JSON share) and generates a polished, accessible,
responsive SVG graph for the GitHub profile README.
"""

import argparse
import base64
import datetime
import hashlib
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple


# Custom Exceptions
class WakaTimeError(Exception):
    """Base exception for WakaTime generator errors."""


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


class ValidationError(WakaTimeError):
    """Raised when the generated SVG fails validation."""


def sanitize_for_log(text: str) -> str:
    """Mask potential secrets or tokens in log strings."""
    if not text:
        return ""
    # Mask uuid-like or hex tokens
    text = re.sub(r'([a-zA-Z0-9_\-]{8})[a-zA-Z0-9_\-]{16,}', r'\1***REDACTED***', text)
    return text


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
        # WakaTime official endpoint for summaries over the last 14 days
        url = "https://wakatime.com/api/v1/users/current/summaries?range=Last%2014%20Days"

        # WakaTime HTTP Basic Auth requires base64-encoded api_key
        # Also support standard Basic Auth or Bearer tokens
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
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = response.getcode()
            if status != 200:
                raise APIResponseError(f"Unexpected HTTP status {status} from WakaTime endpoint.")
            body = response.read().decode("utf-8")
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


def parse_wakatime_data(
    raw_json: Dict[str, Any],
    window_days: int = 14,
    reference_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """
    Parse WakaTime JSON response into normalized 14-day activity records.

    Handles both:
    1. /api/v1/users/current/summaries format (data: [ { range: { date: ... }, grand_total: ... } ])
    2. Share embed JSON format (data: [ { date: ..., total_seconds: ... } ])
    Fills missing activity days with 0 seconds.
    """
    data_list = raw_json.get("data")
    if not isinstance(data_list, list):
        raise APIResponseError("Invalid WakaTime payload: missing 'data' array.")

    # Map discovered dates to total_seconds
    date_to_seconds: Dict[str, float] = {}

    for item in data_list:
        if not isinstance(item, dict):
            continue

        # Extract date
        date_str = None
        if "range" in item and isinstance(item["range"], dict) and "date" in item["range"]:
            date_str = str(item["range"]["date"])
        elif "date" in item:
            date_str = str(item["date"])

        # Extract total seconds
        seconds = 0.0
        if "grand_total" in item and isinstance(item["grand_total"], dict):
            gt = item["grand_total"]
            seconds = float(gt.get("total_seconds", 0.0) or 0.0)
        elif "total_seconds" in item:
            seconds = float(item.get("total_seconds", 0.0) or 0.0)
        elif "seconds" in item:
            seconds = float(item.get("seconds", 0.0) or 0.0)

        if date_str:
            # Normalize to YYYY-MM-DD
            try:
                parsed_d = datetime.date.fromisoformat(date_str)
                date_to_seconds[parsed_d.isoformat()] = max(0.0, seconds)
            except ValueError:
                continue

    # Determine reference end date
    if reference_date is not None:
        end_date = reference_date
    elif date_to_seconds:
        # Use latest date found in payload, or today if ahead
        latest_in_data = max(datetime.date.fromisoformat(d) for d in date_to_seconds.keys())
        today = datetime.datetime.now(datetime.timezone.utc).date()
        end_date = max(latest_in_data, today)
    else:
        end_date = datetime.datetime.now(datetime.timezone.utc).date()

    start_date = end_date - datetime.timedelta(days=window_days - 1)

    # Build consecutive day entries
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

    # Compute metrics
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


def compute_activity_hash(activity_data: Dict[str, Any]) -> str:
    """Compute a deterministic hash of the activity data values."""
    compact_records = [
        f"{d['date']}:{int(round(d['seconds']))}"
        for d in activity_data.get("days", [])
    ]
    payload = "|".join(compact_records)
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


def get_existing_svg_timestamp(svg_path: str) -> Optional[str]:
    """Extract embedded generation timestamp from an existing SVG file if present."""
    if not os.path.exists(svg_path):
        return None
    try:
        with open(svg_path, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.search(r'data-generated-at="([^"]+)"', content)
        if match:
            return match.group(1)
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

        # Catmull-Rom to Cubic Bezier control points
        # Tension factor = 1/6 for standard Catmull-Rom
        cp1x = p1[0] + (p2[0] - p0[0]) / 6.0
        cp1y = p1[1] + (p2[1] - p0[1]) / 6.0
        cp2x = p2[0] - (p3[0] - p1[0]) / 6.0
        cp2y = p2[1] - (p3[1] - p1[1]) / 6.0

        # Clamp control point y so curve never dips below the baseline y=0h
        cp1y = min(cp1y, bottom_y)
        cp2y = min(cp2y, bottom_y)

        line_parts.append(
            f"C {cp1x:.1f} {cp1y:.1f}, {cp2x:.1f} {cp2y:.1f}, {p2[0]:.1f} {p2[1]:.1f}"
        )

    line_path = " ".join(line_parts)

    # Area path: starts at bottom_y of first point, follows line, drops to bottom_y of last point
    first_x = points[0][0]
    last_x = points[-1][0]
    area_path = (
        f"M {first_x:.1f} {bottom_y:.1f} "
        f"L {first_x:.1f} {points[0][1]:.1f} "
        + " ".join(line_parts[1:])
        + f" L {last_x:.1f} {bottom_y:.1f} Z"
    )

    return line_path, area_path


def generate_svg(
    activity_data: Dict[str, Any],
    timestamp_override: Optional[str] = None,
    is_placeholder: bool = False,
) -> str:
    """
    Generate custom SVG activity graph.

    Aesthetic:
    - RTOMS branding: Dark navy (#080f1a / #0b1a2e), sleek border (#1f293d)
    - Blue accent line (#58a6ff), gradient area fill (#58a6ff)
    - Monospace clean typography
    - Minimal gridlines and clear axis labels
    - SVG tooltips for every data point
    """
    days = activity_data.get("days", [])
    if not days:
        raise ValidationError("Cannot generate SVG: no day records provided.")

    width = 840
    height = 330
    chart_left = 60
    chart_right = 810
    chart_top = 105
    chart_bottom = 265
    chart_width = chart_right - chart_left
    chart_height = chart_bottom - chart_top

    # Determine max hours for Y scale
    max_actual_hours = max((d["hours"] for d in days), default=0.0)

    # Pick clean round intervals
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

    # Compute coordinates for each day
    n_days = len(days)
    points: List[Tuple[float, float]] = []
    for i, day in enumerate(days):
        x = chart_left + (i / max(1, n_days - 1)) * chart_width
        y = chart_bottom - (min(day["hours"], max_y) / max_y) * chart_height
        points.append((x, y))

    line_path, area_path = calculate_smooth_path(points, chart_bottom)

    # Activity Hash
    act_hash = compute_activity_hash(activity_data)

    # Timestamp
    if timestamp_override:
        gen_timestamp = timestamp_override
    else:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        gen_timestamp = now_utc.strftime("%Y-%m-%d %H:%M UTC")

    # Metrics
    total_str = activity_data.get("total_formatted", "0m")
    avg_str = activity_data.get("daily_avg_formatted", "0m")
    today_str = activity_data.get("today_formatted", "0m")

    # Build Grid Lines and Y-Axis Labels
    grid_lines_xml = []
    for val in y_ticks:
        y_pos = chart_bottom - (val / max_y) * chart_height
        label_val = f"{int(val)}h" if val.is_integer() else f"{val:.1f}h"
        grid_lines_xml.append(
            f'    <line x1="{chart_left}" y1="{y_pos:.1f}" x2="{chart_right}" y2="{y_pos:.1f}" '
            f'stroke="#152132" stroke-dasharray="3,3" stroke-width="1" />\n'
            f'    <text x="{chart_left - 10}" y="{y_pos + 3.5:.1f}" fill="#6e7681" '
            f'font-size="10" font-family="monospace" text-anchor="end">{label_val}</text>'
        )
    grid_xml_str = "\n".join(grid_lines_xml)

    # Build X-Axis Labels and Data Dots
    dots_xml = []
    x_labels_xml = []
    for i, (day, (x, y)) in enumerate(zip(days, points)):
        # Dot with hover tooltip via <title>
        dot_xml = (
            f'    <g class="data-dot" tabindex="0">\n'
            f'      <circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#080f1a" stroke="#58a6ff" stroke-width="2" />\n'
            f'      <title>{day["date"]}: {day["formatted"]} ({day["hours"]:.2f} hrs)</title>\n'
            f'    </g>'
        )
        dots_xml.append(dot_xml)

        # X-Axis date label
        label_text = day["label"]
        x_labels_xml.append(
            f'    <text x="{x:.1f}" y="{chart_bottom + 18}" fill="#8b949e" '
            f'font-size="9.5" font-family="monospace" text-anchor="middle">{label_text}</text>'
        )

    dots_xml_str = "\n".join(dots_xml)
    x_labels_xml_str = "\n".join(x_labels_xml)

    placeholder_badge = ""
    if is_placeholder:
        placeholder_badge = (
            '    <g transform="translate(60, 48)">\n'
            '      <rect x="0" y="0" width="220" height="20" rx="4" fill="#1f293d" />\n'
            '      <text x="110" y="14" fill="#58a6ff" font-size="10" font-family="monospace" text-anchor="middle">AWAITING INITIAL SYNC</text>\n'
            '    </g>\n'
        )

    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="100%"
     data-activity-hash="{act_hash}" data-generated-at="{gen_timestamp}">
  <!-- activity-hash: {act_hash} -->
  <defs>
    <linearGradient id="bgGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#091322" />
      <stop offset="100%" stop-color="#060b13" />
    </linearGradient>
    <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#58a6ff" stop-opacity="0.28" />
      <stop offset="85%" stop-color="#58a6ff" stop-opacity="0.04" />
      <stop offset="100%" stop-color="#58a6ff" stop-opacity="0.0" />
    </linearGradient>
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="3" result="blur" />
      <feComposite in="SourceGraphic" in2="blur" operator="over" />
    </filter>
  </defs>

  <style>
    .mono {{ font-family: 'JetBrains Mono', 'Fira Code', 'SF Mono', Consolas, Menlo, monospace; }}
    .data-dot {{ cursor: pointer; transition: transform 0.15s ease; }}
    .data-dot circle {{ transition: r 0.2s ease, fill 0.2s ease, stroke 0.2s ease; }}
    .data-dot:hover circle {{ r: 6.5; fill: #58a6ff; stroke: #ffffff; stroke-width: 2.5; }}
    .card-border {{ stroke: #1f293d; stroke-width: 1.2; }}
    .stat-box {{ fill: #0b1a2e; stroke: #19273c; stroke-width: 1; }}
  </style>

  <!-- Container Box -->
  <rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="12" fill="url(#bgGrad)" class="card-border" />

  <!-- Header Section -->
  <g transform="translate(60, 32)">
    <text x="18" y="9" fill="#58a6ff" font-size="15" font-weight="700" letter-spacing="1.5" class="mono">Daily Dev Time</text>
    <text x="18" y="24" fill="#8b949e" font-size="9" letter-spacing="0.8" class="mono">WAKATIME | past 14 days</text>
  </g>

{placeholder_badge}
  <!-- Stat Badges (Top Right) -->
  <g transform="translate(420, 20)">
    <!-- Total Card -->
    <rect x="0" y="0" width="120" height="48" rx="8" class="stat-box" />
    <text x="60" y="18" fill="#8b949e" font-size="9" font-weight="600" letter-spacing="0.6" text-anchor="middle" class="mono">14-DAY TOTAL</text>
    <text x="60" y="38" fill="#f0f6fc" font-size="13.5" font-weight="700" text-anchor="middle" class="mono">{total_str}</text>

    <!-- Daily Average Card -->
    <rect x="132" y="0" width="120" height="48" rx="8" class="stat-box" />
    <text x="192" y="18" fill="#8b949e" font-size="9" font-weight="600" letter-spacing="0.6" text-anchor="middle" class="mono">DAILY AVERAGE</text>
    <text x="192" y="38" fill="#58a6ff" font-size="13.5" font-weight="700" text-anchor="middle" class="mono">{avg_str}</text>

    <!-- Today Card -->
    <rect x="264" y="0" width="120" height="48" rx="8" class="stat-box" />
    <text x="324" y="18" fill="#8b949e" font-size="9" font-weight="600" letter-spacing="0.6" text-anchor="middle" class="mono">TODAY</text>
    <text x="324" y="38" fill="#388bfd" font-size="13.5" font-weight="700" text-anchor="middle" class="mono">{today_str}</text>
  </g>

  <!-- Divider Line -->
  <line x1="{chart_left}" y1="82" x2="{chart_right}" y2="82" stroke="#152132" stroke-width="1" />

  <!-- Grid Lines & Y-Axis -->
  <g id="grid-lines">
{grid_xml_str}
  </g>

  <!-- Area Fill -->
  <path d="{area_path}" fill="url(#areaGrad)" />

  <!-- Smooth Activity Line -->
  <path d="{line_path}" fill="none" stroke="#58a6ff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" />

  <!-- Data Dots with Tooltips -->
  <g id="data-dots">
{dots_xml_str}
  </g>

  <!-- X-Axis Date Labels -->
  <g id="x-axis-labels">
{x_labels_xml_str}
  </g>

  <!-- Footer Metadata -->
  <g id="footer-meta" class="mono">
    <text x="{chart_left}" y="312" fill="#484f58" font-size="9">@rtomswastaken</text>
    <text x="{chart_right}" y="312" fill="#484f58" font-size="9" text-anchor="end">UPDATED {gen_timestamp}</text>
  </g>
</svg>"""

    # Validate output XML
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

    # Check root tag
    tag_clean = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if tag_clean != "svg":
        raise ValidationError(f"Expected root tag <svg>, got <{root.tag}>.")

    # Check viewBox attribute
    viewbox = root.attrib.get("viewBox")
    if not viewbox:
        raise ValidationError("Generated SVG is missing viewBox attribute.")

    # Check for NaN or Infinity
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
            # Data values have not changed! Preserve existing file to prevent empty commits.
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
    parser = argparse.ArgumentParser(description="Generate RTOMS WakaTime coding activity SVG graph.")
    parser.add_argument("--output", default="assets/coding-activity.svg", help="Path to output SVG file")
    parser.add_argument("--api-key", help="WakaTime API Key (or set WAKATIME_API_KEY env var)")
    parser.add_argument("--json-url", help="WakaTime public JSON share URL (or set WAKATIME_JSON_URL env var)")
    parser.add_argument("--force", action="store_true", help="Force rewrite even if data hash is unchanged")
    parser.add_argument("--init-placeholder", action="store_true", help="Create initial awaiting-sync placeholder SVG")
    parser.add_argument("--dry-run", action="store_true", help="Generate and validate without writing to disk")

    args = parser.parse_args()

    output_path = args.output
    api_key = args.api_key or os.environ.get("WAKATIME_API_KEY")
    json_url = args.json_url or os.environ.get("WAKATIME_JSON_URL")

    try:
        if args.init_placeholder:
            print("[INFO] Generating initial awaiting-sync placeholder SVG...")
            data = create_initial_placeholder_data(14)
            svg = generate_svg(data, is_placeholder=True)
            if not args.dry_run:
                update_graph_file(output_path, svg, force=True)
            return 0

        # Normal execution: fetch real WakaTime statistics
        raw_json = fetch_wakatime_data(api_key=api_key, json_url=json_url)
        activity_data = parse_wakatime_data(raw_json, window_days=14)

        # Check existing timestamp if hash matches and not forced
        existing_hash = get_existing_svg_hash(output_path)
        new_hash = compute_activity_hash(activity_data)

        if not args.force and existing_hash == new_hash:
            print(f"[INFO] WakaTime activity data unchanged ({new_hash}). SVG update skipped.")
            return 0

        svg = generate_svg(activity_data, is_placeholder=False)

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
