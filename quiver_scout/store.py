"""What the tool keeps between runs, in one folder.

settings.json   options
scout.db        SQLite: every source's items, pages already read, HTTP validators, when projects were first seen.
                Local to this computer; anyone can rebuild it with a refresh.
decisions.json  Waiting and Blacklist (see decisions.py). Plain JSON, so it can live in a shared folder or a git repo.
taken-down.json Links found taken down or moved (see takendown.py). Shareable the same way.
tokens.json     GitHub and GitLab tokens. Kept out of settings.json so that can be shared; never share this one.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import zlib
from pathlib import Path

from .decisions import Decisions
from .takendown import TakenDown

DEFAULT_SETTINGS = {
    "sources": {"recomplauncher": True, "decompbuddy": True, "unbaked": True, "playported": True,
                "osgameclones": True, "clonedgames": True, "trilarion": True},
    # Clones are new games in the style of an old one (OSGameClones, Cloned Games and OSGL list many); Quiver
    # can't offer them for the original, so they're left out unless this is on.
    "include_clones": False,
    # Read every unbaked game record (thousands of requests the first time) instead of only recent additions.
    "unbaked_full_scan": False,
    # A CSV of Quiver's catalog to use instead of its API. Empty: use the API.
    "quiver_csv": "",
    # Where Waiting and Blacklist live; empty means decisions.json in the data folder.
    "decisions_path": "",
    # Where the shared list of taken-down links lives; empty means taken-down.json in the data folder.
    "taken_down_path": "",
    # Check links that are due (new, or not checked for a while) after each refresh.
    "check_links": True,
    # Put on each decision, so maintainers sharing the file can see who made it.
    "your_name": "",
    "theme": "system",  # "system", "light" or "dark"
    "hidden_columns": {},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    source TEXT NOT NULL, key TEXT NOT NULL, url TEXT, title TEXT, game TEXT, page TEXT, method TEXT,
    platform TEXT, author TEXT, added TEXT, released TEXT, version TEXT, pushed TEXT, slug TEXT, extra TEXT
);
CREATE INDEX IF NOT EXISTS items_source ON items(source);
CREATE INDEX IF NOT EXISTS items_key ON items(key);
-- Pages and records already read from a site, with the version they were read at (a sitemap lastmod, a last push).
CREATE TABLE IF NOT EXISTS pages (
    source TEXT NOT NULL, url TEXT NOT NULL, version TEXT, data TEXT, PRIMARY KEY (source, url)
);
-- ETag / Last-Modified of whole files (catalogs, sitemaps), and the body, so an unchanged file isn't downloaded again.
CREATE TABLE IF NOT EXISTS http_cache (
    url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, body BLOB, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, first_seen TEXT);
-- What the last check of each link found (see linkcheck.py).
CREATE TABLE IF NOT EXISTS links (key TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

ITEM_FIELDS = ["source", "key", "url", "title", "game", "page", "method", "platform", "author", "added", "released",
               "version", "pushed", "slug", "extra"]


def default_dir() -> Path:
    if os.environ.get("QUIVER_SCOUT_HOME"):
        return Path(os.environ["QUIVER_SCOUT_HOME"])
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "quiver-scout"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "quiver-scout"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "quiver-scout"


def read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: Path, data, sort_keys: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=sort_keys)
        f.write("\n")
    os.replace(tmp, path)


class Store:
    def __init__(self, directory: str | Path | None = None):
        self.dir = Path(directory) if directory else default_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        saved = read_json(self.dir / "settings.json", {})
        self.settings = {**DEFAULT_SETTINGS, **saved,
                         "sources": {**DEFAULT_SETTINGS["sources"], **saved.get("sources", {})}}
        self.lock = threading.RLock()
        new = not (self.dir / "scout.db").exists()
        self.db = sqlite3.connect(self.dir / "scout.db", check_same_thread=False)
        self.db.executescript(SCHEMA)
        if new:
            self._migrate_json()
        self.decisions = Decisions(self.decisions_file, legacy=self._legacy_blacklist())
        self.taken_down = TakenDown(self.taken_down_file)

    # ---- settings and decisions ----------------------------------------------------------------------------------

    @property
    def decisions_file(self) -> Path:
        custom = self.settings.get("decisions_path")
        return Path(custom) if custom else self.dir / "decisions.json"

    @property
    def taken_down_file(self) -> Path:
        custom = self.settings.get("taken_down_path")
        return Path(custom) if custom else self.dir / "taken-down.json"

    # ---- tokens --------------------------------------------------------------------------------------------------

    def tokens(self) -> dict[str, str]:
        """github and gitlab tokens: tokens.json, else the GITHUB_TOKEN / GITLAB_TOKEN environment variables."""
        saved = read_json(self.dir / "tokens.json", {})
        return {site: saved.get(site) or os.environ.get(f"{site.upper()}_TOKEN", "") for site in ("github", "gitlab")}

    def save_tokens(self, tokens: dict[str, str]) -> None:
        path = self.dir / "tokens.json"
        write_json(path, {k: v.strip() for k, v in tokens.items() if v and v.strip()})
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _legacy_blacklist(self) -> list[Path]:
        paths = [self.dir / "blacklist.json"]
        old = read_json(self.dir / "settings.json", {}).get("blacklist_path")
        if old:
            paths.insert(0, Path(old))
        return paths

    def save_settings(self) -> None:
        write_json(self.dir / "settings.json", self.settings)
        if Path(self.decisions.path) != self.decisions_file:
            self.decisions = Decisions(self.decisions_file, legacy=[self.decisions.path])
        if Path(self.taken_down.path) != self.taken_down_file:
            self.taken_down = TakenDown(self.taken_down_file)

    # ---- items ---------------------------------------------------------------------------------------------------

    def items(self, source: str | None = None) -> list[dict]:
        with self.lock:
            q = f"SELECT {', '.join(ITEM_FIELDS)} FROM items" + (" WHERE source = ?" if source else "")
            rows = self.db.execute(q, (source,) if source else ()).fetchall()
        out = []
        for row in rows:
            d = dict(zip(ITEM_FIELDS, row))
            d["extra"] = json.loads(d["extra"] or "{}")
            out.append({k: ("" if v is None else v) for k, v in d.items()})
        return out

    def replace_items(self, source: str, items: list[dict]) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM items WHERE source = ?", (source,))
            self.db.executemany(
                f"INSERT INTO items ({', '.join(ITEM_FIELDS)}) VALUES ({', '.join('?' * len(ITEM_FIELDS))})",
                [[json.dumps(i.get("extra") or {}, ensure_ascii=False) if f == "extra" else i.get(f, "") for f in ITEM_FIELDS]
                 for i in items],
            )

    # ---- page cache ----------------------------------------------------------------------------------------------

    def pages(self, source: str) -> dict[str, dict]:
        """url -> {"version", "data"}."""
        with self.lock:
            rows = self.db.execute("SELECT url, version, data FROM pages WHERE source = ?", (source,)).fetchall()
        return {u: {"version": v, "data": json.loads(d) if d else None} for u, v, d in rows}

    def save_pages(self, source: str, pages: dict[str, dict]) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM pages WHERE source = ?", (source,))
            self.db.executemany(
                "INSERT INTO pages (source, url, version, data) VALUES (?, ?, ?, ?)",
                [(source, u, p.get("version"), json.dumps(p.get("data"), ensure_ascii=False)) for u, p in pages.items()],
            )

    # ---- HTTP validators -----------------------------------------------------------------------------------------

    def http_entry(self, url: str) -> tuple[str, str, bytes] | None:
        with self.lock:
            row = self.db.execute("SELECT etag, last_modified, body FROM http_cache WHERE url = ?", (url,)).fetchone()
        return (row[0] or "", row[1] or "", zlib.decompress(row[2])) if row else None

    def save_http(self, url: str, etag: str, last_modified: str, body: bytes, when: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO http_cache VALUES (?, ?, ?, ?, ?)",
                            (url, etag, last_modified, zlib.compress(body, 6), when))

    def clear_http(self) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM http_cache")

    # ---- link checks ---------------------------------------------------------------------------------------------

    def links(self) -> dict[str, dict]:
        with self.lock:
            return {k: json.loads(d) for k, d in self.db.execute("SELECT key, data FROM links").fetchall()}

    def save_links(self, entries: dict[str, dict]) -> None:
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO links VALUES (?, ?)",
                                [(k, json.dumps(v, ensure_ascii=False)) for k, v in entries.items()])

    # ---- first seen and meta -------------------------------------------------------------------------------------

    def seen(self) -> dict[str, str]:
        with self.lock:
            return dict(self.db.execute("SELECT key, first_seen FROM seen").fetchall())

    def add_seen(self, entries: dict[str, str]) -> None:
        with self.lock, self.db:
            self.db.executemany("INSERT OR IGNORE INTO seen VALUES (?, ?)", list(entries.items()))

    def meta(self, k: str, default=None):
        with self.lock:
            row = self.db.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, k: str, v) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, json.dumps(v, ensure_ascii=False)))

    # ---- moving 0.1's JSON files in ------------------------------------------------------------------------------

    def _migrate_json(self) -> None:
        snap = read_json(self.dir / "snapshot.json", {})
        if snap.get("items"):
            by_source: dict[str, list] = {}
            for i in snap["items"]:
                by_source.setdefault(i["source"], []).append(i)
            for source, items in by_source.items():
                self.replace_items(source, items)
            self.set_meta("refresh", {"refreshed_at": snap.get("refreshed_at", ""),
                                      "previous_refresh": snap.get("previous_refresh", ""),
                                      "errors": snap.get("errors", []), "sources": {}})
        seen = read_json(self.dir / "seen.json", {})
        if seen:
            self.add_seen(seen)
        pp = read_json(self.dir / "cache" / "playported.json", {})
        if pp:
            self.save_pages("playported", {u: {"version": c.get("lastmod"), "data": c.get("port")} for u, c in pp.items()})
        ub = read_json(self.dir / "cache" / "unbaked.json", {})
        if ub:
            self.save_pages("unbaked", {s: {"version": c.get("last_push"), "data": c.get("record")} for s, c in ub.items()})
