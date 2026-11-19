"""unbaked (unbaked.games): a registry of decompiled, recompiled and open-source games, with a JSON API.

The quick scan reads /api/additions.json: every project added in the last several weeks, with its repository.
The full scan also reads each game's record (/api/games/{slug}.json, one request per game with public code) and
keeps them, asking again only for games whose newest push changed.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import net
from .base import Context, Item, day

SITE = "https://unbaked.games"
API = f"{SITE}/api"

# Projects that aren't something a launcher could offer to play.
SKIP_METHODS = {"Agent environment"}


def _keep(project: dict) -> bool:
    methods = set(project.get("methods") or [])
    return bool(project.get("url")) and not (methods and methods <= SKIP_METHODS) and not project.get("general_tool")


def _item(game: dict, project: dict, added: str = "") -> Item:
    return Item(
        source="unbaked",
        url=project["url"],
        title=project.get("name", ""),
        game=game.get("title", ""),
        page=game.get("url") or f"{SITE}/games/{game.get('slug') or game.get('game')}/",
        method=", ".join(m for m in project.get("methods") or [] if m not in SKIP_METHODS),
        platform=game.get("platform", ""),
        added=day(project.get("added") or added),
        pushed=day(project.get("last_push")),
        slug=game.get("slug") or game.get("game", ""),
        extra={**{k: project[k] for k in ("stars", "archived", "license") if project.get(k) not in (None, "", False)},
               **({"runs_on": ", ".join(project["runs_on"])} if project.get("runs_on") else {})},
    )


def parse_additions(data: dict) -> list[Item]:
    items = []
    for week in data.get("weeks", []):
        for game in week.get("games", []):
            for p in game.get("projects", []):
                if _keep(p):
                    items.append(_item(game, p, game.get("added", "")))
    return items


def parse_game(record: dict) -> list[Item]:
    return [_item(record, p) for p in record.get("projects", []) if _keep(p)]


def fetch(ctx: Context, full: bool = False, cache: dict | None = None, workers: int = 4) -> list[Item]:
    """`cache` (slug -> {"version": last push, "data": record}) is updated in place for the full scan."""
    ctx.progress("unbaked: recent additions", 0, 0)
    data, changed = ctx.json(f"{API}/additions.json")
    items = parse_additions(data)
    if not full:
        ctx.progress(f"unbaked: {len(items)} recent projects" + ("" if changed else " (unchanged)"), 1, 1)
        return items

    cache = cache if cache is not None else {}
    games = [g for g in ctx.json(f"{API}/games.json")[0].get("games", []) if g.get("has_public_code")]
    stale = [g for g in games if ctx.full or (cache.get(g["slug"]) or {}).get("version") != g.get("last_push")]
    ctx.progress(f"unbaked: {len(stale)} of {len(games)} game records to read", 0, len(stale))

    def one(g: dict):
        return g, net.get_json(g.get("json") or f"{API}/games/{g['slug']}.json")

    errors, done = 0, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(one, g) for g in stale]):
            done += 1
            try:
                g, record = future.result()
                cache[g["slug"]] = {"version": g.get("last_push"), "data": record}
            except Exception:
                errors += 1
            if done % 25 == 0 or done == len(stale):
                ctx.progress(f"unbaked: game records {done}/{len(stale)}", done, len(stale))

    seen = {i.key for i in items}
    for g in games:
        entry = cache.get(g["slug"])
        if not entry or not entry.get("data"):
            continue
        for item in parse_game(entry["data"]):
            if item.key not in seen:
                seen.add(item.key)
                items.append(item)
    ctx.progress(f"unbaked: {len(items)} projects" + (f" ({errors} games failed)" if errors else ""), 1, 1)
    return items
