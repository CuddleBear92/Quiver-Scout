"""Posts on X (Twitter) accounts that announce new recomps and ports, read through FxTwitter.

X itself shows timelines only to logged-in users, and its API costs money. FxTwitter (fxtwitter.com, the service
that fixes X links' previews) has a public API, no login needed:

  /2/profile/<account>/statuses[?cursor=...]   an account's posts, about 20 a page, newest first, links expanded;
                                               each page's cursor.bottom asks for the page before it
  /<account>                                   the account: name, website, how many posts it has

It answers 404 when asked too often, so pages are asked 10 seconds apart, and again after 20 to 90 seconds when it
says not now. Each refresh:

  1. catches up: reads from the newest post back until it meets a post already read,
  2. fills in the history: carries on back from where the last refresh stopped, at most HISTORY_PAGES pages,
     until the account's first post. A big account's history takes a few refreshes; a small one's, one.

Every post read is kept. From each: its links to projects (not to X, YouTube, Discord and the like, nor the account's
own website), and the game, from the way these accounts write ("FIRE EMBLEM WARRIORS gets a native ..."). Each link
becomes an item of the Twitter/X source, which lists it like any site.
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import quote
from datetime import datetime, timezone

from .. import net
from ..urls import canonical
from .base import Context, Item

API = "https://api.fxtwitter.com/2/profile/{}/statuses"
ACCOUNT_API = "https://api.fxtwitter.com/{}"
PROFILE = "https://x.com/{}"
HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

# Links in posts that aren't projects.
SKIP_HOSTS = re.compile(
    r"(^|\.)(x\.com|twitter\.com|t\.co|fxtwitter\.com|fixupx\.com|twimg\.com|youtube\.com|youtu\.be|discord\.gg|"
    r"discord\.com|patreon\.com|ko-fi\.com|twitch\.tv|instagram\.com|tiktok\.com|bsky\.app|reddit\.com|"
    r"facebook\.com|threads\.net|linktr\.ee|buymeacoffee\.com|paypal\.com|kick\.com)$")
# Words that end the game's name in "GAME gets a native PC port": the name is what comes before.
GAME_END = re.compile(r"\s+(?:gets|is|are|now|has|have|receives|lands|arrives|comes|hits|goes|runs)\b", re.I)
SMALL = {"of", "the", "a", "an", "and", "in", "on", "for", "to", "with", "at", "by", "or", "vs"}
# File names X turns into links (.zip is a real domain ending): no path, and a file's extension.
FILE_NAME = re.compile(r"^[^/]+\.(zip|rar|7z|exe|iso|rom|bin|z64|n64|nds|gba|wad|pak|xex|elf|nsp|xci|apk|tar|gz)$", re.I)
ROMAN = {"II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "PC", "HD", "3D", "2D", "VR"}

WAITS = (20, 40, 60, 90)  # seconds before asking again when FxTwitter says not now
BETWEEN_PAGES = 10
HISTORY_PAGES = 50  # pages of history (about 1,000 posts) read back per account per refresh


def handle_of(text: str) -> str | None:
    """"@Name", "x.com/Name", "https://twitter.com/Name/status/1" ... -> "Name", or None."""
    text = (text or "").strip()
    m = re.search(r"(?:x\.com|twitter\.com|fxtwitter\.com|fixupx\.com|nitter\.[a-z.]+)/(@?[A-Za-z0-9_]{1,15})", text)
    name = m.group(1) if m else text
    name = name.lstrip("@").strip("/ ")
    return name if HANDLE.match(name) else None


def _title_case(words: str) -> str:
    """"FIRE EMBLEM WARRIORS" -> "Fire Emblem Warriors", keeping II, 3D, PC and the like."""
    if not words.isupper():
        return words
    out = []
    for n, w in enumerate(words.split(" ")):
        core = re.sub(r"[^A-Za-z0-9]", "", w)
        if core.upper() in ROMAN or any(ch.isdigit() for ch in core):
            out.append(w)
        elif n and core.lower() in SMALL and not out[-1].endswith(":"):
            out.append(w.lower())
        else:
            out.append(w[:1] + w[1:].lower())
    return " ".join(out)


def game_of(first_line: str) -> str:
    """The game a post is about, from its first line; "" when it doesn't read like one."""
    line = re.sub(r"^[^\w]+", "", first_line or "").strip()  # leading emoji
    m = GAME_END.search(line)
    if not m or m.start() < 2:
        return ""
    return _title_case(line[:m.start()].strip(" :-–—"))


RUNS_ON = [("PC", r"\b(windows|pc)\b"), ("Linux", r"\blinux\b"), ("Mac", r"\b(mac|macos|osx)\b"),
           ("Android", r"\bandroid\b"), ("iOS", r"\b(ios|iphone)\b"), ("Switch", r"\bswitch\b"),
           ("PS Vita", r"\bvita\b"), ("Web", r"\b(browser|web)\b"), ("Meta Quest", r"\bquest\b")]


def runs_on_of(first_line: str) -> str:
    """Where a post says the port runs: "gets a native Windows and Linux recompilation" -> "PC, Linux"."""
    tail = GAME_END.split(first_line or "", maxsplit=1)
    text = (tail[-1] if len(tail) > 1 else first_line or "").lower()
    return ", ".join(name for name, pattern in RUNS_ON if re.search(pattern, text))


def method_of(text: str) -> str:
    t = text.lower()
    if "decompil" in t:
        return "Decomp"
    if "recompil" in t or "recomp" in t:
        return "Recomp"
    if "browser" in t:
        return "Web port"
    if "port" in t:
        return "Port"
    return ""


def parse_post(post: dict, own_site: str = "") -> dict:
    """What's kept of a post: id, url, account, date, first line, game, method, project links, likes, views."""
    facets = ((post.get("raw_text") or {}).get("facets")) or []
    urls = [f.get("replacement") or "" for f in facets if f.get("type") == "url"]
    if not facets:  # older answers: links straight from the text
        urls = re.findall(r"https?://[^\s)\]]+", post.get("text") or "")
    links: list[str] = []
    for u in urls:
        c = canonical(u)
        if not c:
            continue
        host = c[0].split("/")[0]
        if SKIP_HOSTS.search(host) or (own_site and host == own_site) or FILE_NAME.match(c[0]):
            continue
        if c[1] not in links:
            links.append(c[1])
    text = post.get("text") or ""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    stamp = post.get("created_timestamp")
    when = datetime.fromtimestamp(stamp, tz=timezone.utc) if stamp else None
    date = when.strftime("%Y-%m-%d") if when else ""
    return {
        "id": str(post.get("id", "")),
        "url": post.get("url", ""),
        "account": ((post.get("author") or {}).get("screen_name")) or "",
        "date": date,
        "time": when.strftime("%Y-%m-%d %H:%M") if when else "",
        "text": first[:300],
        "game": game_of(first),
        "method": method_of(text),
        "runs_on": runs_on_of(first),
        "links": links,
        "likes": post.get("likes") or 0,
        "views": post.get("views") or 0,
    }


def _get(url: str, sleep, progress=None, what: str = "") -> dict:
    """One FxTwitter answer, asking again after a pause when it says not now."""
    last = None
    for wait in (0, *WAITS):
        if wait:
            if progress:
                progress(f"Twitter/X: {what} not answered yet, asking again in {wait} s", 0, 0)
            sleep(wait)
        try:
            return json.loads(net.request(url, retries=1, timeout=30).body)
        except (net.HTTPStatusError, net.FetchError, ValueError) as e:
            last = e
    raise net.FetchError(f"FxTwitter didn't answer ({last})")


def account_info(name: str, sleep=time.sleep, progress=None) -> dict:
    """The account's name, website, how many posts it has, and its own site (whose links aren't projects)."""
    user = (_get(ACCOUNT_API.format(name), sleep, progress, f"@{name}") or {}).get("user") or {}
    site = ((user.get("website") or {}).get("url")) or ""
    return {"name": user.get("name", ""), "website": site, "posts_on_x": user.get("tweets") or 0,
            "own_site": (canonical(site) or ("", ""))[0].split("/")[0] if site else ""}


def read_pages(name: str, known: set[str], own_site: str, cursor: str | None, pages: int, sleep=time.sleep,
               progress=None, stop_at_known: bool = True, on_page=None) -> tuple[list[dict], str | None, bool]:
    """Read back from `cursor` (None: the newest post): up to `pages` pages, stopping at a page with a post in
    `known` when `stop_at_known`. Returns (posts, the cursor to carry on from, whether the account's first post was
    reached). `on_page(posts, next_cursor)` is called after each page, so what's read is kept even when a later page
    fails."""
    posts: list[dict] = []
    for n in range(pages):
        if n:
            sleep(BETWEEN_PAGES)
        url = API.format(name) + (f"?cursor={quote(cursor, safe='')}" if cursor else "")
        data = _get(url, sleep, progress, f"@{name}")
        results = [p for p in data.get("results") or [] if p.get("type") == "status"]
        if not results:
            return posts, cursor, True  # no more: the first post was reached
        page = [parse_post(p, own_site) for p in results]
        posts += page
        nxt = (data.get("cursor") or {}).get("bottom")
        if on_page:
            on_page(page, nxt)
        if progress:
            progress(f"Twitter/X: @{name}: {len(posts)} posts read, back to {page[-1]['date']}", 0, 0)
        if stop_at_known and any(p["id"] in known for p in page):
            return posts, nxt, False
        if not nxt or nxt == cursor:
            return posts, None, True
        cursor = nxt
    return posts, cursor, False


def items_from(cache: dict) -> list[Item]:
    """One item per project link in the posts kept."""
    items = []
    for url, entry in cache.items():
        post = entry.get("data") or {}
        for link in post.get("links", []):
            c = canonical(link)
            name = c[0].rstrip("/").rsplit("/", 1)[-1] if c and c[0].count("/") >= 1 else link
            items.append(Item(
                source="twitter",
                url=link,
                title=name,
                game=post.get("game", ""),
                page=post.get("url") or url,
                method=post.get("method", ""),
                author=(c[0].split("/")[1] if c and c[0].count("/") >= 2 else ""),
                added=post.get("date", ""),
                slug=post.get("id", ""),
                extra={k: v for k, v in {"post": post.get("text", ""), "account": post.get("account", ""),
                                         "likes": post.get("likes"), "views": post.get("views"),
                                         "runs_on": post.get("runs_on", "")}.items() if v},
            ))
    return items


def fetch(ctx: Context, accounts: list[str], cache: dict | None = None, status: dict | None = None,
          sleep=time.sleep) -> list[Item]:
    """`cache` (post url -> {"version", "data": post}) and `status` (account -> what's known of it and its reading)
    are updated in place. Posts of accounts no longer followed are dropped."""
    cache = cache if cache is not None else {}
    status = status if status is not None else {}
    followed = {a.lower() for a in accounts}
    for url in [u for u, e in cache.items() if (e.get("data") or {}).get("account", "").lower() not in followed]:
        del cache[url]
    for gone in [a for a in status if a.lower() not in followed]:
        del status[gone]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for n, name in enumerate(accounts):
        if n:
            sleep(BETWEEN_PAGES)
        ctx.progress(f"Twitter/X: @{name} ({n + 1} of {len(accounts)})", n, len(accounts))
        st = dict(status.get(name) or {})
        mine = [e["data"] for e in cache.values() if (e.get("data") or {}).get("account", "").lower() == name.lower()]
        known = {p["id"] for p in mine}
        before = len(cache)

        def keep(posts: list[dict], _cursor=None) -> None:
            for p in posts:
                if p.get("url"):
                    # Kept under the account it was read from; a repost's own author stays as "author".
                    p = {**p, "author": p.get("author") or p.get("account", ""), "account": name}
                    cache[p["url"]] = {"version": p["id"], "data": p}

        def keep_history(posts: list[dict], cursor) -> None:
            keep(posts)
            st["history_cursor"] = cursor  # carried on from here next time, should a later page fail

        try:
            st.update(account_info(name, sleep, ctx.progress))
            sleep(BETWEEN_PAGES)
            if not known or ctx.full or st.pop("restart", False):
                # First read (or a full refresh): from the newest post back, as far as this refresh goes.
                posts, cursor, done = read_pages(name, set(), st.get("own_site", ""), None, HISTORY_PAGES, sleep,
                                                 ctx.progress, stop_at_known=False, on_page=keep_history)
                st["history_cursor"], st["complete"] = cursor, done
            else:
                # Catch up from the newest post to the posts already read...
                read_pages(name, known, st.get("own_site", ""), None, HISTORY_PAGES, sleep, ctx.progress, on_page=keep)
                # ...then carry on with the history where the last refresh stopped.
                if not st.get("complete") and st.get("history_cursor"):
                    sleep(BETWEEN_PAGES)
                    older, cursor, done = read_pages(name, set(), st.get("own_site", ""), st["history_cursor"],
                                                     HISTORY_PAGES, sleep, ctx.progress, stop_at_known=False,
                                                     on_page=keep_history)
                    st["history_cursor"], st["complete"] = cursor, done
            st.pop("error", None)
            st["read"] = now
        except Exception as e:  # keep what was read; the next refresh carries on
            st.update({"error": str(e), "tried": now})
            ctx.progress(f"Twitter/X: @{name} failed ({e}); keeping the posts read so far", n + 1, len(accounts))
        mine = [e["data"] for e in cache.values() if (e.get("data") or {}).get("account", "").lower() == name.lower()]
        st["new"] = len(cache) - before
        st["posts"] = len(mine)
        st["last_post"] = max((p.get("time") or p.get("date", "") for p in mine), default="")
        status[name] = st
        history = "whole history read" if st.get("complete") else "more history on the next refresh"
        ctx.progress(f"Twitter/X: @{name}: {st['new']} new post(s), {st['posts']} kept, {history}", n + 1, len(accounts))
    items = items_from(cache)
    ctx.progress(f"Twitter/X: {len(items)} project link(s) in {len(cache)} post(s)", 1, 1)
    return items
