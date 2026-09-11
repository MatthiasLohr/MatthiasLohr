#!/usr/bin/env python3
"""Render remote RSS/Atom feeds into README.md between marker comments.

GitHub renders profile READMEs statically: no <iframe>, no JavaScript, no
server-side includes. The only way to show foreign web content is to fetch it
ahead of time and commit the result -- which is what this script does.

Usage:
    python3 scripts/update_feeds.py [--check]

--check exits 1 if README.md would change (useful in CI without committing).
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"
USER_AGENT = "MatthiasLohr-profile-readme/1.0 (+https://github.com/MatthiasLohr)"
TIMEOUT = 20
MAX_ITEMS = 5

NS = {
    "atom": "http://www.w3.org/2005/Atom",
}


@dataclass(frozen=True)
class Feed:
    marker: str  # the NAME in <!-- NAME:START --> / <!-- NAME:END -->
    url: str
    empty_note: str


FEEDS = (
    Feed(
        marker="GITLAB",
        # Any GitLab activity or project feed works; personal activity is
        # https://gitlab.com/<user>.atom
        url="https://gitlab.com/MatthiasLohr.atom",
        empty_note="_No recent public GitLab activity._",
    ),
    # Add more sources by dropping a matching marker pair into README.md, e.g.
    # Feed(marker="BLOG", url="https://mlohr.com/feed.xml",
    #      empty_note="_No posts yet._"),
)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def parse_entries(payload: bytes) -> list[tuple[str, str]]:
    """Return (title, link) pairs for both Atom and RSS 2.0 documents."""
    root = ET.fromstring(payload)
    entries: list[tuple[str, str]] = []

    for entry in root.findall("atom:entry", NS):
        title = (entry.findtext("atom:title", default="", namespaces=NS) or "").strip()
        link_el = entry.find("atom:link", NS)
        link = (link_el.get("href") if link_el is not None else "") or ""
        if title and link:
            entries.append((title, link))

    for item in root.iter("item"):  # RSS 2.0 has no namespace
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if title and link:
            entries.append((title, link))

    return entries[:MAX_ITEMS]


def escape(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"([\\`*_\[\]])", r"\\\1", text)


def render(feed: Feed) -> str:
    try:
        entries = parse_entries(fetch(feed.url))
    except Exception as error:  # keep the previous README content readable
        print(f"warning: {feed.marker}: {error}", file=sys.stderr)
        return f"_Could not load [the feed]({feed.url}) at build time._"

    if not entries:
        return feed.empty_note

    return "\n".join(f"- [{escape(title)}]({link})" for title, link in entries)


def replace_block(text: str, marker: str, body: str) -> str:
    pattern = re.compile(
        rf"(<!-- {marker}:START -->)(.*?)(<!-- {marker}:END -->)",
        re.DOTALL,
    )
    if not pattern.search(text):
        raise SystemExit(f"error: markers for {marker} not found in {README.name}")
    return pattern.sub(lambda m: f"{m.group(1)}\n{body}\n{m.group(3)}", text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the README would change, without writing it",
    )
    args = parser.parse_args()

    original = README.read_text(encoding="utf-8")
    updated = original
    for feed in FEEDS:
        updated = replace_block(updated, feed.marker, render(feed))

    if updated == original:
        print("README.md is up to date")
        return 0

    if args.check:
        print("README.md is out of date")
        return 1

    README.write_text(updated, encoding="utf-8")
    print("README.md updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
