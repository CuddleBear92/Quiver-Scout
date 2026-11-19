"""RecompLauncher (recomplauncher.com): its PC catalog, one JSON file the site and its launcher read."""

from __future__ import annotations

from ..urls import from_provider
from .base import Context, Item, day

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
                added=day(a.get("added")),
                released=day(a.get("released")),
                version=a.get("tag") or a.get("version") or "",
                slug=a.get("id", ""),
                extra={
                    **{k: a[k] for k in ("status", "archived", "soon", "stars", "downloads", "releases", "first")
                       if a.get(k) not in (None, "", False)},
                    **({"runs_on": ", ".join(a["systems"])} if a.get("systems") else {}),
                },
            )
        )
    return items


def fetch(ctx: Context) -> list[Item]:
    ctx.progress("RecompLauncher: catalog", 0, 0)
    data, changed = ctx.json(CATALOG, timeout=120)
    items = parse(data)
    ctx.progress(f"RecompLauncher: {len(items)} projects" + ("" if changed else " (unchanged since last refresh)"), 1, 1)
    return items
