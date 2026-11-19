# Quiver Scout

A maintainer tool for the [Quiver Launcher](https://quiverlauncher.com) catalog. It reads every app Quiver lists,
reads the recomp and port listings on other sites, matches them by repository, and shows what Quiver is missing.
Then you can open each one, and blacklist those that don't belong.

![Quiver Scout](docs/screenshot.png)

## What it reads

| Source | How | Requests |
| --- | --- | --- |
| **Quiver Launcher** | `api.quiverlauncher.com/api/v1`, the API the launcher uses: `/release-status` for each app's repository, `/apps` for names and games. Or a CSV export (see Settings). | ~40 |
| **RecompLauncher** | `recomplauncher.com/pc-catalog.json`, its whole PC catalog. | 1 |
| **unbaked** | `unbaked.games/api/additions.json`, every project added in the last several weeks. Optionally every game's full record (Settings). | 1 (full: ~6,000 the first time, then only changed games) |
| **PlayPorted** | `playported.com/sitemap.xml`, then each port's page for its project link. Pages are cached and re-read only when the sitemap says they changed. | ~2,100 the first time, then only new or changed ports |

Projects are matched by repository, whatever form the link takes: `https://GitHub.com/Owner/Repo.git`,
`github.com/owner/repo/releases/tag/v1` and `http://www.github.com/owner/repo/` are the same project. GitLab
subgroups, Codeberg and other Gitea/Forgejo hosts, and project sites that aren't on a code host are handled too.

## Run it

It needs Python 3.9 or later and nothing else. On Windows, the python.org installer includes Tkinter, which the
window uses. On Linux you may need your distribution's `python3-tk` package.

```bash
git clone https://github.com/CuddleBear92/Quiver-Scout
```

```bash
cd Quiver-Scout
```

```bash
python -m quiver_scout
```

On Windows you can also double-click `Quiver Scout.pyw`. Or install it, which adds `quiver-scout` and
`quiver-scout-gui` commands:

```bash
pip install .
```

## The window

- **Missing**: projects other sites list that Quiver doesn't, merged across sites. Bold rows are new in the last
  refresh. *Game in Quiver* says Quiver already has another app for the same game, which is often a sign it's a
  fork or an older project, and the details pane links to it.
- **In Quiver**: Quiver's catalog, and which other sites also list each app.
- **Blacklist**: projects you decided not to add, with the reason. Grey rows are no longer listed anywhere.
- **Log**: what the last refresh did, and any site that failed.

Filters: search (every word must match the project, game, URL, author, platform or method), which sites a project
is listed on, *New in last refresh only*, *Hide games Quiver already has*, and *On 2+ sites*. Click a column
heading to sort.

| Key | Action |
| --- | --- |
| F5 | Refresh every source |
| Enter / double-click | Open the repository |
| Ctrl+Enter | Open its pages on the listing sites |
| Ctrl+C | Copy the selected rows' URLs |
| Delete | Blacklist (on the Blacklist tab: remove from it) |
| Ctrl+A | Select every row |
| Ctrl+F / Esc | Search / clear the search |
| Ctrl+E | Export the current list as CSV |

Blacklisting moves the selection to the next row, so you can work down the list with Enter and Delete.

When a site fails during a refresh, its items from the previous refresh are kept, so a site being down doesn't make
everything look new or missing. The error is shown in the Log tab.

### New since last refresh

The first refresh marks everything it finds as already known. After that, each project first found in a refresh
gets that refresh's time in *First seen*, and *New in last refresh only* shows those from the newest one.

## Command line

```bash
python -m quiver_scout quiver-csv quiver-catalog.csv
```

writes Quiver's catalog: title, game, url, provider, slug, developer, type and Quiver page.

```bash
python -m quiver_scout missing missing.csv --refresh
```

refreshes every source and writes the projects Quiver is missing. `--new-only` keeps only those new in that
refresh, and `--all` writes every project with a `status` column (`missing`, `in quiver` or `blacklisted`).

```bash
python -m quiver_scout refresh
```

only refreshes, for running on a schedule.

## Settings and data

Settings are in File → Settings: which sites to read, the unbaked full scan, a Quiver CSV to use instead of the API,
and where the blacklist lives.

**Quiver CSV**: when Quiver's admins export the catalog, pick the file here. It needs a URL column (`url`,
`repository`, `repo`, `source` or similar), and uses `title`, `game` and `slug` columns when it has them.

**Shared blacklist**: point the blacklist file at a synced folder or a git checkout to share it between maintainers.

Everything lives in one folder (File → Open data folder): `%APPDATA%\quiver-scout` on Windows,
`~/Library/Application Support/quiver-scout` on macOS, `~/.local/share/quiver-scout` on Linux. `--data-dir` or
the `QUIVER_SCOUT_HOME` environment variable puts it somewhere else.

## Adding a site

Each site is a module in `quiver_scout/sources/` with a `fetch(progress)` that returns `Item`s (a project URL,
its name and game, and its page on that site). Add it to `EXTERNAL` in `sources/__init__.py` and to `_fetch_one`
in `engine.py`.

## Tests

```bash
python -m unittest discover -s tests
```
