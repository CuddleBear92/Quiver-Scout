"""Decomp Buddy (decompbuddy.com): its whole catalog as one JSON file, published for anyone to use.

Each project has its latest release (version and date), when Decomp Buddy listed it, its repository's last push,
and which systems it publishes builds for.
"""

from __future__ import annotations

from .base import Context, Item, day

SITE = "https://decompbuddy.com"
CATALOG = "https://tanookistudios.com/decomp-buddy/catalog.json"


def parse(catalog: dict) -> list[Item]:
    items = []
    for g in catalog.get("games", []):
        url = g.get("repoUrl") or ""
        if not url:
            continue
        facts = g.get("facts") or {}
        extra = {
            "stars": g.get("stars"),
            "downloads": g.get("downloads"),
            "builds": ", ".join(g.get("platforms") or []),
            "prerelease": g.get("status") == "prerelease",
            "archived": facts.get("archived"),
            "ai": facts.get("aiUse") if facts.get("aiUse") not in (None, "none", "unknown") else None,
            "completeness": facts.get("completeness") if facts.get("completeness") not in (None, "unknown") else None,
            "taken_down": facts.get("takedown"),
            "via": ", ".join(g.get("sources") or []),
        }
        items.append(
            Item(
                source="decompbuddy",
                url=url,
                title=g.get("project") or g.get("title", ""),
                game=g.get("title", ""),
                page=f"{SITE}/games/{g['slug']}" if g.get("slug") else "",
                platform=g.get("console", ""),
                author=(g.get("repo") or "").split("/")[0],
                added=day(g.get("listedAt")),
                released=day(g.get("updatedAt")) if g.get("version") else "",
                version=g.get("version") or "",
                pushed=day(facts.get("pushedAt")),
                slug=g.get("slug", ""),
                extra={k: v for k, v in extra.items() if v not in (None, "", False)},
            )
        )
    return items


def fetch(ctx: Context) -> list[Item]:
    ctx.progress("Decomp Buddy: catalog", 0, 0)
    data, changed = ctx.json(CATALOG, timeout=120)
    items = parse(data)
    ctx.progress(f"Decomp Buddy: {len(items)} projects" + ("" if changed else " (unchanged since last refresh)"), 1, 1)
    return items
