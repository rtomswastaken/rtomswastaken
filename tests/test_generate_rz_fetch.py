#!/usr/bin/env python3
"""
Unit tests for RZ Fetch Fastfetch terminal SVG generator.
"""

import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

# Add scripts directory to sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from generate_rz_fetch import (
    CANONICAL_ASCII_ROWS,
    RAMP,
    extract_ascii_from_image,
    generate_rz_fetch_svg,
    update_rz_fetch_file,
)


class TestRZFetchGenerator(unittest.TestCase):
    """Test suite for Fastfetch terminal component generation and validation."""

    def test_canonical_ascii_rows_structure(self):
        """Verify canonical ASCII rows have consistent dimensions and valid character set."""
        self.assertEqual(len(CANONICAL_ASCII_ROWS), 22)
        valid_chars = set(RAMP)
        for row_idx, row in enumerate(CANONICAL_ASCII_ROWS):
            self.assertEqual(len(row), 64, f"Row {row_idx} must have 64 columns")
            for ch in row:
                self.assertIn(ch, valid_chars, f"Character '{ch}' not in allowed RAMP")

    def test_svg_is_valid_xml(self):
        """Verify generated SVG parses cleanly as valid XML."""
        svg_content = generate_rz_fetch_svg()
        root = ET.fromstring(svg_content)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertEqual(root.attrib.get("viewBox"), "0 0 920 540")

    def test_terminal_chrome_elements(self):
        """Verify macOS terminal window chrome (buttons, title bar, prompt)."""
        svg_content = generate_rz_fetch_svg()
        # macOS Traffic lights
        self.assertIn('#FF5F56', svg_content, "Red close button must be present")
        self.assertIn('#FFBD2E', svg_content, "Yellow minimize button must be present")
        self.assertIn('#27C93F', svg_content, "Green maximize button must be present")
        # Terminal prompt
        self.assertIn("fastfetch --profile rtoms", svg_content)
        self.assertIn("rtoms@macbook: ~ (rz-fetch)", svg_content)

    def test_ascii_logo_rendered_with_ansi_colors(self):
        """Verify custom RZ ASCII logo is rendered into SVG tspans with ANSI colors."""
        svg_content = generate_rz_fetch_svg()
        # Key terminal palette colors used in the ASCII art
        self.assertIn("#7EE787", svg_content, "Emerald green must be in SVG")
        self.assertIn("#56D4DD", svg_content, "Cyan must be in SVG")
        self.assertIn("#58A6FF", svg_content, "Blue must be in SVG")
        self.assertIn("#0F241A", svg_content, "Forest green backdrop must be in SVG")
        # Ensure ASCII text class is applied
        self.assertIn('class="ascii-text"', svg_content)

    def test_fastfetch_system_info_fields(self):
        """Verify all required developer information fields are present."""
        svg_content = generate_rz_fetch_svg()
        expected_fields = [
            "rtoms",
            "github",
            "OS",
            "macOS (Apple Silicon)",
            "HOST",
            "MacBook Pro",
            "USER",
            "Richardsen Thomas (RTOMS)",
            "ROLE",
            "AI &amp; Software Developer",
            "LOCATION",
            "Kerala, India",
            "SHELL",
            "zsh",
            "EDITOR",
            "VS Code",
            "Neovim",
            "LANGUAGES",
            "Python",
            "Go",
            "TypeScript",
            "Rust",
            "AI / ML",
            "PyTorch",
            "Ollama",
            "Local LLMs",
            "STACK",
            "React",
            "FastAPI",
            "PROJECTS",
            "Zoe AI",
            "chatTUI",
            "Oriah IDE",
            "Time-Table",
            "FOCUS",
            "github.com/rtomswastaken",
        ]
        for field in expected_fields:
            self.assertIn(field, svg_content, f"Expected field '{field}' not found in SVG")

    def test_vigilnet_is_strictly_excluded(self):
        """Verify that unreleased project VigilNet is NEVER included."""
        svg_content = generate_rz_fetch_svg()
        self.assertNotIn("VigilNet", svg_content, "VigilNet must not be featured!")
        self.assertNotIn("vigilnet", svg_content.lower(), "vigilnet must not appear anywhere!")

    def test_ansi_color_palette_blocks(self):
        """Verify Fastfetch 16-color ANSI palette blocks are present."""
        svg_content = generate_rz_fetch_svg()
        # Normal & Bright tier colors
        self.assertIn("#F85149", svg_content)
        self.assertIn("#7EE787", svg_content)
        self.assertIn("#FFA657", svg_content)
        self.assertIn("#58A6FF", svg_content)
        self.assertIn("#BC8CFF", svg_content)
        self.assertIn("#56D4DD", svg_content)
        self.assertIn("#E6EDF3", svg_content)
        self.assertIn("#FF7B72", svg_content)
        self.assertIn("#FFFFFF", svg_content)

    def test_update_file_writer(self):
        """Verify update_rz_fetch_file writes to disk accurately."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "rz-fetch.svg")
            svg_content = generate_rz_fetch_svg()
            res = update_rz_fetch_file(out_file, svg_content)
            self.assertTrue(res)
            self.assertTrue(os.path.exists(out_file))
            with open(out_file, "r", encoding="utf-8") as f:
                saved = f.read()
            self.assertEqual(saved, svg_content)

    def test_image_extraction_if_logo_exists(self):
        """Verify image-to-ASCII extraction works if assets/rz-logo.png exists."""
        logo_path = os.path.join(REPO_ROOT, "assets", "rz-logo.png")
        if os.path.exists(logo_path):
            rows = extract_ascii_from_image(logo_path, target_w=64, target_h=22)
            self.assertEqual(len(rows), 22)
            self.assertEqual(len(rows[0]), 64)
            # Verify it contains RZ logo characters
            has_symbols = any(c in "@%#*" for row in rows for c in row)
            self.assertTrue(has_symbols, "Extracted ASCII rows must contain logo symbols")

    def test_readme_integration(self):
        """Verify README.md embeds assets/rz-fetch.svg with a proper relative path."""
        readme_path = os.path.join(REPO_ROOT, "README.md")
        self.assertTrue(os.path.exists(readme_path))
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("assets/rz-fetch.svg", content)
        # Ensure Daily Dev Time coding activity graph is still preserved
        self.assertIn("assets/coding-activity.svg", content)
        # Ensure VigilNet is NOT in README
        self.assertNotIn("VigilNet", content)
        self.assertNotIn("vigilnet", content.lower())


if __name__ == "__main__":
    unittest.main()
