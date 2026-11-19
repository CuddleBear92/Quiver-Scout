"""Refreshing every source, and comparing the listing sites with Quiver's catalog."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime

from .decisions import BLACKLIST, WAIT_REASONS, WAITING
from . import names
from .sources import (EXTERNAL, Context, Item, clonedgames, decompbuddy, osgameclones, playported, quiver,
                      recomplauncher, trilarion, unbaked)
from .sources.base import Progress, day, no_progress
from .store import Store
from .urls import aliases, normalize_title

# Code hosts Quiver Launcher reads releases from. Self-hosted Gitea/Forgejo sites count once Quiver lists an app
# from them. Projects anywhere else go to "Other sites": Quiver would need to support that site first.
FORGE_HOSTS = {"github.com", "gitlab.com", "codeberg.org", "bitbucket.org"}
FORGE_PROVIDERS = {"github", "gitlab", "gitea", "bitbucket"}

ORDER = ["quiver", *EXTERNAL]


def _fetch_one(store: Store, name: str, ctx: Context) -> list[Item]:
    s = store.settings
    if name == "quiver":
        if s.get("quiver_csv"):
            ctx.progress(f"Quiver: reading {s['quiver_csv']}", 0, 0)
            return quiver.from_csv(s["quiver_csv"])
        cache = {} if ctx.full else store.pages("quiver")
        items = quiver.fetch(ctx, cache=cache)
        store.save_pages("quiver", cache)
        return items
    if name == "recomplauncher":
        return recomplauncher.fetch(ctx)
    if name == "decompbuddy":
        return decompbuddy.fetch(ctx)
    if name == "unbaked":
        cache = store.pages("unbaked")
        try:
            return unbaked.fetch(ctx, full=bool(s.get("unbaked_full_scan")), cache=cache)
        finally:
            if cache:
                store.save_pages("unbaked", cache)
    if name in ("playported", "clonedgames"):
        module = playported if name == "playported" else clonedgames
        cache = store.pages(name)
        try:
            return module.fetch(ctx, cache=cache)
        finally:
            store.save_pages(name, cache)
    if name == "osgameclones":
        return osgameclones.fetch(ctx)
    if name == "trilarion":
        return trilarion.fetch(ctx)
    raise ValueError(name)


def refresh(store: Store, progress: Progress = no_progress, full: bool = False) -> dict:
    """Read every enabled source. Pages and files that haven't changed since the last refresh aren't downloaded
    again, unless `full`. A source that fails keeps its items from the last refresh."""
    old = store.meta("refresh", {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ctx = Context(store, progress, full)
    errors: list[str] = []
    status: dict[str, dict] = {}
    for name in ORDER:
        if name != "quiver" and not store.settings["sources"].get(name, True):
            continue
        started = datetime.now()
        try:
            fetched = [i.to_dict() for i in _fetch_one(store, name, ctx) if i.key]
            store.replace_items(name, fetched)
            status[name] = {"count": len(fetched), "seconds": round((datetime.now() - started).total_seconds(), 1)}
        except Exception as e:  # keep going with the other sites
            errors.append(f"{name}: {e}")
            progress(f"{name} failed: {e}", 1, 1)
            status[name] = {"error": str(e)}

    # First seen: the very first refresh marks everything as already known, so only later finds count as new.
    seen = store.seen()
    first_run = not seen
    new = {}
    for i in store.items():
        if i["source"] != "quiver" and i["key"] not in seen and i["key"] not in new:
            new[i["key"]] = "initial" if first_run else now
    store.add_seen(new)

    result = {"refreshed_at": now, "previous_refresh": old.get("refreshed_at", ""), "errors": errors,
              "sources": status, "new": 0 if first_run else len(new)}
    store.set_meta("refresh", result)
    progress("Done" + (f", with {len(errors)} error(s)" if errors else ""), 1, 1)
    return result


@dataclass
class Candidate:
    """One project, merged from every listing site that has it."""

    key: str
    url: str
    title: str = ""
    game: str = ""
    method: str = ""
    platform: str = ""
    author: str = ""
    added: str = ""  # first listed on any site
    released: str = ""  # newest release any site knows of
    version: str = ""
    pushed: str = ""  # newest push any site knows of
    first_seen: str = ""
    sources: list[str] = field(default_factory=list)
    pages: dict[str, str] = field(default_factory=dict)
    listings: dict[str, Item] = field(default_factory=dict)  # each site's own item
    in_quiver: list[Item] = field(default_factory=list)  # Quiver apps with this repository
    same_game: list[Item] = field(default_factory=list)  # Quiver apps for the same game, from another repository
    decision: dict | None = None
    supported_host: bool = True

    @property
    def host(self) -> str:
        return self.key.split("/")[0]

    @property
    def listed(self) -> bool:
        return bool(self.sources)


@dataclass
class Comparison:
    quiver: list[Item]
    candidates: list[Candidate]
    refresh: dict

    def _open(self) -> list[Candidate]:
        return [c for c in self.candidates if c.listed and not c.in_quiver and not c.decision]

    @property
    def missing(self) -> list[Candidate]:
        """Not in Quiver, not decided on, on a code host Quiver reads."""
        return [c for c in self._open() if c.supported_host]

    @property
    def other_sites(self) -> list[Candidate]:
        """Not in Quiver, not decided on, on a site Quiver would have to support first."""
        return [c for c in self._open() if not c.supported_host]

    def decided(self, which: str) -> list[Candidate]:
        return [c for c in self.candidates if c.decision and c.decision.get("list") == which]

    @property
    def refreshed_at(self) -> str:
        return self.refresh.get("refreshed_at", "")

    def is_new(self, c: Candidate) -> bool:
        return c.first_seen not in ("", "initial") and c.first_seen == self.refreshed_at


def supported_hosts(quiver_items: list[Item]) -> set[str]:
    hosts = set(FORGE_HOSTS)
    for q in quiver_items:
        if q.extra.get("provider", "github") in FORGE_PROVIDERS and q.key:
            hosts.add(q.key.split("/")[0])
    return hosts


def _tidy(i: Item) -> Item:
    """The same words and dates for every site: "ps2" and "PlayStation 2", "2026-11-07T22:02:09" and "2026-11-07"."""
    i.platform = names.platform(i.platform)
    i.method = names.method(i.method)
    i.added, i.released, i.pushed = day(i.added), day(i.released), day(i.pushed)
    return i


def compare(store: Store) -> Comparison:
    clones = bool(store.settings.get("include_clones"))
    items = [_tidy(Item.from_dict(d)) for d in store.items()]
    items = [i for i in items if clones or not i.extra.get("clone")]
    seen = store.seen()
    quiver_items = [i for i in items if i.source == "quiver"]
    hosts = supported_hosts(quiver_items)

    by_key: dict[str, list[Item]] = {}
    by_game: dict[str, list[Item]] = {}
    for q in quiver_items:
        for k in aliases(q.key):
            by_key.setdefault(k, []).append(q)
        for g in q.game.split("; "):
            if normalize_title(g):
                by_game.setdefault(normalize_title(g), []).append(q)

    merged: dict[str, Candidate] = {}
    for i in sorted((i for i in items if i.source != "quiver"), key=lambda i: ORDER.index(i.source)):
        c = merged.get(i.key)
        if not c:
            c = merged[i.key] = Candidate(key=i.key, url=i.url, first_seen=seen.get(i.key, ""))
        if i.source not in c.sources:
            c.sources.append(i.source)
            c.listings[i.source] = i
        if i.page and i.source not in c.pages:
            c.pages[i.source] = i.page
        for attr in ("title", "game", "method", "platform", "author"):
            if not getattr(c, attr) and getattr(i, attr):
                setattr(c, attr, getattr(i, attr))
        if i.added and (not c.added or i.added < c.added):
            c.added = i.added
        if i.released and i.released > c.released:
            c.released, c.version = i.released, i.version
        if i.pushed and i.pushed > c.pushed:
            c.pushed = i.pushed

    # Decided-on projects no site lists any more still show in Waiting and Blacklist.
    for key, d in store.decisions.entries.items():
        if key not in merged:
            merged[key] = Candidate(key=key, url=d.get("url", ""), title=d.get("title", ""), game=d.get("game", ""),
                                    first_seen=seen.get(key, ""))

    for c in merged.values():
        c.in_quiver = [q for k in aliases(c.key) for q in by_key.get(k, [])]
        if not c.in_quiver:
            c.same_game = by_game.get(normalize_title(c.game), [])
        c.decision = store.decisions.get(c.key)
        c.supported_host = c.host in hosts

    return Comparison(
        quiver=quiver_items,
        candidates=sorted(merged.values(), key=lambda c: (c.game or c.title).lower()),
        refresh=store.meta("refresh", {}),
    )


def decide(store: Store, which: str, candidates: list[Candidate], reason: str = "", note: str = "") -> None:
    store.decisions.set(which, [{"key": c.key, "title": c.title, "game": c.game, "url": c.url} for c in candidates],
                        reason=reason, note=note, by=store.settings.get("your_name", ""))


def undecide(store: Store, keys: list[str]) -> None:
    store.decisions.remove(keys)


# ---- CSV exports -------------------------------------------------------------------------------------------------

QUIVER_COLUMNS = ["title", "game", "url", "provider", "slug", "developer", "type", "added", "latest_release",
                  "version", "quiver_page"]
CANDIDATE_COLUMNS = ["title", "game", "url", "host", "sources", "method", "platform", "author", "listed_since",
                     "latest_release", "version", "last_push", "first_seen", "status", "waiting_for", "note",
                     "quiver_apps_for_same_game", *[f"{s}_page" for s in EXTERNAL]]


def write_quiver_csv(path: str, quiver_items: list[Item]) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(QUIVER_COLUMNS)
        for q in sorted(quiver_items, key=lambda q: q.title.lower()):
            w.writerow([q.title, q.game, q.url, q.extra.get("provider", ""), q.slug, q.author, q.method, q.added,
                        q.released, q.version, q.page])
    return len(quiver_items)


def status_of(c: Candidate) -> str:
    if c.in_quiver:
        return "in quiver"
    if c.decision:
        return c.decision.get("list", "")
    return "missing" if c.supported_host else "other site"


def write_candidates_csv(path: str, candidates: list[Candidate]) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CANDIDATE_COLUMNS)
        for c in candidates:
            d = c.decision or {}
            w.writerow([
                c.title, c.game, c.url, c.host, ", ".join(EXTERNAL[s] for s in c.sources), c.method, c.platform,
                c.author, c.added, c.released, c.version, c.pushed, "" if c.first_seen == "initial" else c.first_seen,
                status_of(c), WAIT_REASONS.get(d.get("reason", ""), "") if d.get("list") == WAITING else "",
                d.get("note", ""), "; ".join(q.title for q in c.same_game), *[c.pages.get(s, "") for s in EXTERNAL],
            ])
    return len(candidates)


__all__ = ["BLACKLIST", "WAITING", "Candidate", "Comparison", "compare", "decide", "refresh", "undecide"]
