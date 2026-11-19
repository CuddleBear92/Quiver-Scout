"""The Quiver Launcher catalog, from its public API (the same one the launcher uses) or from a CSV export.

/release-status lists every app with its repository (provider, host, owner/name); /apps gives each app's
name and games. Both are paged 100 at a time.
"""

from __future__ import annotations

import csv
from urllib.parse import quote

from ..net import get_json
from ..urls import from_provider
from .base import Item, Progress, no_progress

API = "https://api.quiverlauncher.com/api/v1"
FALLBACK_API = "https://famous-wildebeest-660.convex.site/api/v1"
SITE = "https://quiverlauncher.com"


def _pages(base: str, path: str, progress: Progress, label: str) -> list[dict]:
    items: list[dict] = []
    cursor = None
    while True:
        url = f"{base}{path}{'&' if '?' in path else '?'}limit=100"
        if cursor:
            url += "&cursor=" + quote(cursor, safe="")
        page = get_json(url)
        items += page.get("items", [])
        progress(f"Quiver: {label} ({len(items)})", len(items), 0)
        cursor = page.get("nextCursor")
        if page.get("isDone") or not cursor:
            return items


def fetch(progress: Progress = no_progress, base: str = API) -> list[Item]:
    try:
        status = _pages(base, "/release-status", progress, "repositories")
    except Exception:
        if base != API:
            raise
        base = FALLBACK_API
        status = _pages(base, "/release-status", progress, "repositories")
    apps = {a["slug"]: a for a in _pages(base, "/apps?sort=name", progress, "app names")}

    items = []
    for s in status:
        slug = s.get("slug", "")
        app = apps.get(slug, {})
        games = [g.get("title", "") for g in app.get("games", [])]
        url = from_provider(s.get("provider", ""), s.get("repository", ""), s.get("host"))
        items.append(
            Item(
                source="quiver",
                url=url,
                title=app.get("projectName") or app.get("name") or slug,
                game="; ".join(g for g in games if g),
                page=f"{SITE}/apps/{slug}" if slug else "",
                method=app.get("projectType", ""),
                platform=", ".join(app.get("consoles", []) or []),
                author=(app.get("developer") or {}).get("name", ""),
                slug=slug,
                extra={"provider": s.get("provider", ""), "name": app.get("name", "")},
            )
        )
    progress(f"Quiver: {len(items)} apps", 1, 1)
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
