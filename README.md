# Quiver Scout

A maintainer tool for the [Quiver Launcher](https://quiverlauncher.com) catalog. It reads every app Quiver lists,
reads the recomp and port listings on other sites, matches them by repository, and shows what Quiver is missing.
Then you can open each one, wait for the ones that aren't ready, and blacklist those that don't belong.

![Quiver Scout](docs/screenshot.png)

## What it reads

| Source | How | After the first refresh |
| --- | --- | --- |
| **Quiver Launcher** | `api.quiverlauncher.com/api/v1`, the API the launcher uses: `/release-status` for each app's repository and newest release, `/apps` for names and games. Or a CSV export (see Settings). | `/release-status` (~20 requests); `/apps` only for apps it hasn't seen |
| **RecompLauncher** | `recomplauncher.com/pc-catalog.json`, its whole PC catalog, with each project's latest release. | Downloaded only when it changed |
| **Decomp Buddy** | `tanookistudios.com/decomp-buddy/catalog.json`, the catalog it publishes for anyone to use, with each project's latest release, last push and published builds. | Downloaded only when it changed |
| **unbaked** | `unbaked.games/api/additions.json`, every project added in the last several weeks. Optionally every game's full record (Settings). | Downloaded only when it changed; full scan: only games with new pushes |
| **PlayPorted** | `playported.com/sitemap.xml`, then each port's page for its project link (~2,100 pages the first time). | Only new or changed port pages |

A refresh where nothing changed takes a few seconds and downloads almost nothing. **Full refresh** (Shift+F5) ignores
what's cached and reads everything again.

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

| Tab | What's in it |
| --- | --- |
| **Missing** | Projects other sites list that Quiver doesn't, on a code host Quiver reads releases from (GitHub, GitLab, Codeberg, Bitbucket, and any self-hosted Gitea/Forgejo site Quiver already has an app from). Bold rows are new in the last refresh. |
| **Other sites** | The same, but on a site Quiver can't read yet: SourceForge, itch.io, a project's own website, a web port. Kept apart for when Quiver supports custom sites. |
| **Waiting** | Projects you're waiting on, and why: Quiver must support its site first, no source code yet, no release file yet, or something else, with a note. A green row is in Quiver now, so you can take it off the list. |
| **Blacklist** | Projects you decided not to add, with a note. Grey rows are no longer listed anywhere. |
| **In Quiver** | Quiver's catalog, each app's latest release, and which other sites also list it. |
| **Log** | What each refresh did, and any site that failed. |

Columns include **Latest release**, **Version**, **Last push**, **Listed since** and **First seen**. Click a heading
to sort (dates sort newest first, with unknown dates last either way); right-click a heading to show or hide
columns. Sorting and columns are remembered. *Game in Quiver* says Quiver already has another app for the same game,
often a sign it's a fork; the details pane links to it.

Filters: search (every word must match the project, game, URL, author, platform, method or note), which sites a
project is listed on, *New* (in the last refresh), *Has a release*, *Hide games in Quiver*, and *2+ sites*.

The details pane shows what each site says about the selected project: its page there, the release it knows of,
the last push, when it was listed, stars, and for Decomp Buddy which systems it publishes builds for (a project with
no builds is likely one to wait for).

**View → Theme** switches between light, dark, and the system's setting. On Windows the title bar follows too.

| Key | Action |
| --- | --- |
| F5 / Shift+F5 | Refresh what changed / full refresh |
| Enter / double-click | Open the repository |
| Ctrl+Enter | Open its pages on the listing sites |
| Ctrl+C | Copy the selected rows' URLs |
| W | Wait for it (on Waiting: change why) |
| Delete | Blacklist (on Waiting and Blacklist: back to the list) |
| Backspace | Back to the list, from Waiting or Blacklist |
| Ctrl+A | Select every row |
| Ctrl+1 … Ctrl+5 | Switch tabs |
| Ctrl+F / Esc | Search / clear the search |
| Ctrl+E | Export the current list as CSV |

Right-click a row for the same actions, including *Wait for…* with each reason in one click. After waiting or
blacklisting, the selection moves to the next row, so you can work down a list from the keyboard.

When a site fails during a refresh, its items from the previous refresh are kept, so a site being down doesn't make
everything look new or missing. The error is shown in the Log tab.

The first refresh marks everything it finds as already known. After that, each project first found in a refresh
gets that refresh's time in *First seen*, and *New* shows those from the newest one.

## Sharing between maintainers

What you decide (Waiting and Blacklist) lives in its own file, `decisions.json`, apart from everything that can be
downloaded again. To share it, point **Settings → Decisions file** at the same file for everyone:

- **A git repository** (best): commit `decisions.json` to a small repo the maintainers share. It's sorted with one
  project per entry, so changes diff and merge cleanly.
- **A synced folder** (Dropbox, OneDrive, Google Drive): works as long as two people don't change it in the same
  minute.

Every change reads the file again first and writes back only that change, so one maintainer's edits don't undo
another's. The window also reloads it when you come back to it. Set **Your name** in Settings to have it on each
decision.

The database (`scout.db`) is a local cache: everyone keeps their own and a refresh rebuilds it. Don't put it in a
synced folder.

## Command line

```bash
python -m quiver_scout quiver-csv quiver-catalog.csv
```

writes Quiver's catalog: title, game, url, provider, slug, developer, type, added, latest release, version and Quiver
page.

```bash
python -m quiver_scout missing missing.csv --refresh
```

refreshes and writes the projects Quiver is missing. `--list other`, `--list waiting`, `--list blacklist` or
`--list all` writes another list instead, and `--new-only` keeps only those new in the last refresh.

```bash
python -m quiver_scout refresh
```

only refreshes, for running on a schedule. `--full` reads everything again.

## Settings and data

Settings are in File → Settings: which sites to read, the unbaked full scan, a Quiver CSV to use instead of the API,
the decisions file, and your name.

**Quiver CSV**: when Quiver's admins export the catalog, pick the file here. It needs a URL column (`url`,
`repository`, `repo`, `source` or similar), and uses `title`, `game` and `slug` columns when it has them.

Everything lives in one folder (File → Open data folder): `%APPDATA%\quiver-scout` on Windows,
`~/Library/Application Support/quiver-scout` on macOS, `~/.local/share/quiver-scout` on Linux. `--data-dir` or
the `QUIVER_SCOUT_HOME` environment variable puts it somewhere else.

| File | What |
| --- | --- |
| `settings.json` | Options, sort orders and hidden columns |
| `decisions.json` | Waiting and Blacklist (shareable, see above) |
| `scout.db` | SQLite: each source's items (table `items`), pages already read (`pages`), the ETag or Last-Modified of each catalog file (`http_cache`), and when each project was first seen (`seen`) |

Version 0.1's `snapshot.json`, `seen.json`, cache and `blacklist.json` are moved in on first start; nothing is read
again.

## Adding a site

Each site is a module in `quiver_scout/sources/` with a `fetch(ctx)` that returns `Item`s (a project URL, its name
and game, its page on that site, and any dates it knows). `ctx.json(url)` downloads a file only when it changed. Add
it to `EXTERNAL` in `sources/__init__.py` and to `_fetch_one` in `engine.py`.

## Tests

```bash
python -m unittest discover -s tests
```

## License

[MIT](LICENSE)
