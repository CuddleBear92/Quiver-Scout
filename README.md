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
| **OSGameClones** | `osgameclones.com`, every project on one page: remakes, official source releases, and clones. | Downloaded only when it changed |
| **Cloned Games** | `clonedgames.com/sitemap.xml`, then each project's page for its repository and original game (~4,200 pages, ~120 MB the first time). Its `/api/` is off limits by its robots.txt, so it isn't used. | Only new or changed pages |
| **OSGL** (Trilarion) | `trilarion.github.io/opensourcegames`, the Open Source Games List: 27 letter pages with each game's repository and what inspired it. Remakes and clones only; original games are left out. | Each page downloaded only when it changed |

A refresh where nothing changed takes about ten seconds and downloads almost nothing: catalog files come back as
"not modified", and page caches only fetch what the sitemaps say is new. **Full refresh** (Shift+F5) ignores
what's cached and reads everything again.

**Clones** are new games in the style of an old one, not ports of it, so Quiver can't offer them for the original.
OSGameClones, Cloned Games and OSGL list many; they're left out unless **Settings → Include clones** is on. Cloned
Games calls everything that isn't a decompilation or recompilation a clone, source ports too, so those of its
projects count as clones here; most are also on OSGameClones or OSGL as remakes.

**Forgejo and Gitea** run on anyone's server, so a project on a site the tool doesn't know is checked the way Quiver
Launcher checks one: the site is asked for `/api/v1/version`, which Forgejo and Gitea answer. Only addresses shaped
like a repository (`site/owner/name`) are checked, each site once; sites that aren't forges are asked again after 30
days, and sites that didn't answer after a day. A project on a Forgejo/Gitea site goes to Missing, not Other sites.

Platforms and methods are written one way whichever site they come from: `ps2`, `PS2` and `PlayStation 2` are all
*PlayStation 2*, `gba` is *Game Boy Advance*, `x360` is *Xbox 360* (see `quiver_scout/names.py`).

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
| **Missing** | Projects other sites list that Quiver doesn't, on a code host Quiver reads releases from: GitHub, GitLab, Codeberg, Bitbucket, and self-hosted Forgejo/Gitea servers (see below). Bold rows are new in the last refresh. |
| **Other sites** | The same, but on a site Quiver can't read yet: SourceForge, itch.io, a project's own website, a web port. Kept apart for when Quiver supports custom sites. |
| **Waiting** | Projects you're waiting on, and why: Quiver must support its site first, no source code yet, no release file yet, or something else, with a note. A green row is in Quiver now, so you can take it off the list. |
| **Blacklist** | Projects you decided not to add, with a note. Grey rows are no longer listed anywhere. |
| **In Quiver** | Quiver's catalog, each app's latest release, and which other sites also list it. |
| **Log** | What each refresh did, and any site that failed. |

Columns include **Latest release**, **Version**, **Last push**, **Listed since**, **First seen** and **Runs on**.

- **Sort:** click a heading. A second click reverses, a third goes back to the list's own order (by game). Dates sort
  newest first, with unknown dates last either way.
- **Reorder:** drag a heading left or right, or right-click it for *Move left* / *Move right*.
- **Hide:** right-click a heading for *Hide*, or tick columns under *Columns*. *Reset columns* puts back the order,
  columns and widths.

Each tab's column order, hidden columns, widths and sorting are saved in `settings.json`. *Game in Quiver* says Quiver already has another app for the same game,
often a sign it's a fork; the details pane links to it.

Filters: search (every word must match the project, game, URL, author, platform, method or note), *Listed on*
(a drop-down of the sites), *Runs on* (a drop-down of the systems projects run on), *New* (in the last refresh),
*Has a release*, *Hide games in Quiver* and *2+ sites*.

*Runs on* lists every system any site says a project runs on or has builds for (Decomp Buddy's builds, unbaked's and
OSGL's platforms, RecompLauncher's systems), most common first with a count, and *Not known* for projects no site
says anything about. A project that says Android in its name or link counts as Android. To see one system, pick
*No systems*, then tick it.

The details pane, under the list when one project is selected and only as tall as what it says, shows what each
site says about it: its page there, the release it knows of,
the last push, when it was listed, stars, and for Decomp Buddy which systems it publishes builds for (a project with
no builds is likely one to wait for).

**View → Theme** switches between light, dark, and the system's setting. The menu bar and, on Windows, the title bar
follow too. Alt+F, Alt+V and Alt+H open the menus.

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

Settings are in File → Settings: which sites to read, the unbaked full scan, whether to include clones, a Quiver CSV
to use instead of the API, the decisions file, and your name.

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
