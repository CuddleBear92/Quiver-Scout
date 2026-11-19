"""The Open Source Games List by Trilarion (trilarion.github.io/opensourcegames): one page of games per letter.

Each entry has tags, the games that inspired it, its homepages and code repositories. Entries tagged "remake" are
reimplementations of an existing game; "clone" ones are new games in its style, marked as clones. Original games
are left out. Each letter page is only downloaded again when it changed.
"""

from __future__ import annotations

import html
import re

from .base import Context, Item

SITE = "https://trilarion.github.io/opensourcegames"
LETTERS = ["0"] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]

_ENTRY = re.compile(r'<div id="([^"]+)" class="box">(.*?)(?=<div id="[^"]+" class="box">|<footer)', re.S)
_TITLE = re.compile(r'class="level-item title is-4">(.*?)</div>', re.S)
_TAG = re.compile(r'class="tag[^"]*"[^>]*>([^<]+)<')
_LINE = {
    "Homepages": re.compile(r"Homepages?: (.*?)<br>", re.S),
    "Inspirations": re.compile(r"Inspirations?: (.*?)<br>", re.S),
    "Code repositories": re.compile(r"Code repositor(?:y|ies): (.*?)<br>", re.S),
}
_HREF = re.compile(r'href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_PLATFORMS = re.compile(r'href="platforms\.html#[^"]+" title="([^"]+)"')
_STATE = re.compile(r'<span class="has-text-weight-bold">([^<]+)</span>')
_TAGS = re.compile(r"<[^>]+>")


def _links(block: str, label: str) -> list[tuple[str, str]]:
    m = _LINE[label].search(block)
    return [(html.unescape(u), html.unescape(_TAGS.sub("", t)).strip()) for u, t in _HREF.findall(m.group(1))] if m else []


def parse_letter(letter: str, page: str) -> list[Item]:
    items = []
    for eid, block in _ENTRY.findall(page):
        tags = {html.unescape(t).strip() for t in _TAG.findall(block)}
        if not tags & {"remake", "clone"}:
            continue
        repos = [u for u, _ in _links(block, "Code repositories") if u.startswith("http")]
        homes = [u for u, _ in _links(block, "Homepages") if u.startswith("http")]
        url = (repos or homes or [""])[0]
        if not url:
            continue
        title = _TITLE.search(block)
        state = _STATE.findall(block)
        items.append(
            Item(
                source="trilarion",
                url=url,
                title=html.unescape(_TAGS.sub("", title.group(1))).strip() if title else eid,
                game="; ".join(t for _, t in _links(block, "Inspirations")),
                page=f"{SITE}/games/{letter}.html#{eid}",
                method="Remake" if "remake" in tags else "Clone",
                slug=eid,
                # Its platforms are where it runs (Windows, Linux), not the original game's, so they're kept apart.
                extra={k: v for k, v in {
                    "clone": "remake" not in tags,
                    "state": ", ".join(state),
                    "runs_on": ", ".join(p for p in dict.fromkeys(_PLATFORMS.findall(block)) if "Unknown" not in p),
                }.items() if v},
            )
        )
    return items


def fetch(ctx: Context) -> list[Item]:
    items: list[Item] = []
    unchanged = 0
    for n, letter in enumerate(LETTERS, start=1):
        page, changed = ctx.text(f"{SITE}/games/{letter}.html")
        unchanged += not changed
        items += parse_letter(letter, page)
        ctx.progress(f"OSGL: letter pages {n}/{len(LETTERS)}", n, len(LETTERS))
    ctx.progress(f"OSGL: {len(items)} projects" + (" (unchanged since last refresh)" if unchanged == len(LETTERS) else ""), 1, 1)
    return items
