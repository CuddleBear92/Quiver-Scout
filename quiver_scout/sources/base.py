from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .. import net
from ..urls import canonical

# progress(message, done, total); total 0 means unknown.
Progress = Callable[[str, int, int], None]


def no_progress(message: str, done: int = 0, total: int = 0) -> None:
    pass


def day(value: Any) -> str:
    """YYYY-MM-DD from an ISO date or time, or from milliseconds since 1970. "" when there's none."""
    if value in (None, "", 0):
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return str(value)[:10]


@dataclass
class Item:
    """One project as one site lists it."""

    source: str  # "quiver", "recomplauncher", "decompbuddy", "unbaked", "playported"
    url: str  # the project's repository or home page
    title: str  # the project's name: "Zelda 64: Recompiled"
    game: str = ""  # the original game: "The Legend of Zelda: Majora's Mask"
    page: str = ""  # the project's page on the listing site
    method: str = ""  # "Recomp", "Decomp", "Static recomp", "port", ...
    platform: str = ""  # original console: "n64", "Wii"
    author: str = ""
    added: str = ""  # when the site listed it, YYYY-MM-DD
    released: str = ""  # its newest release, YYYY-MM-DD, when the site knows
    version: str = ""  # that release's version
    pushed: str = ""  # its repository's last push, YYYY-MM-DD, when the site knows
    slug: str = ""  # the site's own id for it
    extra: dict = field(default_factory=dict)
    key: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            c = canonical(self.url)
            if c:
                self.key, self.url = c

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class Context:
    """What a source needs while it reads: progress reports, and downloads that skip files that haven't changed."""

    def __init__(self, store=None, progress: Progress = no_progress, full: bool = False):
        self.store = store
        self.progress = progress
        self.full = full  # ignore every cache and read everything again

    def fetch(self, url: str, timeout: float = 60) -> tuple[bytes, bool]:
        """(body, changed). With a store, sends the last ETag / Last-Modified, and reuses the saved body on a 304."""
        cached = None if (self.full or not self.store) else self.store.http_entry(url)
        headers = {}
        if cached:
            if cached[0]:
                headers["If-None-Match"] = cached[0]
            if cached[1]:
                headers["If-Modified-Since"] = cached[1]
        r = net.request(url, headers=headers, timeout=timeout)
        if r.status == 304 and cached:
            return cached[2], False
        if self.store and (r.headers.get("etag") or r.headers.get("last-modified")):
            self.store.save_http(url, r.headers.get("etag", ""), r.headers.get("last-modified", ""), r.body,
                                 datetime.now().strftime("%Y-%m-%d %H:%M"))
        return r.body, True

    def json(self, url: str, timeout: float = 60) -> tuple[Any, bool]:
        body, changed = self.fetch(url, timeout)
        return json.loads(body), changed

    def text(self, url: str, timeout: float = 60) -> tuple[str, bool]:
        body, changed = self.fetch(url, timeout)
        return body.decode("utf-8", errors="replace"), changed
