"""RecompLauncher (recomplauncher.com): its PC catalog, one JSON file the site and its launcher read."""

from __future__ import annotations

from ..net import get_json
from ..urls import from_provider
from .base import Item, Progress, no_progress

SITE = "https://recomplauncher.com"
CATALOG = f"{SITE}/pc-catalog.json"


def parse(catalog: dict) -> list[Item]:
    items = []
    for a in catalog.get("apps", []):
        repo = (a.get("repo") or "").strip()
        if not repo:
            continue
        url = from_provider(a.get("host") or "github", repo)
        items.append(
            Item(
                source="recomplauncher",
                url=url,
                title=a.get("name", ""),
                game=a.get("game", ""),
                page=f"{SITE}/game/{a['id']}" if a.get("id") else "",
                platform=a.get("origin", ""),
                author=a.get("author", ""),
                added=a.get("added", ""),
                slug=a.get("id", ""),
                extra={
                    k: a[k]
                    for k in ("status", "archived", "soon", "stars", "downloads", "version")
                    if a.get(k) not in (None, "", False)
                },
            )
        )
    return items


def fetch(progress: Progress = no_progress) -> list[Item]:
    progress("RecompLauncher: downloading catalog", 0, 0)
    items = parse(get_json(CATALOG, timeout=60))
    progress(f"RecompLauncher: {len(items)} projects", 1, 1)
    return items
