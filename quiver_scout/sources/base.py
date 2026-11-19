from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable

from ..urls import canonical

# progress(message, done, total); total 0 means unknown.
Progress = Callable[[str, int, int], None]


def no_progress(message: str, done: int = 0, total: int = 0) -> None:
    pass


@dataclass
class Item:
    """One project as one site lists it."""

    source: str  # "quiver", "recomplauncher", "unbaked", "playported"
    url: str  # the project's repository or home page
    title: str  # the project's name: "Zelda 64: Recompiled"
    game: str = ""  # the original game: "The Legend of Zelda: Majora's Mask"
    page: str = ""  # the project's page on the listing site
    method: str = ""  # "Recomp", "Decomp", "Static recomp", "port", ...
    platform: str = ""  # original console: "n64", "Wii"
    author: str = ""
    added: str = ""  # when the site added it, YYYY-MM-DD when known
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
