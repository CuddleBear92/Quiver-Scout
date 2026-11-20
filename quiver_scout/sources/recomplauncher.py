"""RecompLauncher (recomplauncher.com): its PC catalog, one JSON file the site and its launcher read, and its player
reports, one more (the scores its game pages show: "Plays great · 98 reports").

From the catalog: GitHub stars, downloads, and players, which is how many PCs played it through RecompLauncher in the
last 90 days. From the reports: the score (0-100) and how many reports it's from.
"""

from __future__ import annotations

from ..urls import from_provider
from .base import Context, Item, day

SITE = "https://recomplauncher.com"
CATALOG = f"{SITE}/pc-catalog.json"
SCORES = f"{SITE}/api/play.php?a=scores"


def parse(catalog: dict, scores: dict | None = None) -> list[Item]:
    """`scores`: app id -> {score, n, verdicts...}, from SCORES' "pc"."""
    scores = scores or {}
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
                    **{k: a[k] for k in ("status", "archived", "soon", "stars", "downloads", "players", "releases", "first")
                       if a.get(k) not in (None, "", False)},
                    **({"score": scores[a["id"]].get("score"), "reports": scores[a["id"]].get("n")}
                       if isinstance(scores.get(a.get("id")), dict) and scores[a["id"]].get("n") else {}),
                    **({"runs_on": ", ".join(a["systems"])} if a.get("systems") else {}),
                },
            )
        )
    return items


def fetch(ctx: Context) -> list[Item]:
    ctx.progress("RecompLauncher: catalog", 0, 0)
    data, changed = ctx.json(CATALOG, timeout=120)
    try:
        scores = (ctx.json(SCORES, timeout=60)[0] or {}).get("pc") or {}
    except Exception as e:  # the catalog is what matters; scores are a bonus
        ctx.progress(f"RecompLauncher: player reports failed: {e}", 0, 0)
        scores = {}
    items = parse(data, scores)
    ctx.progress(f"RecompLauncher: {len(items)} projects" + ("" if changed else " (unchanged since last refresh)"), 1, 1)
    return items
