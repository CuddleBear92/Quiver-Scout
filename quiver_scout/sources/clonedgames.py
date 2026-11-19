"""Cloned Games (clonedgames.com): open-source clones, ports and decompilations of retro games, one page each.

Its sitemap lists every project page and when it last changed; each page's structured data names the project, its
repository and the original game. Pages are kept and only read again when the sitemap says they changed (the first
refresh reads about 4,000 pages, ~120 MB). Decompilations and recompilations carry a badge saying so; the site calls
everything else a clone, and so does this tool, which leaves them out unless Settings says to include clones.
Its /api/ is off limits by its robots.txt, so this doesn't use it.
"""

from __future__ import annotations

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import net
from .base import Context, Item, day

SITE = "https://clonedgames.com"
SITEMAP = f"{SITE}/sitemap.xml"

# Sitemap sections that aren't project pages.
SECTIONS = {"originals", "compare", "themes", "decompilations", "engines", "genres", "blog", "contact", "privacy",
            "dmca", "about", "terms", ""}

_URL = re.compile(r"<url>\s*<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]+)</lastmod>)?", re.S)
_LD = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)
_ORIGINAL = re.compile(r'href="/originals/([^"]+)"[^>]*>(.*?)</a>', re.S)
_TAGS = re.compile(r"<[^>]+>")
_BADGE = re.compile(r">\s*(Decompilation|Recompilation|Disassembly)\s*<")


def parse_sitemap(xml: str) -> dict[str, str]:
    """Project page URL -> lastmod."""
    out = {}
    for loc, lastmod in _URL.findall(xml):
        path = loc.strip().split("://", 1)[-1].split("/", 1)[-1] if "/" in loc.split("://", 1)[-1] else ""
        if path.split("/")[0] in SECTIONS or "/" in path.strip("/"):
            continue
        out[loc.strip()] = (lastmod or "").strip()
    return out


def parse_page(url: str, page: str) -> dict | None:
    """The project on one page, or None when the page isn't a project or has no link."""
    game = None
    for block in _LD.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in data.get("@graph", [data]):
            if node.get("@type") == "VideoGame":
                game = node
    if not game:
        return None
    link = game.get("sameAs") or ""
    if isinstance(link, list):
        link = link[0] if link else ""
    if not link:
        return None
    body = page.split("self.__next_f", 1)[0]
    based = game.get("isBasedOn") or []
    slugs = [b.rstrip("/").rsplit("/", 1)[-1] for b in ([based] if isinstance(based, str) else based)]
    # The page's links to those originals carry their names (other /originals/ links are the site's menus).
    names = {}
    for slug, text in _ORIGINAL.findall(body):
        name = html.unescape(_TAGS.sub("", text)).strip()
        if name and slug not in names:
            names[slug] = name
    originals = [names.get(s) or s.replace("-", " ").title() for s in slugs]
    badge = _BADGE.search(body)
    kind = {"Decompilation": "Decomp", "Recompilation": "Recomp", "Disassembly": "Disassembly"}.get(
        badge.group(1) if badge else "", "")
    return {
        "url": link,
        "title": html.unescape(game.get("name", "")),
        "game": "; ".join(originals),
        "method": kind or "Clone",
        "clone": not kind,
        "slug": url.rstrip("/").rsplit("/", 1)[-1],
    }


def _item(page_url: str, d: dict, lastmod: str) -> Item:
    return Item(
        source="clonedgames",
        url=d["url"],
        title=d["title"],
        game=d["game"],
        page=page_url,
        method=d["method"],
        slug=d["slug"],
        extra={"clone": True} if d.get("clone") else {"updated": day(lastmod)},
    )


def fetch(ctx: Context, cache: dict | None = None, workers: int = 4) -> list[Item]:
    """`cache` (page URL -> {"version": lastmod, "data": project or None}) is updated in place."""
    cache = cache if cache is not None else {}
    ctx.progress("Cloned Games: sitemap", 0, 0)
    pages = parse_sitemap(ctx.text(SITEMAP)[0])
    stale = [u for u, mod in pages.items() if ctx.full or u not in cache or cache[u].get("version") != mod]
    ctx.progress(f"Cloned Games: {len(stale)} of {len(pages)} pages to read", 0, len(stale))

    def one(url: str):
        return url, parse_page(url, net.get_text(url))

    errors, done = 0, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(one, u) for u in stale]):
            done += 1
            try:
                url, project = future.result()
                cache[url] = {"version": pages[url], "data": project}
            except Exception:
                errors += 1
            if done % 25 == 0 or done == len(stale):
                ctx.progress(f"Cloned Games: pages {done}/{len(stale)}", done, len(stale))

    for gone in set(cache) - set(pages):
        del cache[gone]
    items = [_item(u, c["data"], c.get("version") or "") for u, c in cache.items() if c.get("data")]
    ctx.progress(f"Cloned Games: {len(items)} projects" + (f" ({errors} pages failed)" if errors else ""), 1, 1)
    return items
