"""Command line: `quiver-scout` with no arguments opens the window; the subcommands work without one."""

from __future__ import annotations

import argparse
import sys

from . import __version__, engine
from .sources import Context, quiver
from .store import Store

LISTS = ("missing", "other", "taken", "waiting", "blacklist", "all")


def _progress(message: str, done: int = 0, total: int = 0) -> None:
    print(f"  {message}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="quiver-scout", description="Find recomps and ports that Quiver Launcher doesn't list yet.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--data-dir", help="where settings, the database and decisions live (default: your user data folder)")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("gui", help="open the window (the default)")

    q = sub.add_parser("quiver-csv", help="write Quiver's catalog (title, game, repository URL, ...) to a CSV file")
    q.add_argument("output", nargs="?", default="quiver-catalog.csv")

    r = sub.add_parser("refresh", help="read every source and save what changed")
    r.add_argument("--full", action="store_true", help="ignore what's cached and read everything again")

    lc = sub.add_parser("check-links", help="check which projects' links are taken down, moved or archived")
    lc.add_argument("--all", action="store_true", help="every link again, not only new ones and ones due a check")

    m = sub.add_parser("missing", help="write a list of projects to a CSV file (default: those Quiver is missing)")
    m.add_argument("output", nargs="?", default="missing.csv")
    m.add_argument("--list", choices=LISTS, default="missing",
                   help="missing (on code hosts Quiver reads), other (other sites), taken (links taken down), "
                        "waiting, blacklist, or all")
    m.add_argument("--refresh", action="store_true", help="refresh first instead of using the last refresh")
    m.add_argument("--new-only", action="store_true", help="only projects first seen in the last refresh")
    m.add_argument("--all", action="store_true", help="same as --list all")

    a = p.parse_args(argv)
    if a.command in (None, "gui"):
        from .gui import run

        return run(a.data_dir)

    store = Store(a.data_dir)
    if a.command == "quiver-csv":
        if store.settings.get("quiver_csv"):
            items = quiver.from_csv(store.settings["quiver_csv"])
        else:
            items = quiver.fetch(Context(store, _progress), cache=store.pages("quiver"))
        n = engine.write_quiver_csv(a.output, items)
        print(f"Wrote {n} Quiver apps to {a.output}")
        return 0

    if a.command == "check-links":
        counts = engine.check_links(store, _progress, everything=a.all)
        print(", ".join(f"{k} {v}" for k, v in counts.items()))
        return 0

    if a.command == "refresh" or getattr(a, "refresh", False) or not store.meta("refresh"):
        result = engine.refresh(store, _progress, full=getattr(a, "full", False))
        for e in result["errors"]:
            print(f"error: {e}", file=sys.stderr)
        if a.command == "refresh":
            return 1 if result["errors"] else 0

    c = engine.compare(store)
    which = "all" if a.all else a.list
    rows = {
        "missing": c.missing,
        "other": c.other_sites,
        "taken": c.taken_down,
        "waiting": c.decided(engine.WAITING),
        "blacklist": c.decided(engine.BLACKLIST),
        "all": [x for x in c.candidates if x.listed],
    }[which]
    if a.new_only:
        rows = [x for x in rows if c.is_new(x)]
    n = engine.write_candidates_csv(a.output, rows)
    print(f"Wrote {n} projects to {a.output} ({len(c.missing)} missing from Quiver, {len(c.other_sites)} on other "
          f"sites, {len(c.quiver)} in Quiver)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
