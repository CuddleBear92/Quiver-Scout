"""OSGameClones (osgameclones.com): open-source remakes of old games, all on one page.

Each project is tagged remake (a reimplementation of the original), official (the original's own source code),
clone (a new game in the style of the original), similar or tool. Clones and similar games are marked as clones,
which the comparison leaves out unless Settings says to include them.
"""

from __future__ import annotations

import html
import re

from .base import Context, Item, day

SITE = "https://osgameclones.com"

_ENTRY = "itemtype=http://schema.org/SoftwareSourceCode>"
_GAME = re.compile(r"<dt id=(\"[^\"]+\"|[^ >]+) class=searchable>\s*<a href=\"?([^ \">]+)\"?>([^<]+)</a>(.*?)</dt>", re.S)
_PLATFORM = re.compile(r'class="tag platforms ([\w-]+)" data-name=[\w-]+>\s*([^<]*?)\s*<')
_ATTR = {k: re.compile(rf'data-{k}=("[^"]*"|[^ >]+)') for k in ("name", "parent", "tags", "updated")}
_REPO = re.compile(r'itemprop=codeRepository href="?([^ ">]+)')
_HOME = re.compile(r'href="?([^ ">]+)"? itemprop=url')
_NAME = re.compile(r"<span itemprop=name>([^<]+)</span>")

TYPES = {"remake": "Remake", "official": "Source release", "clone": "Clone", "similar": "Similar", "tool": "Tool"}
CLONES = {"clone", "similar"}


def _attr(chunk: str, k: str) -> str:
    m = _ATTR[k].search(chunk)
    return html.unescape(m.group(1).strip('"')).strip() if m else ""


def parse(page: str) -> list[Item]:
    games = {}
    for gid, href, title, rest in _GAME.findall(page):
        gid = html.unescape(gid.strip('"'))
        if gid not in games:
            # Some platforms are shown as an icon only, with no text: their tag id names them (ps3, xbox-360).
            platforms = [(text or tag.replace("-", " ")).strip() for tag, text in _PLATFORM.findall(rest)]
            games[gid] = (html.unescape(title.strip()), href, platforms)

    items = []
    for chunk in page.split(_ENTRY)[1:]:
        chunk = chunk[: chunk.find("</dd>")]
        tags = set(_attr(chunk, "tags").split())
        kind = next((t for t in TYPES if t in tags), "")
        if kind == "tool":
            continue
        repo, home = _REPO.search(chunk), _HOME.search(chunk)
        url = html.unescape((repo or home).group(1)) if (repo or home) else ""
        if not url:
            continue
        parent = _attr(chunk, "parent")
        title, href, platforms = games.get(parent, ("", "", []))
        name = _NAME.search(chunk)
        items.append(
            Item(
                source="osgameclones",
                url=url,
                title=html.unescape(name.group(1)) if name else _attr(chunk, "name"),
                game=title,
                page=SITE + href if href.startswith("/") else (href or SITE),
                method=TYPES.get(kind, ""),
                platform=", ".join(platforms),
                slug=_attr(chunk, "name"),
                extra={"clone": True} if kind in CLONES else {"updated": day(_attr(chunk, "updated"))},
            )
        )
    return items


def fetch(ctx: Context) -> list[Item]:
    ctx.progress("OSGameClones: page", 0, 0)
    page, changed = ctx.text(SITE + "/", timeout=120)
    items = parse(page)
    ctx.progress(f"OSGameClones: {len(items)} projects" + ("" if changed else " (unchanged since last refresh)"), 1, 1)
    return items
