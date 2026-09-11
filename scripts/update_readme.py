#!/usr/bin/env python3
"""Render GitLab project cards and activity into README.md between markers.

GitHub renders profile READMEs statically: no <iframe>, no JavaScript, no
server-side includes. The only way to show foreign web content is to fetch it
ahead of time and commit the result -- which is what this script does.

Usage:
    python3 scripts/update_readme.py [--check]

--check exits 1 if README.md would change (useful in CI without committing).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"
USER_AGENT = "MatthiasLohr-profile-readme/1.0 (+https://github.com/MatthiasLohr)"
TIMEOUT = 20
MAX_ITEMS = 5
MAX_PROJECTS = 6
GITLAB_USER = "MatthiasLohr"
GITLAB_API = "https://gitlab.com/api/v4"

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


def fetch_projects() -> list[dict]:
    """Public, non-archived projects owned by GITLAB_USER, best first."""
    url = (
        f"{GITLAB_API}/users/{GITLAB_USER}/projects"
        "?per_page=100&order_by=star_count&sort=desc"
    )
    projects = json.loads(fetch(url))

    interesting = [
        project
        for project in projects
        if not project.get("archived")
        and project.get("description")
        and "deletion_scheduled" not in project["path"]
    ]
    interesting.sort(
        key=lambda project: (
            project.get("star_count", 0),
            project.get("forks_count", 0),
        ),
        reverse=True,
    )
    return interesting[:MAX_PROJECTS]


def latest_tag(path_with_namespace: str) -> str:
    """Newest tag name, or "" when the project has none."""
    slug = urllib.parse.quote(path_with_namespace, safe="")
    try:
        tags = json.loads(fetch(f"{GITLAB_API}/projects/{slug}/repository/tags?per_page=1"))
    except Exception as error:
        print(f"warning: tags for {path_with_namespace}: {error}", file=sys.stderr)
        return ""
    return tags[0]["name"] if tags else ""


def render_card(project: dict) -> str:
    name = html.escape(project["name"])
    url = html.escape(project["web_url"], quote=True)
    description = html.escape(re.sub(r"\s+", " ", project["description"]).strip())

    facts = [f"&#11088; {project.get('star_count', 0)}"]
    if project.get("forks_count"):
        facts.append(f"&#127860; {project['forks_count']}")
    tag = latest_tag(project["path_with_namespace"])
    if tag:
        facts.append(f"&#127991;&#65039; {html.escape(tag)}")

    return (
        '    <td width="50%" align="left" valign="top">\n'
        f'      <h4><a href="{url}">{name}</a></h4>\n'
        f"      <p>{description}</p>\n"
        f'      <p><sub>{" &nbsp;&middot;&nbsp; ".join(facts)}</sub></p>\n'
        "    </td>"
    )


def render_projects() -> str:
    """A two-column HTML table of project cards.

    GitHub strips <style> and inline CSS from READMEs, so a table is the only
    layout primitive available for something card-shaped.
    """
    try:
        projects = fetch_projects()
    except Exception as error:
        print(f"warning: projects: {error}", file=sys.stderr)
        return (
            "_Could not load projects at build time -- see "
            f"[gitlab.com/{GITLAB_USER}](https://gitlab.com/{GITLAB_USER})._"
        )

    if not projects:
        return f"_No public projects on [GitLab](https://gitlab.com/{GITLAB_USER}) yet._"

    rows = []
    for index in range(0, len(projects), 2):
        cells = "\n".join(render_card(project) for project in projects[index : index + 2])
        rows.append(f"  <tr>\n{cells}\n  </tr>")

    body = "\n".join(rows)
    return f"<table>\n{body}\n</table>"


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
