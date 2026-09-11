#!/usr/bin/env python3
"""Render the mlohr.com open-source portfolio into README.md between markers.

GitHub renders profile READMEs statically: no <iframe>, no JavaScript, no
server-side includes. The only way to show foreign web content is to fetch it
ahead of time and commit the result -- which is what this script does.

https://mlohr.com/open-source/ is the single source of truth for which
projects are shown and how they are described; this script mirrors that
curation rather than ranking repositories itself.

Usage:
    python3 scripts/update_readme.py [--check]

--check exits 1 if README.md would change (useful in CI without committing).
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"
USER_AGENT = "MatthiasLohr-profile-readme/1.0 (+https://github.com/MatthiasLohr)"
TIMEOUT = 20
MAX_ITEMS = 5

OPEN_SOURCE_URL = "https://mlohr.com/open-source/"

# Categories rendered as collapsed <details> instead of open card grids.
COLLAPSED_CATEGORIES = {"Other Open Source Projects"}
# Rendered as a compact list -- these are other people's repositories.
UPSTREAM_CATEGORY = "Contributions to Upstream Projects"

NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass
class Project:
    name: str
    description: str = ""
    url: str = ""
    badges: list[str] = field(default_factory=list)


@dataclass
class Category:
    title: str
    projects: list[Project] = field(default_factory=list)


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
)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


class OpenSourcePageParser(HTMLParser):
    """Pull categories and project cards out of the open-source page.

    Anchored on the page's own class names: <section class="service-category">
    wraps an <h2> title and a series of <div class="os-card"> entries, each
    with an <h3> name, a <p class="os-card-description">, shields.io <img>
    badges and a repository <a href>.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.categories: list[Category] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._card_depth: int | None = None
        self._depth = 0

    # -- helpers ---------------------------------------------------------
    @property
    def _category(self) -> Category | None:
        return self.categories[-1] if self.categories else None

    @property
    def _project(self) -> Project | None:
        category = self._category
        return category.projects[-1] if category and category.projects else None

    def _start_capture(self, field_name: str) -> None:
        self._capture = field_name
        self._buffer = []

    # -- HTMLParser API --------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: (value or "") for key, value in attrs}
        classes = attributes.get("class", "").split()
        self._depth += 1

        if tag == "section" and "service-category" in classes:
            self.categories.append(Category(title=""))
        elif tag == "div" and "os-card" in classes:
            if self._category is not None:
                self._category.projects.append(Project(name=""))
                self._card_depth = self._depth
        elif tag == "h2" and self._category is not None and not self._category.title:
            self._start_capture("title")
        elif tag == "h3" and self._project is not None:
            self._start_capture("name")
        elif tag == "p" and "os-card-description" in classes:
            self._start_capture("description")
        elif tag == "img" and self._project is not None:
            source = attributes.get("src", "")
            if source.startswith("https://img.shields.io/"):
                self._project.badges.append(source)
        elif tag == "a" and self._project is not None and not self._project.url:
            href = attributes.get("href", "")
            if href.startswith(("https://gitlab.com/", "https://github.com/")):
                self._project.url = href

    def handle_endtag(self, tag: str) -> None:
        if self._capture and tag in {"h2", "h3", "p"}:
            text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
            if self._capture == "title" and self._category is not None:
                self._category.title = text
            elif self._project is not None:
                setattr(self._project, self._capture, text)
            self._capture = None

        if tag == "div" and self._card_depth == self._depth:
            self._card_depth = None
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)


def escape_attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_markdown(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"([\\`*_\[\]])", r"\\\1", text)


def render_card(project: Project) -> str:
    name = escape_text(project.name)
    heading = (
        f'<a href="{escape_attr(project.url)}">{name}</a>' if project.url else name
    )
    lines = [
        '    <td width="50%" align="left" valign="top">',
        f"      <h4>{heading}</h4>",
        f"      <p>{escape_text(project.description)}</p>",
    ]
    if project.badges:
        badges = "\n".join(
            f'        <img src="{escape_attr(badge)}" '
            f'alt="{name} badge" height="20">'
            for badge in project.badges
        )
        lines.append(f"      <p>\n{badges}\n      </p>")
    lines.append("    </td>")
    return "\n".join(lines)


def render_grid(projects: list[Project]) -> str:
    """Two-column HTML table of cards.

    GitHub strips <style> and inline CSS from READMEs, so a table is the only
    layout primitive available for something card-shaped.
    """
    rows = []
    for index in range(0, len(projects), 2):
        pair = projects[index : index + 2]
        cells = "\n".join(render_card(project) for project in pair)
        if len(pair) == 1:  # keep the last card from stretching full width
            cells += '\n    <td width="50%"></td>'
        rows.append(f"  <tr>\n{cells}\n  </tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>"


def render_upstream(projects: list[Project]) -> str:
    items = []
    for project in projects:
        label = escape_markdown(project.name)
        entry = f"- [{label}]({project.url})" if project.url else f"- {label}"
        if project.description:
            entry += f" — {escape_markdown(project.description)}"
        items.append(entry)
    return "\n".join(items)


def render_projects() -> str:
    try:
        parser = OpenSourcePageParser()
        parser.feed(fetch(OPEN_SOURCE_URL).decode("utf-8", errors="replace"))
        categories = [
            category
            for category in parser.categories
            if category.title and category.projects
        ]
    except Exception as error:
        print(f"warning: open-source page: {error}", file=sys.stderr)
        categories = []

    if not categories:
        return (
            f"_Could not load [the project list]({OPEN_SOURCE_URL}) at build time._"
        )

    blocks = []
    for category in categories:
        title = escape_text(category.title)
        if category.title == UPSTREAM_CATEGORY:
            blocks.append(f"#### {title}\n\n{render_upstream(category.projects)}")
        elif category.title in COLLAPSED_CATEGORIES:
            blocks.append(
                f"<details>\n<summary><b>{title}</b></summary>\n\n"
                f"{render_grid(category.projects)}\n\n</details>"
            )
        else:
            blocks.append(f"#### {title}\n\n{render_grid(category.projects)}")

    return "\n\n".join(blocks)


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


def render_feed(feed: Feed) -> str:
    try:
        entries = parse_entries(fetch(feed.url))
    except Exception as error:  # keep the previous README content readable
        print(f"warning: {feed.marker}: {error}", file=sys.stderr)
        return f"_Could not load [the feed]({feed.url}) at build time._"

    if not entries:
        return feed.empty_note

    return "\n".join(f"- [{escape_markdown(title)}]({link})" for title, link in entries)


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
        updated = replace_block(updated, feed.marker, render_feed(feed))
    updated = replace_block(updated, "PROJECTS", render_projects())

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
