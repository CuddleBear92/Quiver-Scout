"""The Quiver Launcher catalog, from its public API (the same one the launcher uses) or from a CSV export.

/release-status lists every app with its repository (provider, host, owner/name); /apps gives each app's
name and games. Both are paged 100 at a time. /release-status is read every refresh (it has each repository's
newest release); /apps only for apps not seen before, newest first, stopping once it has them all.
"""

from __future__ import annotations

import csv
from urllib.parse import quote

from .. import net
from ..urls import from_provider
from .base import Context, Item, day

API = "https://api.quiverlauncher.com/api/v1"
FALLBACK_API = "https://famous-wildebeest-660.convex.site/api/v1"
SITE = "https://quiverlauncher.com"

# More unknown apps than this and reading the whole list by name is quicker than paging newest-first.
FULL_LIST_ABOVE = 300


def _pages(ctx: Context, base: str, path: str, label: str, until=None) -> list[dict]:
    """Every page of a list, or until `until(items so far)` says enough."""
    items: list[dict] = []
    cursor = None
    while True:
        url = f"{base}{path}{'&' if '?' in path else '?'}limit=100"
        if cursor:
            url += "&cursor=" + quote(cursor, safe="")
        page = net.get_json(url)
        items += page.get("items", [])
        ctx.progress(f"Quiver: {label} ({len(items)})", len(items), 0)
        cursor = page.get("nextCursor")
        if page.get("isDone") or not cursor or (until and until(items)):
            return items


def _summary(app: dict) -> dict:
    """What's kept of an app between refreshes: what doesn't change with its releases."""
    return {
        "name": app.get("name", ""),
        "projectName": app.get("projectName", ""),
        "games": [g.get("title", "") for g in app.get("games", [])],
        "consoles": app.get("consoles") or [],
        "projectType": app.get("projectType", ""),
        "developer": (app.get("developer") or {}).get("name", ""),
        "addedAt": app.get("addedAt"),
    }


def fetch(ctx: Context, cache: dict | None = None, base: str = API) -> list[Item]:
    """`cache` (slug -> {"data": app summary}) is updated in place: only apps it doesn't know are asked for."""
    cache = cache if cache is not None else {}
    try:
        status = _pages(ctx, base, "/release-status", "repositories")
    except Exception:
        if base != API:
            raise
        base = FALLBACK_API
        status = _pages(ctx, base, "/release-status", "repositories")

    slugs = {s.get("slug") for s in status}
    unknown = set(slugs) if ctx.full else {s for s in slugs if s not in cache}
    if unknown:
        if len(unknown) > FULL_LIST_ABOVE:
            apps = _pages(ctx, base, "/apps?sort=name", "app names")
        else:
            apps = _pages(ctx, base, "/apps?sort=added", f"names of {len(unknown)} new apps",
                          until=lambda got: unknown <= {a.get("slug") for a in got})
        for a in apps:
            cache[a["slug"]] = {"version": "", "data": _summary(a)}
    for gone in set(cache) - slugs:
        del cache[gone]

    items = []
    for s in status:
        slug = s.get("slug", "")
        app = (cache.get(slug) or {}).get("data") or {}
        url = from_provider(s.get("provider", ""), s.get("repository", ""), s.get("host"))
        latest = s.get("latestUpstream") or s.get("verified") or {}  # the repository's newest release
        items.append(
            Item(
                source="quiver",
                url=url,
                title=app.get("projectName") or app.get("name") or slug,
                game="; ".join(g for g in app.get("games", []) if g),
                page=f"{SITE}/apps/{slug}" if slug else "",
                method=app.get("projectType", ""),
                platform=", ".join(app.get("consoles", [])),
                author=app.get("developer", ""),
                added=day(app.get("addedAt")),
                released=day(latest.get("releasedAt")),
                version=latest.get("version", ""),
                slug=slug,
                extra={"provider": s.get("provider", ""), "name": app.get("name", "")},
            )
        )
    ctx.progress(f"Quiver: {len(items)} apps", 1, 1)
    return items


# Column names a CSV export might use, most likely first.
TITLE_COLUMNS = ("title", "projectname", "project", "name", "app")
URL_COLUMNS = ("url", "repository", "repo", "source", "sourceurl", "repourl", "github", "link")
GAME_COLUMNS = ("game", "games", "gametitle")
SLUG_COLUMNS = ("slug", "id")


def _pick(header: dict[str, str], names: tuple[str, ...]) -> str | None:
    """The column named exactly one of `names`, else the first whose name contains one ("Repository URL")."""
    for n in names:
        if n in header:
            return header[n]
    for n in names:
        for h, original in header.items():
            if n in h:
                return original
    return None


def from_csv(path: str) -> list[Item]:
    """Quiver's catalog from a CSV file with at least a URL column, and ideally title, game and slug columns."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = {"".join(ch for ch in (h or "").lower() if ch.isalnum()): h for h in reader.fieldnames or []}
        url_col = _pick(header, URL_COLUMNS)
        if not url_col:
            raise ValueError(f"{path}: no URL column (expected one of: {', '.join(URL_COLUMNS)})")
        title_col, game_col, slug_col = _pick(header, TITLE_COLUMNS), _pick(header, GAME_COLUMNS), _pick(header, SLUG_COLUMNS)
        items = []
        for row in reader:
            url = (row.get(url_col) or "").strip()
            if not url:
                continue
            slug = (row.get(slug_col) or "").strip() if slug_col else ""
            items.append(
                Item(
                    source="quiver",
                    url=url,
                    title=(row.get(title_col) or "").strip() if title_col else "",
                    game=(row.get(game_col) or "").strip() if game_col else "",
                    page=f"{SITE}/apps/{slug}" if slug else "",
                    slug=slug,
                )
            )
        return items
