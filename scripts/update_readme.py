#!/usr/bin/env python3
"""Render the mlohr.com open-source portfolio into README.md between markers.

GitHub renders profile READMEs statically: no <iframe>, no JavaScript, no
server-side includes. The only way to show foreign web content is to fetch it
ahead of time and commit the result -- which is what this script does.

Project data comes from data/open_source.yaml, a vendored copy of the databag
that drives https://mlohr.com/open-source/, so both pages describe the same
projects the same way. Refresh the copy after editing the website:

    python3 scripts/update_readme.py --sync ../mlohr.com/website/databags/open_source.yaml

Usage:
    python3 scripts/update_readme.py [--check] [--databag PATH]

--check exits 1 if README.md would change (useful in CI without committing).
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET

import yaml
from dataclasses import dataclass, field
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"
USER_AGENT = "MatthiasLohr-profile-readme/1.0 (+https://github.com/MatthiasLohr)"
TIMEOUT = 20
MAX_ITEMS = 5

DATABAG = README.parent / "data" / "open_source.yaml"
OPEN_SOURCE_URL = "https://mlohr.com/open-source/"

# Categories rendered as collapsed <details> instead of open card grids.
COLLAPSED_CATEGORIES = {"Other Open Source Projects"}
# Heading for the databag's "contributions" list -- other people's
# repositories, rendered as a compact list rather than cards.
UPSTREAM_CATEGORY = "Contributions to Upstream Projects"

NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass
class Project:
    name: str
    description: str = ""
    url: str = ""          # repository
    homepage: str = ""     # project or docs page, when it has one
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


def load_categories(databag: Path) -> tuple[list[Category], list[Project]]:
    """Read the website databag into categories plus upstream contributions."""
    data = yaml.safe_load(databag.read_text(encoding="utf-8")) or {}

    def as_project(entry: dict) -> Project:
        return Project(
            name=str(entry.get("name", "")).strip(),
            description=str(entry.get("description") or "").strip(),
            url=str(entry.get("repository") or entry.get("url") or "").strip(),
            homepage=str(entry.get("url") or "").strip(),
            badges=[
                str(badge["url"])
                for badge in entry.get("badges") or []
                if isinstance(badge, dict) and badge.get("url")
            ],
        )

    categories = [
        Category(
            title=str(group.get("category_title", "")).strip(),
            projects=[
                project
                for entry in group.get("entries") or []
                if (project := as_project(entry)).name
            ],
        )
        for group in data.get("projects") or []
    ]
    contributions = [
        project
        for entry in data.get("contributions") or []
        if (project := as_project(entry)).name
    ]
    return [c for c in categories if c.title and c.projects], contributions


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
        target = project.homepage or project.url
        entry = f"- [{label}]({target})" if target else f"- {label}"
        if project.description:
            entry += f" — {escape_markdown(project.description)}"
        if project.homepage and project.url:
            entry += f" ([repository]({project.url}))"
        items.append(entry)
    return "\n".join(items)


def render_projects(databag: Path) -> str:
    try:
        categories, contributions = load_categories(databag)
    except Exception as error:
        print(f"warning: {databag}: {error}", file=sys.stderr)
        return f"_Could not load [the project list]({OPEN_SOURCE_URL}) at build time._"

    if not categories:
        return f"_No projects listed in {databag.name}._"

    blocks = []
    for category in categories:
        title = escape_text(category.title)
        grid = render_grid(category.projects)
        if category.title in COLLAPSED_CATEGORIES:
            blocks.append(
                f"<details>\n<summary><b>{title}</b></summary>\n\n{grid}\n\n</details>"
            )
        else:
            blocks.append(f"#### {title}\n\n{grid}")

    if contributions:
        blocks.append(
            f"#### {escape_text(UPSTREAM_CATEGORY)}\n\n{render_upstream(contributions)}"
        )

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
    parser.add_argument(
        "--databag",
        type=Path,
        default=DATABAG,
        help=f"project data to render (default: {DATABAG.name})",
    )
    parser.add_argument(
        "--sync",
        type=Path,
        metavar="PATH",
        help="copy PATH over the vendored databag before rendering",
    )
    args = parser.parse_args()

    if args.sync:
        shutil.copyfile(args.sync, DATABAG)
        print(f"synced {DATABAG.name} from {args.sync}")

    original = README.read_text(encoding="utf-8")
    updated = original
    for feed in FEEDS:
        updated = replace_block(updated, feed.marker, render_feed(feed))
    updated = replace_block(updated, "PROJECTS", render_projects(args.databag))

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
