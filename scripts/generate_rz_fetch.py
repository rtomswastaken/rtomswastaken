#!/usr/bin/env python3
"""
RZ FETCH — Custom Fastfetch-Style Terminal Profile Component Generator
Generates a developer terminal SVG component for GitHub profile READMEs,
featuring a custom RZ ASCII logo, syntax-highlighted system information,
and authentic terminal chrome.
"""

import argparse
import html
import os
import sys
import xml.etree.ElementTree as ET

# Canonical ASCII lines generated from assets/rz-logo.png (64x22 grid, box resampled, gamma 0.9)
CANONICAL_ASCII_ROWS = [
    "                              =%%%%@@@@@@@@+                    ",
    "                            -%@@@@@@@@@@@#:                     ",
    "                          .*@@@@@@@@@@@%-                       ",
    "          -#=             =+==#@@@@@@@+.                        ",
    "       .=%@#          .....:=%@@@@@@%=:------------------:.     ",
    "     .+@@@@@%%%%%@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@#-   ",
    "   :*@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@%= ",
    " :*@@@@@@@@@@@@@@@@@@@@@%%%#@@@@@@#++++=========-----=*@@@@@@@@#",
    "*@@@@@@@@@@@%*==--::..  .=*%@@@@@+                     .@@@@@@@*",
    ".. -@@@@#*=.         :+#@@@@@@@@-                     =%@@@@@%= ",
    "  +@%*-.            :###@@@@@@%:              .:-=++*%@@@@@@+.  ",
    "  :.                   *@@@@@#.      :==+*#%%@@@@@@@@@@@@@*.    ",
    "                      *@@@@@@++=.  :%@@@@@@@@@@@@@@%%#*+=.      ",
    "                     #@@@@@@@@@+  +@@@@@@@@@*+=-:.              ",
    "                   .#@@@@@@@@*. :%@@@@@@@@@@%=                  ",
    "                  .#@@@@@@@*.  +@@%#*%@@@@@@@@%=                ",
    "                 .%@@@@@%+.   :=-.    :+#@@@@@@@%=              ",
    "                .%@@@@%=                 .=#@@@@@@%=            ",
    "               :%@@@*-                      .-*%@@@@%=          ",
    "              :%@%+.                            :=#@@@%-        ",
    "             -%#=                                  .-+%@#:      ",
    "             +:                                        .=**.    ",
]

RAMP = " .:-=+*#%@"


def extract_ascii_from_image(image_path: str, target_w: int = 64, target_h: int = 22) -> list[str]:
    """
    Extracts text-based ASCII art from the RZ logo image using PIL and NumPy.
    Crops to the white foreground symbol and resizes with area-averaging.
    """
    from PIL import Image
    import numpy as np

    img = Image.open(image_path)
    arr = np.array(img)

    # If image has green background box (like user uploaded asset)
    if arr.shape[0] > 700 and arr.shape[1] > 1000:
        green_box = arr[37:796, 3:1023]
        is_white = ((green_box[:, :, 0] > 200) & (green_box[:, :, 1] > 200) & (green_box[:, :, 2] > 200)).astype(np.float32)
    else:
        # Transparent or standard white logo
        if arr.shape[2] == 4:
            is_white = (arr[:, :, 3] > 100).astype(np.float32)
        else:
            is_white = (arr[:, :, 0] > 200).astype(np.float32)

    wy, wx = np.where(is_white > 0)
    cropped = is_white[wy.min():wy.max() + 1, wx.min():wx.max() + 1]

    pil_crop = Image.fromarray((cropped * 255).astype(np.uint8))
    resized = pil_crop.resize((target_w, target_h), Image.Resampling.BOX)
    res_arr = np.array(resized) / 255.0
    res_arr = np.power(res_arr, 0.9)

    lines = []
    for row in res_arr:
        line_chars = []
        for val in row:
            idx = int(round(val * (len(RAMP) - 1)))
            idx = max(0, min(len(RAMP) - 1, idx))
            line_chars.append(RAMP[idx])
        lines.append("".join(line_chars))
    return lines


def generate_rz_fetch_svg(
    ascii_rows: list[str] | None = None,
    width: int = 920,
    height: int = 540,
) -> str:
    """
    Generates a standalone, fully-styled Fastfetch SVG terminal component.
    """
    rows = ascii_rows if ascii_rows is not None else CANONICAL_ASCII_ROWS
    num_rows = len(rows)

    svg_parts = []
    svg_parts.append(f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="100%">
  <title>RZ Fetch — RTOMS Developer Profile</title>
  <desc>Fastfetch-style developer terminal component featuring custom RZ ASCII logo and system information</desc>
  <defs>
    <style>
      .terminal-window {{ fill: #0D1117; stroke: #30363D; stroke-width: 1; }}
      .title-bar {{ fill: #161B22; }}
      .window-title {{ fill: #8B949E; font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Monaco, Consolas, 'Liberation Mono', monospace; font-size: 12px; }}
      .prompt-user {{ fill: #7EE787; font-weight: 600; }}
      .prompt-host {{ fill: #58A6FF; font-weight: 600; }}
      .prompt-tilde {{ fill: #BC8CFF; }}
      .prompt-symbol {{ fill: #8B949E; }}
      .prompt-cmd {{ fill: #56D4DD; font-weight: 500; }}
      .ascii-text {{ font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Monaco, Consolas, 'Liberation Mono', monospace; font-size: 9.8px; font-weight: 700; }}
      .info-text {{ font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Monaco, Consolas, 'Liberation Mono', monospace; font-size: 11.8px; }}
      .info-header {{ fill: #58A6FF; font-weight: 700; font-size: 14px; }}
      .info-at {{ fill: #8B949E; font-size: 14px; }}
      .info-host {{ fill: #56D4DD; font-weight: 700; font-size: 14px; }}
      .info-sep {{ stroke: #30363D; stroke-width: 1; }}
      .field-label {{ fill: #58A6FF; font-weight: 600; }}
      .field-val {{ fill: #E6EDF3; }}
      .val-accent-green {{ fill: #7EE787; }}
      .val-accent-cyan {{ fill: #56D4DD; }}
      .val-accent-purple {{ fill: #BC8CFF; }}
      .val-accent-orange {{ fill: #FFA657; }}
      .section-hdr {{ fill: #BC8CFF; font-weight: 700; }}
      .proj-name {{ font-weight: 600; }}
      .proj-desc {{ fill: #8B949E; }}
    </style>
  </defs>

  <!-- Terminal Window Chrome -->
  <rect width="{width}" height="{height}" rx="10" class="terminal-window"/>
  <path d="M 0,10 A 10,10 0 0,1 10,0 L {width-10},0 A 10,10 0 0,1 {width},10 L {width},38 L 0,38 Z" class="title-bar"/>
  <line x1="0" y1="38" x2="{width}" y2="38" stroke="#21262D" stroke-width="1"/>

  <!-- Traffic Light Window Buttons -->
  <circle cx="22" cy="19" r="6" fill="#FF5F56"/>
  <circle cx="42" cy="19" r="6" fill="#FFBD2E"/>
  <circle cx="62" cy="19" r="6" fill="#27C93F"/>
  <text x="{width // 2}" y="23" text-anchor="middle" class="window-title">rtoms@macbook: ~ (rz-fetch)</text>

  <!-- Terminal Prompt -->
  <text x="28" y="66" class="info-text">
    <tspan class="prompt-user">rtoms</tspan><tspan class="prompt-symbol">@</tspan><tspan class="prompt-host">github</tspan> <tspan class="prompt-tilde">~</tspan> <tspan class="prompt-symbol">%</tspan> <tspan class="prompt-cmd">fastfetch --profile rtoms</tspan>
  </text>

  <!-- Left: Forest Green Backdrop for Logo -->
  <rect x="24" y="88" width="404" height="424" rx="8" fill="#0F241A" stroke="#1C422F" stroke-width="1"/>''')

    # Add ASCII lines
    start_y = 114
    line_height = 17.5

    for r_idx, row in enumerate(rows):
        y_pos = start_y + r_idx * line_height
        vert_factor = r_idx / max(1, num_rows - 1)

        line_tspans = []
        for ch in row:
            if ch == " ":
                line_tspans.append(" ")
                continue

            if ch in "@%":
                if vert_factor < 0.35:
                    color = "#FFFFFF"
                elif vert_factor < 0.7:
                    color = "#E6EDF3"
                else:
                    color = "#56D4DD"
            elif ch in "#*":
                if vert_factor < 0.35:
                    color = "#7EE787"
                elif vert_factor < 0.7:
                    color = "#56D4DD"
                else:
                    color = "#58A6FF"
            elif ch in "+=":
                if vert_factor < 0.35:
                    color = "#48B07D"
                elif vert_factor < 0.7:
                    color = "#38BDAE"
                else:
                    color = "#388BFD"
            else:  # - : .
                if vert_factor < 0.35:
                    color = "#2EA043"
                elif vert_factor < 0.7:
                    color = "#258E7D"
                else:
                    color = "#1F6FEB"

            escaped = html.escape(ch)
            line_tspans.append(f'<tspan fill="{color}">{escaped}</tspan>')

        line_content = "".join(line_tspans)
        svg_parts.append(f'  <text x="36" y="{y_pos:.1f}" class="ascii-text" xml:space="preserve">{line_content}</text>')

    # Add Right side: Fastfetch Developer Information
    svg_parts.append('''
  <!-- Right: Fastfetch Developer Information -->
  <g transform="translate(450, 96)" class="info-text">
    <!-- User@Host Header -->
    <text x="0" y="16">
      <tspan class="info-header">rtoms</tspan><tspan class="info-at">@</tspan><tspan class="info-host">github</tspan>
    </text>
    <line x1="0" y1="25" x2="440" y2="25" class="info-sep"/>

    <!-- System Info Fields -->
    <text x="0" y="46"><tspan class="field-label">OS</tspan><tspan x="95" class="field-val">macOS (Apple Silicon)</tspan></text>
    <text x="0" y="64"><tspan class="field-label">HOST</tspan><tspan x="95" class="field-val">MacBook Pro</tspan></text>
    <text x="0" y="82"><tspan class="field-label">USER</tspan><tspan x="95" class="field-val">Richardsen Thomas (RTOMS)</tspan></text>
    <text x="0" y="100"><tspan class="field-label">ROLE</tspan><tspan x="95" class="val-accent-green">AI &amp; Software Developer · Designer</tspan></text>
    <text x="0" y="118"><tspan class="field-label">LOCATION</tspan><tspan x="95" class="field-val">Kerala, India</tspan></text>
    <text x="0" y="136"><tspan class="field-label">SHELL</tspan><tspan x="95" class="field-val">zsh</tspan></text>
    <text x="0" y="154"><tspan class="field-label">EDITOR</tspan><tspan x="95" class="field-val">VS Code · Neovim</tspan></text>
    <text x="0" y="172"><tspan class="field-label">UPTIME</tspan><tspan x="95" class="val-accent-cyan">Active · 100+ commits</tspan></text>

    <!-- Technical Skills -->
    <line x1="0" y1="184" x2="440" y2="184" class="info-sep" stroke-dasharray="3,3"/>
    <text x="0" y="204"><tspan class="field-label">LANGUAGES</tspan><tspan x="95" class="val-accent-orange">Python · Go · TypeScript · Rust · C++</tspan></text>
    <text x="0" y="222"><tspan class="field-label">AI / ML</tspan><tspan x="95" class="val-accent-purple">PyTorch · Ollama · Local LLMs · OpenCV</tspan></text>
    <text x="0" y="240"><tspan class="field-label">STACK</tspan><tspan x="95" class="val-accent-cyan">React · Next.js · FastAPI · SQLite · Docker</tspan></text>
    <text x="0" y="258"><tspan class="field-label">SYSTEMS</tspan><tspan x="95" class="field-val">macOS · Linux (Arch, Fedora)</tspan></text>

    <!-- Featured Projects -->
    <line x1="0" y1="270" x2="440" y2="270" class="info-sep" stroke-dasharray="3,3"/>
    <text x="0" y="290"><tspan class="section-hdr">PROJECTS</tspan></text>
    <text x="0" y="308"><tspan fill="#58A6FF" class="proj-name">  Zoe AI</tspan><tspan x="110" class="proj-desc">Local Computer-Use AI for macOS</tspan></text>
    <text x="0" y="326"><tspan fill="#7EE787" class="proj-name">  chatTUI</tspan><tspan x="110" class="proj-desc">Decentralized Terminal Chat in Go</tspan></text>
    <text x="0" y="344"><tspan fill="#FFA657" class="proj-name">  Oriah IDE</tspan><tspan x="110" class="proj-desc">Agentic Terminal AI Workspace</tspan></text>
    <text x="0" y="362"><tspan fill="#56D4DD" class="proj-name">  Time-Table</tspan><tspan x="110" class="proj-desc">Timetable Constraint Optimization</tspan></text>

    <!-- Focus -->
    <line x1="0" y1="374" x2="440" y2="374" class="info-sep" stroke-dasharray="3,3"/>
    <text x="0" y="392"><tspan class="field-label">FOCUS</tspan><tspan x="95" class="val-accent-green">Building local AI agents &amp; developer tools</tspan></text>

    <!-- Fastfetch ANSI Color Palette Blocks -->
    <g transform="translate(0, 404)">
      <rect x="0" y="0" width="22" height="10" rx="2" fill="#161B22"/>
      <rect x="26" y="0" width="22" height="10" rx="2" fill="#F85149"/>
      <rect x="52" y="0" width="22" height="10" rx="2" fill="#7EE787"/>
      <rect x="78" y="0" width="22" height="10" rx="2" fill="#FFA657"/>
      <rect x="104" y="0" width="22" height="10" rx="2" fill="#58A6FF"/>
      <rect x="130" y="0" width="22" height="10" rx="2" fill="#BC8CFF"/>
      <rect x="156" y="0" width="22" height="10" rx="2" fill="#56D4DD"/>
      <rect x="182" y="0" width="22" height="10" rx="2" fill="#E6EDF3"/>
      <!-- High-Intensity / Bright Tier -->
      <rect x="0" y="13" width="22" height="10" rx="2" fill="#484F58"/>
      <rect x="26" y="13" width="22" height="10" rx="2" fill="#FF7B72"/>
      <rect x="52" y="13" width="22" height="10" rx="2" fill="#AFF5B4"/>
      <rect x="78" y="13" width="22" height="10" rx="2" fill="#FFD1AC"/>
      <rect x="104" y="13" width="22" height="10" rx="2" fill="#79C0FF"/>
      <rect x="130" y="13" width="22" height="10" rx="2" fill="#D2A8FF"/>
      <rect x="156" y="13" width="22" height="10" rx="2" fill="#A5F3FC"/>
      <rect x="182" y="13" width="22" height="10" rx="2" fill="#FFFFFF"/>
      <!-- Terminal prompt cursor / URL -->
      <text x="225" y="17" fill="#8B949E" font-size="11">github.com/rtomswastaken</text>
    </g>
  </g>
</svg>''')

    svg_content = "\n".join(svg_parts)

    # Validate XML before returning
    try:
        ET.fromstring(svg_content)
    except ET.ParseError as err:
        raise ValueError(f"Generated SVG is not well-formed XML: {err}") from err

    return svg_content


def update_rz_fetch_file(target_path: str, svg_content: str) -> bool:
    """
    Safely writes the SVG content to the target file.
    Returns True if written.
    """
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(svg_content)
    return True


def main():
    parser = argparse.ArgumentParser(description="Generate RZ Fetch Fastfetch terminal SVG")
    parser.add_argument("--output", default="assets/rz-fetch.svg", help="Target output SVG path")
    parser.add_argument("--image", default=None, help="Optional image to re-extract ASCII art from")
    args = parser.parse_args()

    ascii_rows = None
    if args.image and os.path.exists(args.image):
        print(f"[INFO] Extracting ASCII art from image: {args.image}")
        ascii_rows = extract_ascii_from_image(args.image)

    svg = generate_rz_fetch_svg(ascii_rows=ascii_rows)
    update_rz_fetch_file(args.output, svg)
    print(f"[SUCCESS] Generated RZ Fetch SVG at: {args.output}")


if __name__ == "__main__":
    main()
