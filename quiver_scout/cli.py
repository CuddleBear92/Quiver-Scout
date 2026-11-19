"""Command line: `quiver-scout` with no arguments opens the window; the subcommands work without one."""

from __future__ import annotations

import argparse
import sys

from . import __version__, engine
from .sources import quiver
from .store import Store


def _progress(message: str, done: int = 0, total: int = 0) -> None:
    print(f"  {message}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="quiver-scout", description="Find recomps and ports that Quiver Launcher doesn't list yet.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--data-dir", help="where settings, cache and blacklist live (default: your user data folder)")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("gui", help="open the window (the default)")

    q = sub.add_parser("quiver-csv", help="write Quiver's catalog (title, game, repository URL, ...) to a CSV file")
    q.add_argument("output", nargs="?", default="quiver-catalog.csv")

    sub.add_parser("refresh", help="read every source and save a snapshot")

    m = sub.add_parser("missing", help="write projects other sites list and Quiver doesn't to a CSV file")
    m.add_argument("output", nargs="?", default="missing.csv")
    m.add_argument("--refresh", action="store_true", help="refresh first instead of using the last snapshot")
    m.add_argument("--new-only", action="store_true", help="only projects first seen in the last refresh")
    m.add_argument("--all", action="store_true", help="every project, with a status column, not only missing ones")

    a = p.parse_args(argv)
    if a.command in (None, "gui"):
        from .gui import run

        return run(a.data_dir)

    store = Store(a.data_dir)
    if a.command == "quiver-csv":
        items = quiver.from_csv(store.settings["quiver_csv"]) if store.settings.get("quiver_csv") else quiver.fetch(_progress)
        n = engine.write_quiver_csv(a.output, items)
        print(f"Wrote {n} Quiver apps to {a.output}")
        return 0

    if a.command == "refresh" or getattr(a, "refresh", False) or not store.snapshot:
        snap = engine.refresh(store, _progress)
        for e in snap["errors"]:
            print(f"error: {e}", file=sys.stderr)
        if a.command == "refresh":
            return 1 if snap["errors"] else 0

    result = engine.compare(store)
    rows = result.candidates if a.all else result.missing
    if a.new_only:
        rows = [c for c in rows if result.is_new(c)]
    n = engine.write_candidates_csv(a.output, rows)
    print(f"Wrote {n} projects to {a.output} ({len(result.missing)} missing from Quiver, {len(result.quiver)} in Quiver)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
