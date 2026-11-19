"""Refreshing every source, and comparing the listing sites with Quiver's catalog."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime

from .decisions import BLACKLIST, WAIT_REASONS, WAITING
from . import forges, names
from .sources import (EXTERNAL, Context, Item, clonedgames, decompbuddy, osgameclones, playported, quiver,
                      recomplauncher, trilarion, unbaked)
from .sources.base import Progress, day, no_progress
from .store import Store
from .urls import aliases, canonical, normalize_title

# Code hosts Quiver Launcher and its catalog read releases from: GitHub, GitLab, and Forgejo/Gitea, whose known
# servers are these (the launcher's own list, providers/gitea.ts) plus any found by asking (forges.py) or that Quiver
# already has an app from. Bitbucket has a provider in the launcher's code but no app in the catalog, so it isn't
# counted. Projects anywhere else go to "Other sites": Quiver would need to support that site first.
GITEA_HOSTS = {"codeberg.org", "gitea.com", "git.ryujinx.app", "git.eden-emu.dev", "try.gitea.io"}
FORGE_HOSTS = {"github.com", "gitlab.com", *GITEA_HOSTS}
FORGE_PROVIDERS = {"github", "gitlab", "gitea"}

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

    # Sites Quiver might read from that it has no app from yet: ask whether they're Forgejo/Gitea servers.
    try:
        all_items = store.items()
        known = supported_hosts([Item.from_dict(i) for i in all_items if i["source"] == "quiver"])
        hosts = {i["key"].split("/")[0] for i in all_items
                 if i["source"] != "quiver" and forges.looks_like_repo(i["key"])} - known
        store.set_meta("forges", forges.check(hosts, store.meta("forges", {}), progress=progress))
    except Exception as e:  # a check that fails only leaves those projects in Other sites
        progress(f"Forgejo/Gitea check failed: {e}", 1, 1)

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

    # Links that are due a check, if that's on. Without a GitHub token, GitHub is checked one page at a time, which
    # for thousands of new links takes long, so then it's only done when asked (File -> Check links).
    if store.settings.get("check_links", True) and store.tokens().get("github"):
        try:
            result["links"] = check_links(store, progress)
        except Exception as e:
            progress(f"Link check failed: {e}", 1, 1)
    else:
        try:
            result["live"] = verify_quiver_live(store, progress)
        except Exception as e:
            progress(f"Live check of Down in Quiver failed: {e}", 1, 1)
    progress("Done" + (f", with {len(errors)} error(s)" if errors else ""), 1, 1)
    return result


def apply_live(live: dict, quiver_items: list[Item]) -> list[Item]:
    """Quiver's apps as the live catalog has them now: archived ones left out, changed repositories changed.
    `live` is what verify_quiver_live found: slug -> {"archived": True} or {"url", "provider"}."""
    out = []
    for q in quiver_items:
        v = live.get(q.slug) or {}
        if v.get("archived"):
            continue
        if v.get("url"):
            c = canonical(v["url"])
            if c and c[0] != q.key:
                q.key, q.url = c
                q.extra = {**q.extra, "provider": v.get("provider") or q.extra.get("provider", ""), "live": True}
        out.append(q)
    return out


def verify_quiver_live(store: Store, progress: Progress = no_progress, slugs: list[str] | None = None) -> dict:
    """Ask Quiver's live (uncached) API about each app in Down in Quiver (or `slugs`): an archived app answers
    404 and is left out from then on; an app whose repository was changed gets the new one. The catalog the tool
    reads otherwise is Cloudflare's cached copy, which can be 20 minutes behind. Returns counts."""
    from datetime import date
    from urllib.parse import quote

    from . import linkcheck, net
    from .sources import quiver as quiver_source
    from .urls import from_provider

    items = [Item.from_dict(d) for d in store.items("quiver")]
    by_slug = {q.slug: q for q in items}
    # Forget apps that have left the catalog (archived ones do, once the cache catches up).
    live = {s: v for s, v in store.meta("quiver_live", {}).items() if s in by_slug}
    if slugs is None:
        states = link_states(store)
        shown = apply_live(live, [Item.from_dict(d) for d in store.items("quiver")])
        slugs = [q.slug for q in shown if (states.get(q.key) or {}).get("status") in linkcheck.TAKEN_DOWN]
        # Archived ones are asked again too, in case they were brought back.
        slugs += [s for s, v in live.items() if v.get("archived") and s not in slugs]
    counts = {"checked": 0, "archived": 0, "changed": 0}
    if not slugs:
        store.set_meta("quiver_live", live)
        return counts
    progress(f"Asking Quiver about {len(slugs)} app(s) in Down in Quiver", 0, len(slugs))
    today = date.today().isoformat()
    for n, slug in enumerate(slugs, start=1):
        try:
            r = net.request(f"{quiver_source.LIVE_API}/apps/{quote(slug, safe='')}", retries=2)
            project = (json.loads(r.body) or {}).get("project") or {}
        except net.HTTPStatusError as e:
            if e.status == 404:
                live[slug] = {"archived": True, "checked": today}
                counts["archived"] += 1
                counts["checked"] += 1
            continue
        except Exception:
            continue
        counts["checked"] += 1
        url = from_provider(project.get("provider", ""), project.get("repository", ""), project.get("host"))
        c = canonical(url) if url else None
        q = by_slug.get(slug)
        if c and q and c[0] != q.key:
            live[slug] = {"url": c[1], "provider": project.get("provider", ""), "checked": today}
            counts["changed"] += 1
        else:
            live.pop(slug, None)  # as the catalog says: nothing to change
        progress(f"Quiver: {n}/{len(slugs)}", n, len(slugs))
    store.set_meta("quiver_live", live)
    progress(f"Quiver: {counts['archived']} archived, {counts['changed']} with a changed repository, of "
             f"{counts['checked']} asked", 1, 1)
    return counts


def link_states(store: Store) -> dict[str, dict]:
    """Each link's last check: this computer's, or the shared file's when that is newer (another maintainer's)."""
    out = store.links()
    for key, shared in store.taken_down.entries.items():
        mine = out.get(key)
        if not mine or shared.get("checked", "") > mine.get("checked", ""):
            out[key] = shared
    return out


def host_kinds(store: Store, quiver_items: list[Item]) -> dict[str, str]:
    """host -> "gitlab" or "gitea", for checking links through their APIs."""
    kinds = {h: "gitea" for h in GITEA_HOSTS}
    kinds["gitlab.com"] = "gitlab"
    for q in quiver_items:
        if q.extra.get("provider") in ("gitea", "gitlab") and q.key:
            kinds[q.key.split("/")[0]] = q.extra["provider"]
    for h, v in store.meta("forges", {}).items():
        if v.get("kind"):
            kinds[h] = "gitea"
    return kinds


def check_links(store: Store, progress: Progress = no_progress, keys: list[str] | None = None,
                everything: bool = False) -> dict:
    """Check the links of every project (listed on a site, waited for, blacklisted) and every Quiver app that are due,
    or just `keys`, or all of them. Saves what's found, and puts taken-down and moved ones in the shared file.
    Returns counts."""
    from datetime import date

    from . import linkcheck

    items = [Item.from_dict(d) for d in store.items()]
    urls: dict[str, str] = {}
    for i in items:
        urls.setdefault(i.key, i.url)
    for k, d in store.decisions.entries.items():
        urls.setdefault(k, d.get("url", ""))
    states = link_states(store)
    today = date.today()
    if keys is not None:
        todo = [k for k in keys if urls.get(k)]
    else:
        todo = [k for k in urls if urls[k] and (everything or linkcheck.due(states.get(k), today))]
    if not todo:
        progress("Link check: nothing due", 1, 1)
        return {"checked": 0}
    progress(f"Link check: {len(todo)} link(s)", 0, len(todo))
    found = linkcheck.check([(k, urls[k]) for k in todo], host_kinds(store, [i for i in items if i.source == "quiver"]),
                            store.tokens(), progress)
    mine = store.links()
    entries = {}
    for k, r in found.items():
        e = linkcheck.merge(mine.get(k), r, today.isoformat())
        # A link still down keeps the replacements found for it.
        if e["status"] in linkcheck.TAKEN_DOWN and (states.get(k) or {}).get("replacements_checked"):
            e.update({x: states[k][x] for x in ("replacements", "replacements_checked") if x in states[k]})
        entries[k] = e
    store.save_links(entries)
    shared = store.taken_down.update(entries, urls, by=store.settings.get("your_name", ""))
    counts: dict[str, int] = {}
    for e in entries.values():
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    down = sum(n for s, n in counts.items() if s in linkcheck.TAKEN_DOWN)
    progress(f"Link check: {len(entries)} of {len(todo)} checked, {down} taken down, {counts.get('moved', 0)} moved"
             + (f"; {shared} change(s) to the shared list" if shared else ""), 1, 1)

    # Look for taken-down Quiver apps elsewhere every day, as developers may move their code; for other taken-down
    # projects, only among what's already listed (no requests). Apps archived or changed in Quiver since the cached
    # catalog was read are taken out first, so they aren't looked for.
    try:
        counts["live"] = verify_quiver_live(store, progress)
    except Exception as e:
        progress(f"Live check of Down in Quiver failed: {e}", 1, 1)
    states = link_states(store)
    quiver_keys = {q.key for q in apply_live(store.meta("quiver_live", {}), [i for i in items if i.source == "quiver"])}
    down = [k for k, e in states.items() if e.get("status") in linkcheck.TAKEN_DOWN]
    ask = [k for k in down if k in quiver_keys and (states[k].get("replacements_checked", "") < today.isoformat())]
    data_only = [k for k in entries if k in down and k not in quiver_keys]
    if ask or data_only:
        found_any = find_replacements(store, ask, progress) + find_replacements(store, data_only, progress, ask=False)
        if found_any:
            counts["replacements"] = found_any
    return {"checked": len(entries), **counts}


def find_replacements(store: Store, keys: list[str], progress: Progress = no_progress, ask: bool = True) -> int:
    """Look for each taken-down link's repository elsewhere (replacements.py) and keep what's found with its link
    check, here and in the shared list. Returns how many links got at least one possible replacement."""
    from datetime import date

    from . import linkcheck, replacements

    if not keys:
        return 0
    states = link_states(store)
    known: dict[str, str] = {}
    for i in store.items():
        where = "in Quiver" if i["source"] == "quiver" else EXTERNAL.get(i["source"], i["source"])
        known[i["key"]] = where if i["key"] not in known else (
            known[i["key"]] if where in known[i["key"]] else f"{known[i['key']]}, {where}")
    down = {k for k, e in states.items() if e.get("status") in linkcheck.TAKEN_DOWN}
    tokens = store.tokens()
    today = date.today().isoformat()
    entries, hits = {}, 0
    if ask:
        progress(f"Looking for {len(keys)} taken-down repositories elsewhere", 0, len(keys))
    for n, k in enumerate(keys, start=1):
        found = replacements.find(k, known, down, tokens, ask=ask)
        entries[k] = {**(states.get(k) or {}), "replacements": found, "replacements_checked": today}
        hits += bool(found)
        if ask:
            progress(f"Replacements: {n}/{len(keys)}", n, len(keys))
    store.save_links(entries)
    urls = {k: known_url for k, known_url in ((i["key"], i["url"]) for i in store.items()) if k in entries}
    store.taken_down.update(entries, urls, by=store.settings.get("your_name", ""))
    if ask:
        progress(f"Replacements: found possible ones for {hits} of {len(keys)}", 1, 1)
    return hits


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
    runs_on: str = ""  # systems it has builds for or runs on (PC, Linux, Mac, Android...), from any site
    first_seen: str = ""
    sources: list[str] = field(default_factory=list)
    pages: dict[str, str] = field(default_factory=dict)
    listings: dict[str, Item] = field(default_factory=dict)  # each site's own item
    in_quiver: list[Item] = field(default_factory=list)  # Quiver apps with this repository
    same_game: list[Item] = field(default_factory=list)  # Quiver apps for the same game, from another repository
    decision: dict | None = None
    supported_host: bool = True
    forge: str = ""  # "forgejo" or "gitea" when its site was found to be a self-hosted one
    link: dict | None = None  # the last link check (linkcheck.py): status, checked, moved_to...

    @property
    def link_status(self) -> str:
        return (self.link or {}).get("status", "")

    @property
    def taken_down(self) -> bool:
        from .linkcheck import TAKEN_DOWN
        return self.link_status in TAKEN_DOWN

    @property
    def host(self) -> str:
        return self.key.split("/")[0]

    @property
    def listed(self) -> bool:
        return bool(self.sources)

    @property
    def android(self) -> bool:
        """Runs on Android, by any site's say, or says Android in its name or link."""
        return "Android" in self.runs_on.split(", ") or "android" in f"{self.title} {self.url} {self.game}".lower()

    @property
    def systems(self) -> list[str]:
        """What it runs on, for the Runs on filter: the sites' say, and Android when its name or link says so."""
        out = [s for s in self.runs_on.split(", ") if s]
        if self.android and "Android" not in out:
            out.append("Android")
        return out


@dataclass
class Comparison:
    quiver: list[Item]
    candidates: list[Candidate]
    refresh: dict

    links: dict = field(default_factory=dict)  # key -> last link check, for Quiver's apps too

    def _open(self) -> list[Candidate]:
        return [c for c in self.candidates if c.listed and not c.in_quiver and not c.decision and not c.taken_down]

    @property
    def taken_down(self) -> list[Candidate]:
        """Not in Quiver, not decided on, and its link is taken down."""
        return [c for c in self.candidates if c.listed and not c.in_quiver and not c.decision and c.taken_down]

    @property
    def quiver_down(self) -> list[Item]:
        """Quiver's own (live) apps whose repository is taken down."""
        from .linkcheck import TAKEN_DOWN
        return [q for q in self.quiver if (self.links.get(q.key) or {}).get("status") in TAKEN_DOWN]

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


def supported_hosts(quiver_items: list[Item], forge_sites: dict | None = None) -> set[str]:
    """Sites Quiver reads releases from: GitHub, GitLab, the known Forgejo/Gitea servers, any self-hosted site Quiver
    already has an app from, and any site found to be a Forgejo or Gitea server."""
    hosts = set(FORGE_HOSTS)
    for q in quiver_items:
        if q.extra.get("provider", "github") in FORGE_PROVIDERS and q.key:
            hosts.add(q.key.split("/")[0])
    hosts |= {h for h, v in (forge_sites or {}).items() if v.get("kind")}
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
    quiver_items = apply_live(store.meta("quiver_live", {}), [i for i in items if i.source == "quiver"])
    forge_sites = store.meta("forges", {})
    hosts = supported_hosts(quiver_items, forge_sites)

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
        runs = names.platform(", ".join(x for x in (i.extra.get("runs_on"), i.extra.get("builds")) if x))
        if runs:
            c.runs_on = names.platform(", ".join(x for x in (c.runs_on, runs) if x))

    # Decided-on projects no site lists any more still show in Waiting and Blacklist.
    for key, d in store.decisions.entries.items():
        if key not in merged:
            merged[key] = Candidate(key=key, url=d.get("url", ""), title=d.get("title", ""), game=d.get("game", ""),
                                    first_seen=seen.get(key, ""))

    links = link_states(store)
    for c in merged.values():
        c.link = links.get(c.key)
        # A renamed repository may be in Quiver under its new name.
        names_ = set(aliases(c.key))
        moved = canonical((c.link or {}).get("moved_to", "")) if c.link_status == "moved" else None
        if moved:
            names_ |= aliases(moved[0])
        c.in_quiver = list({id(q): q for k in names_ for q in by_key.get(k, [])}.values())
        if not c.in_quiver:
            c.same_game = by_game.get(normalize_title(c.game), [])
        c.decision = store.decisions.get(c.key)
        c.supported_host = c.host in hosts
        c.forge = (forge_sites.get(c.host) or {}).get("kind", "")

    return Comparison(
        quiver=quiver_items,
        candidates=sorted(merged.values(), key=lambda c: (c.game or c.title).lower()),
        refresh=store.meta("refresh", {}),
        links=links,
    )


def import_file(store: Store, path: str) -> tuple[str, dict[str, int]]:
    """Merge a decisions file or a taken-down list someone sent into this computer's. Tells which it was by what's in
    it. Returns ("decisions" | "taken-down", counts of added, updated and kept)."""
    import json as _json

    with open(path, encoding="utf-8") as f:
        data = _json.load(f)
    if isinstance(data, dict) and isinstance(data.get("decisions"), dict):
        return "decisions", store.decisions.merge(data["decisions"])
    if isinstance(data, dict) and isinstance(data.get("links"), dict):
        return "taken-down", store.taken_down.merge(data["links"])
    if isinstance(data, dict) and data and all(isinstance(v, dict) and "url" in v for v in data.values()):
        # Version 0.1's blacklist.json: key -> {title, game, url, reason, date}.
        from .decisions import BLACKLIST as _B
        old = {k: {**{x: y for x, y in v.items() if x != "reason"}, "list": _B, "note": v.get("reason", "")}
               for k, v in data.items()}
        return "decisions", store.decisions.merge(old)
    raise ValueError(f"{path} isn't a decisions file or a taken-down list")


def export_file(store: Store, which: str, path: str) -> int:
    """Save a copy of the decisions file or the taken-down list, to send to someone. Returns how many entries."""
    import shutil

    if which == "decisions":
        store.decisions.reload()
        source, count = store.decisions.path, len(store.decisions.entries)
    else:
        store.taken_down.reload()
        source, count = store.taken_down.path, len(store.taken_down.entries)
    if not source.exists():
        (store.decisions if which == "decisions" else store.taken_down)._write()
    shutil.copyfile(source, path)
    return count


def decide(store: Store, which: str, candidates: list[Candidate], reason: str = "", note: str = "") -> None:
    store.decisions.set(which, [{"key": c.key, "title": c.title, "game": c.game, "url": c.url} for c in candidates],
                        reason=reason, note=note, by=store.settings.get("your_name", ""))


def undecide(store: Store, keys: list[str]) -> None:
    store.decisions.remove(keys)


# ---- CSV exports -------------------------------------------------------------------------------------------------

QUIVER_COLUMNS = ["title", "game", "url", "provider", "slug", "developer", "type", "added", "latest_release",
                  "version", "quiver_page", "link", "link_checked", "moved_to"]
CANDIDATE_COLUMNS = ["title", "game", "url", "host", "sources", "method", "platform", "author", "listed_since",
                     "latest_release", "version", "last_push", "first_seen", "status", "waiting_for", "note",
                     "quiver_apps_for_same_game", "link", "link_checked", "moved_to", *[f"{s}_page" for s in EXTERNAL]]


def write_quiver_csv(path: str, quiver_items: list[Item], links: dict | None = None) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(QUIVER_COLUMNS)
        for q in sorted(quiver_items, key=lambda q: q.title.lower()):
            link = (links or {}).get(q.key) or {}
            w.writerow([q.title, q.game, q.url, q.extra.get("provider", ""), q.slug, q.author, q.method, q.added,
                        q.released, q.version, q.page, link.get("status", ""), link.get("checked", ""),
                        link.get("moved_to", "")])
    return len(quiver_items)


def status_of(c: Candidate) -> str:
    if c.in_quiver:
        return "in quiver"
    if c.decision:
        return c.decision.get("list", "")
    if c.taken_down:
        return "taken down"
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
                d.get("note", ""), "; ".join(q.title for q in c.same_game), c.link_status,
                (c.link or {}).get("checked", ""), (c.link or {}).get("moved_to", ""), *[c.pages.get(s, "") for s in EXTERNAL],
            ])
    return len(candidates)


__all__ = ["BLACKLIST", "WAITING", "Candidate", "Comparison", "check_links", "compare", "decide", "refresh",
           "undecide"]
