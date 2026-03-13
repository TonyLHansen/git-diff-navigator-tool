#!/usr/bin/env python3
"""
Extract normalized visible text from SVG <text> nodes.

This utility is useful when renderers split visible words across multiple
adjacent <text> elements and encode non-breaking spaces as HTML entities.
"""

from __future__ import annotations

import argparse
import html
from pathlib import Path
import re
import sys


def svg_plain_text(svg_text: str) -> str:
    """Extract and normalize rendered SVG <text> node content."""
    text_chunks = re.findall(r"<text[^>]*>(.*?)</text>", svg_text, flags=re.DOTALL)
    joined = "".join(text_chunks)
    plain = html.unescape(joined)
    return plain.replace("\xa0", " ")


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the SVG text extraction utility."""
    parser = argparse.ArgumentParser(description="Extract plain text from an SVG file")
    parser.add_argument("svg_file", help="Path to SVG file")
    return parser.parse_args()


def main() -> int:
    """Run the CLI utility and print normalized text from the requested SVG file."""
    args = _parse_args()
    in_path = Path(args.svg_file)
    if not in_path.is_file():
        print(f"Error: SVG file not found: {in_path}", file=sys.stderr)
        return 2

    svg = in_path.read_text(encoding="utf-8")
    sys.stdout.write(svg_plain_text(svg) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
