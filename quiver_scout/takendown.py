"""The shared list of projects whose links are taken down or moved, so maintainers don't each check thousands of links.

One JSON file, sorted, one link per entry: key -> {status, url, checked, detail?, moved_to?, by?}. Like the decisions
file it can sit in a shared folder or a git repository: every change reads the file again first and writes back only
what changed, so one maintainer's results don't undo another's. Links found fine again are taken off it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .linkcheck import SHARED


class TakenDown:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.entries: dict[str, dict] = {}
        self._mtime = None
        self.reload()

    def reload(self) -> bool:
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except OSError:
            return False
        if mtime == self._mtime:
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = data.get("links", {}) if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return False
        self._mtime = mtime
        return True

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def update(self, results: dict[str, dict], urls: dict[str, str], by: str = "") -> int:
        """Put taken-down and moved links on the list and take ones found fine off it. Returns how many changed."""
        self._mtime = None
        self.reload()
        changed = 0
        for key, r in results.items():
            if r.get("status") in SHARED:
                entry = {k: v for k, v in r.items()
                         if k in ("status", "checked", "detail", "moved_to", "replacements", "replacements_checked")}
                entry["url"] = urls.get(key, "")
                if by:
                    entry["by"] = by
                old = self.entries.get(key)
                if not old or {k: v for k, v in old.items() if k not in ("checked", "by")} != \
                        {k: v for k, v in entry.items() if k not in ("checked", "by")} or old.get("checked") != entry["checked"]:
                    self.entries[key] = entry
                    changed += 1
            elif r.get("status") in ("ok", "archived") and key in self.entries:
                del self.entries[key]
                changed += 1
        if changed or not self.path.exists():
            self._write()
        return changed

    def merge(self, incoming: dict[str, dict]) -> dict[str, int]:
        """Take in another maintainer's list: new links are added, and a link both have keeps whichever check is
        newer. Returns counts: added, updated, kept."""
        self._mtime = None
        self.reload()
        counts = {"added": 0, "updated": 0, "kept": 0}
        for key, theirs in incoming.items():
            if not isinstance(theirs, dict) or theirs.get("status") not in SHARED:
                continue
            mine = self.entries.get(key)
            if not mine:
                self.entries[key] = theirs
                counts["added"] += 1
            elif theirs.get("checked", "") > mine.get("checked", ""):
                self.entries[key] = theirs
                counts["updated"] += 1
            else:
                counts["kept"] += 1
        if counts["added"] or counts["updated"]:
            self._write()
        return counts

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "links": self.entries}, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, self.path)
        self._mtime = os.stat(self.path).st_mtime_ns
