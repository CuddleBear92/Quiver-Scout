"""Refreshing every source, and comparing the listing sites with Quiver's catalog."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime

from .sources import EXTERNAL, Item, playported, quiver, recomplauncher, unbaked
from .sources.base import Progress, no_progress
from .store import Store
from .urls import aliases, normalize_title


def _fetch_one(store: Store, name: str, progress: Progress) -> list[Item]:
    s = store.settings
    if name == "quiver":
        if s.get("quiver_csv"):
            progress(f"Quiver: reading {s['quiver_csv']}", 0, 0)
            return quiver.from_csv(s["quiver_csv"])
        return quiver.fetch(progress)
    if name == "recomplauncher":
        return recomplauncher.fetch(progress)
    if name == "unbaked":
        cache = store.cache("unbaked")
        try:
            return unbaked.fetch(progress, full=bool(s.get("unbaked_full_scan")), cache=cache)
        finally:
            if cache:
                store.save_cache("unbaked", cache)
    if name == "playported":
        cache = store.cache("playported")
        try:
            return playported.fetch(progress, cache=cache)
        finally:
            store.save_cache("playported", cache)
    raise ValueError(name)


def refresh(store: Store, progress: Progress = no_progress) -> dict:
    """Read every enabled source and save a new snapshot. A source that fails keeps its items from the last one."""
    old = store.snapshot or {}
    old_items = {name: [i for i in old.get("items", []) if i["source"] == name] for name in ["quiver", *EXTERNAL]}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    items: list[dict] = []
    errors: list[str] = []
    for name in ["quiver", *EXTERNAL]:
        if name != "quiver" and not store.settings["sources"].get(name, True):
            continue
        try:
            fetched = [i.to_dict() for i in _fetch_one(store, name, progress) if i.key]
        except Exception as e:  # keep going with the other sites
            errors.append(f"{name}: {e}")
            progress(f"{name} failed: {e}", 1, 1)
            fetched = old_items.get(name, [])
        items += fetched

    # First seen: the very first refresh marks everything as already known, so only later finds count as new.
    first_run = not store.seen
    for i in items:
        if i["source"] != "quiver" and i["key"] not in store.seen:
            store.seen[i["key"]] = "initial" if first_run else now
    store.save_seen()

    store.snapshot = {"refreshed_at": now, "previous_refresh": old.get("refreshed_at", ""), "items": items, "errors": errors}
    store.save_snapshot()
    progress("Done" + (f", with {len(errors)} error(s)" if errors else ""), 1, 1)
    return store.snapshot


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
    added: str = ""
    first_seen: str = ""
    sources: list[str] = field(default_factory=list)
    pages: dict[str, str] = field(default_factory=dict)
    in_quiver: list[Item] = field(default_factory=list)  # Quiver apps with this repository
    same_game: list[Item] = field(default_factory=list)  # Quiver apps for the same game, from another repository
    blacklisted: dict | None = None


@dataclass
class Comparison:
    quiver: list[Item]
    candidates: list[Candidate]
    refreshed_at: str
    previous_refresh: str
    errors: list[str]

    @property
    def missing(self) -> list[Candidate]:
        return [c for c in self.candidates if not c.in_quiver and not c.blacklisted]

    @property
    def blacklisted(self) -> list[Candidate]:
        return [c for c in self.candidates if c.blacklisted and not c.in_quiver]

    def is_new(self, c: Candidate) -> bool:
        return c.first_seen not in ("", "initial") and c.first_seen == self.refreshed_at


def compare(store: Store) -> Comparison:
    snap = store.snapshot or {}
    items = [Item.from_dict(d) for d in snap.get("items", [])]
    quiver_items = [i for i in items if i.source == "quiver"]

    by_key: dict[str, list[Item]] = {}
    by_game: dict[str, list[Item]] = {}
    for q in quiver_items:
        for k in aliases(q.key):
            by_key.setdefault(k, []).append(q)
        for g in q.game.split("; "):
            if normalize_title(g):
                by_game.setdefault(normalize_title(g), []).append(q)

    merged: dict[str, Candidate] = {}
    for i in items:
        if i.source == "quiver":
            continue
        c = merged.get(i.key)
        if not c:
            c = merged[i.key] = Candidate(key=i.key, url=i.url, first_seen=store.seen.get(i.key, ""))
        if i.source not in c.sources:
            c.sources.append(i.source)
        if i.page and i.source not in c.pages:
            c.pages[i.source] = i.page
        for attr in ("title", "game", "method", "platform", "author"):
            if not getattr(c, attr) and getattr(i, attr):
                setattr(c, attr, getattr(i, attr))
        if i.added and (not c.added or i.added < c.added):
            c.added = i.added

    order = list(EXTERNAL)
    for c in merged.values():
        c.sources.sort(key=order.index)
        c.in_quiver = [q for k in aliases(c.key) for q in by_key.get(k, [])]
        if not c.in_quiver:
            c.same_game = by_game.get(normalize_title(c.game), [])
        c.blacklisted = store.blacklist.get(c.key)

    return Comparison(
        quiver=quiver_items,
        candidates=sorted(merged.values(), key=lambda c: (c.game or c.title).lower()),
        refreshed_at=snap.get("refreshed_at", ""),
        previous_refresh=snap.get("previous_refresh", ""),
        errors=snap.get("errors", []),
    )


def blacklist(store: Store, candidates: list[Candidate], reason: str = "") -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    for c in candidates:
        store.blacklist[c.key] = {"title": c.title, "game": c.game, "url": c.url, "reason": reason, "date": today}
        c.blacklisted = store.blacklist[c.key]
    store.save_blacklist()


def unblacklist(store: Store, keys: list[str]) -> None:
    for k in keys:
        store.blacklist.pop(k, None)
    store.save_blacklist()


# ---- CSV exports -------------------------------------------------------------------------------------------------

QUIVER_COLUMNS = ["title", "game", "url", "provider", "slug", "developer", "type", "quiver_page"]
CANDIDATE_COLUMNS = ["title", "game", "url", "sources", "method", "platform", "author", "added", "first_seen",
                     "status", "quiver_apps_for_same_game", "recomplauncher_page", "unbaked_page", "playported_page"]


def write_quiver_csv(path: str, quiver_items: list[Item]) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(QUIVER_COLUMNS)
        for q in sorted(quiver_items, key=lambda q: q.title.lower()):
            w.writerow([q.title, q.game, q.url, q.extra.get("provider", ""), q.slug, q.author, q.method, q.page])
    return len(quiver_items)


def status_of(c: Candidate) -> str:
    if c.in_quiver:
        return "in quiver"
    return "blacklisted" if c.blacklisted else "missing"


def write_candidates_csv(path: str, candidates: list[Candidate]) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CANDIDATE_COLUMNS)
        for c in candidates:
            w.writerow([
                c.title, c.game, c.url, ", ".join(EXTERNAL[s] for s in c.sources), c.method, c.platform, c.author,
                c.added, c.first_seen, status_of(c), "; ".join(q.title for q in c.same_game),
                c.pages.get("recomplauncher", ""), c.pages.get("unbaked", ""), c.pages.get("playported", ""),
            ])
    return len(candidates)
