"""Everything the tool keeps between runs, as JSON files in one folder.

settings.json   options
blacklist.json  projects maintainers decided not to add: key -> {title, url, reason, date}
seen.json       when each project was first seen on a listing site: key -> refresh time
snapshot.json   the last refresh's items, so the window opens with data straight away
cache/*.json    pages already read from PlayPorted and unbaked, to read only what changed
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

DEFAULT_SETTINGS = {
    "sources": {"recomplauncher": True, "unbaked": True, "playported": True},
    # Read every unbaked game record (thousands of requests the first time) instead of only recent additions.
    "unbaked_full_scan": False,
    # A CSV of Quiver's catalog to use instead of its API. Empty: use the API.
    "quiver_csv": "",
    # Where the blacklist lives; empty means the data folder. Point it at a shared folder to share it.
    "blacklist_path": "",
}


def default_dir() -> Path:
    if os.environ.get("QUIVER_SCOUT_HOME"):
        return Path(os.environ["QUIVER_SCOUT_HOME"])
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "quiver-scout"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "quiver-scout"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "quiver-scout"


def _read(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class Store:
    def __init__(self, directory: str | Path | None = None):
        self.dir = Path(directory) if directory else default_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        saved = _read(self.dir / "settings.json", {})
        self.settings = {**DEFAULT_SETTINGS, **saved, "sources": {**DEFAULT_SETTINGS["sources"], **saved.get("sources", {})}}
        self.blacklist: dict[str, dict] = _read(self.blacklist_file, {})
        self.seen: dict[str, str] = _read(self.dir / "seen.json", {})
        self.snapshot: dict = _read(self.dir / "snapshot.json", {})

    @property
    def blacklist_file(self) -> Path:
        custom = self.settings.get("blacklist_path")
        return Path(custom) if custom else self.dir / "blacklist.json"

    def save_settings(self) -> None:
        _write(self.dir / "settings.json", self.settings)
        self.blacklist = _read(self.blacklist_file, self.blacklist)

    def save_blacklist(self) -> None:
        _write(self.blacklist_file, self.blacklist)

    def save_seen(self) -> None:
        _write(self.dir / "seen.json", self.seen)

    def save_snapshot(self) -> None:
        _write(self.dir / "snapshot.json", self.snapshot)

    def cache(self, name: str) -> dict:
        return _read(self.dir / "cache" / f"{name}.json", {})

    def save_cache(self, name: str, data: dict) -> None:
        _write(self.dir / "cache" / f"{name}.json", data)
