"""PlayPorted (playported.com): console games ported to PC, one page per port.

There is no JSON API, so the sitemap gives every port page and when it last changed, and each page gives the
port's project link. Pages are kept in the cache and only read again when the sitemap says they changed.
"""

from __future__ import annotations

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..net import get_text
from .base import Item, Progress, no_progress

SITE = "https://playported.com"
SITEMAP = f"{SITE}/sitemap.xml"

_URL = re.compile(r"<url>\s*<loc>([^<]+/ports/[^<]+)</loc>\s*(?:<lastmod>([^<]+)</lastmod>)?", re.S)
_LD = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)
_NAME = re.compile(r'class="project-name"[^>]*>(.*?)</p>', re.S)
_META = re.compile(r'class="project-meta"[^>]*>(.*?)</p>', re.S)
_LINK = re.compile(r'<a class="btn primary" href="([^"]+)"')
_TAGS = re.compile(r"<[^>]+>")


def parse_sitemap(xml: str) -> dict[str, str]:
    """Port page URL -> lastmod."""
    return {loc.strip(): (lastmod or "").strip() for loc, lastmod in _URL.findall(xml)}


def _text(fragment: str) -> str:
    return html.unescape(_TAGS.sub("", fragment)).strip()


def parse_port(page_url: str, page: str) -> dict | None:
    """The fields of one port page, or None when it has no project link."""
    link = _LINK.search(page)
    if not link:
        return None
    game, platform, modified = "", "", ""
    for block in _LD.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in data.get("@graph", [data]):
            if node.get("@type") == "VideoGame":
                game, platform = node.get("name", ""), node.get("gamePlatform", "")
            elif node.get("@type") == "WebPage":
                modified = node.get("dateModified", "")
    name = _NAME.search(page)
    meta = _text(_META.search(page).group(1)) if _META.search(page) else ""
    parts = [p.strip() for p in meta.split("·")]
    author = next((p[3:] for p in parts if p.startswith("by ")), "")
    pid = re.search(r"/ports/(\d+)-", page_url)
    return {
        "url": html.unescape(link.group(1)),
        "title": _text(name.group(1)) if name else "",
        "game": html.unescape(game),
        "platform": platform,
        "method": parts[0] if parts else "",
        "author": author,
        "added": modified[:10],
        "slug": pid.group(1) if pid else "",
    }


def _item(page_url: str, d: dict) -> Item:
    return Item(
        source="playported",
        url=d["url"],
        title=d["title"],
        game=d["game"],
        page=page_url,
        method=d["method"],
        platform=d["platform"],
        author=d["author"],
        added=d["added"],
        slug=d["slug"],
    )


def fetch(progress: Progress = no_progress, cache: dict | None = None, workers: int = 4) -> list[Item]:
    """`cache` (page URL -> {"lastmod", "port"}) is updated in place."""
    cache = cache if cache is not None else {}
    progress("PlayPorted: reading sitemap", 0, 0)
    pages = parse_sitemap(get_text(SITEMAP, timeout=60))
    stale = [u for u, mod in pages.items() if u not in cache or cache[u].get("lastmod") != mod]
    progress(f"PlayPorted: {len(stale)} of {len(pages)} port pages to read", 0, len(stale))

    def one(url: str):
        return url, parse_port(url, get_text(url))

    errors, done = 0, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(one, u) for u in stale]):
            done += 1
            try:
                url, port = future.result()
                cache[url] = {"lastmod": pages[url], "port": port}
            except Exception:
                errors += 1
            if done % 25 == 0 or done == len(stale):
                progress(f"PlayPorted: port pages {done}/{len(stale)}", done, len(stale))

    for gone in set(cache) - set(pages):
        del cache[gone]
    items = [_item(u, c["port"]) for u, c in cache.items() if c.get("port")]
    progress(f"PlayPorted: {len(items)} ports" + (f" ({errors} pages failed)" if errors else ""), 1, 1)
    return items
