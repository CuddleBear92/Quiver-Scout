"""What maintainers decided about projects: wait for them, or never add them.

Kept in one JSON file, sorted, one project per entry, so it can sit in a shared folder or a git repository and be
shared between maintainers. Every change reads the file again first and writes it back with only that change made,
so one maintainer's edits don't undo another's.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

WAITING = "waiting"
BLACKLIST = "blacklist"

# Why a project is waiting, in the order the window offers them.
WAIT_REASONS = {
    "host": "Quiver must support its site first",
    "source": "No source code yet",
    "release": "No release file yet",
    "other": "Other",
}


class Decisions:
    def __init__(self, path: str | Path, legacy: list[Path] | None = None):
        self.path = Path(path)
        self.entries: dict[str, dict] = {}
        self._mtime = None
        if not self.path.exists():
            self._import_legacy(legacy or [])
        self.reload()

    def _import_legacy(self, paths: list[Path]) -> None:
        """0.1 kept a blacklist.json of key -> {title, game, url, reason, date}."""
        for p in paths:
            try:
                old = json.loads(Path(p).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(old, dict) and "decisions" in old:
                self.entries = old["decisions"]
            elif isinstance(old, dict) and old:
                self.entries = {k: {**v, "list": BLACKLIST, "note": v.get("reason", "")} for k, v in old.items()}
                for v in self.entries.values():
                    v.pop("reason", None)
            if self.entries:
                self._write()
                return

    def reload(self) -> bool:
        """Read the file again if it changed on disk (another maintainer, a sync). True when it did."""
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except OSError:
            return False
        if mtime == self._mtime:
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = data.get("decisions", {}) if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return False
        self._mtime = mtime
        return True

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "decisions": self.entries}, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, self.path)
        self._mtime = os.stat(self.path).st_mtime_ns

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def of(self, which: str) -> dict[str, dict]:
        return {k: v for k, v in self.entries.items() if v.get("list") == which}

    def set(self, which: str, projects: list[dict], reason: str = "", note: str = "", by: str = "") -> None:
        """projects: dicts with key, title, game, url."""
        self._mtime = None
        self.reload()
        today = datetime.now().strftime("%Y-%m-%d")
        for p in projects:
            entry = {"list": which, "title": p.get("title", ""), "game": p.get("game", ""), "url": p.get("url", ""),
                     "note": note, "date": today}
            if which == WAITING:
                entry["reason"] = reason or "other"
            if by:
                entry["by"] = by
            self.entries[p["key"]] = entry
        self._write()

    def remove(self, keys: list[str]) -> None:
        self._mtime = None
        self.reload()
        for k in keys:
            self.entries.pop(k, None)
        self._write()
