import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quiver_scout import engine, net
from quiver_scout.decisions import BLACKLIST, WAITING, Decisions
from quiver_scout.sources import Context, Item, decompbuddy, playported, quiver, recomplauncher, unbaked
from quiver_scout.sources.base import day
from quiver_scout.store import Store
from quiver_scout.urls import aliases, canonical, from_provider, normalize_title

FIX = Path(__file__).parent / "fixtures"


class Urls(unittest.TestCase):
    def test_github_forms_share_a_key(self):
        forms = [
            "https://github.com/Owner/Repo",
            "http://www.github.com/owner/repo/",
            "github.com/owner/repo.git",
            "https://github.com/owner/repo/releases/tag/v1.0",
            "https://github.com/owner/repo/tree/main/src",
            "https://raw.githubusercontent.com/owner/repo/HEAD/README.md",
        ]
        self.assertEqual({canonical(f)[0] for f in forms}, {"github.com/owner/repo"})

    def test_gitlab_keeps_subgroups_and_drops_inner_pages(self):
        self.assertEqual(canonical("https://gitlab.com/group/sub/proj/-/releases")[0], "gitlab.com/group/sub/proj")
        self.assertEqual(canonical("https://codeberg.org/a/b/releases")[0], "codeberg.org/a/b")
        self.assertEqual(canonical("https://git.example.dev/a/b/src/branch/main")[0], "git.example.dev/a/b")

    def test_other_sites_keep_their_path(self):
        self.assertEqual(canonical("https://t3hd0gg.com/project-r/"), ("t3hd0gg.com/project-r", "https://t3hd0gg.com/project-r"))
        self.assertIsNone(canonical(""))

    def test_github_pages_alias(self):
        self.assertIn("github.com/owner/repo", aliases("owner.github.io/repo"))

    def test_from_provider(self):
        self.assertEqual(from_provider("github", "a/b"), "https://github.com/a/b")
        self.assertEqual(from_provider("gitlab", "a/b"), "https://gitlab.com/a/b")
        self.assertEqual(from_provider("gitea", "a/b", "codeberg.org"), "https://codeberg.org/a/b")
        self.assertEqual(from_provider("manual", "https://sourceforge.net/projects/x/"), "https://sourceforge.net/projects/x/")

    def test_normalize_title(self):
        self.assertEqual(normalize_title("The Legend of Zelda: Majora's Mask (USA)"), normalize_title("the legend of zelda majoras mask"))

    def test_day(self):
        self.assertEqual(day("2026-08-25T17:54:43Z"), "2026-08-25")
        self.assertEqual(day(1792434254000), "2026-10-19")
        self.assertEqual(day(None), "")


class Sources(unittest.TestCase):
    def test_recomplauncher(self):
        items = recomplauncher.parse(json.loads((FIX / "recomplauncher_pc.json").read_text()))
        by = {i.slug: i for i in items}
        z = by["zelda64recomp"]
        self.assertEqual(z.key, "github.com/zelda64recomp/zelda64recomp")
        self.assertEqual(z.page, "https://recomplauncher.com/game/zelda64recomp")
        self.assertEqual((z.released, z.version, z.added), ("2025-08-27", "v1.2.2", "2026-10-26"))
        self.assertTrue(any(i.url.startswith("https://gitlab.com/") for i in items))
        self.assertEqual(len(items), 3)  # the one without a repository is left out

    def test_decompbuddy(self):
        items = decompbuddy.parse(json.loads((FIX / "decompbuddy_catalog.json").read_text()))
        by = {i.title: i for i in items}
        ship = by["2 Ship 2 Harkinian"]
        self.assertEqual(ship.key, "github.com/harbourmasters/2ship2harkinian")
        self.assertEqual((ship.released, ship.version, ship.added), ("2026-08-25", "5.0.1", "2026-09-13"))
        self.assertEqual(ship.page, "https://decompbuddy.com/games/the-legend-of-zelda-majora-s-mask-nintendo-64")
        self.assertEqual(ship.extra["builds"], "Windows, Linux, macOS")
        self.assertEqual(by["bermuda"].released, "")  # no release, so its date isn't a release date
        self.assertEqual(by["Longshot"].key, "git.magicbean.dev/magicbeandev/longshot")

    def test_unbaked_additions(self):
        items = unbaked.parse_additions(json.loads((FIX / "unbaked_additions.json").read_text()))
        self.assertEqual(len(items), 3)
        tribes = next(i for i in items if "tribes" in i.key)
        self.assertEqual((tribes.game, tribes.method), ("Tribes 2", "Static recomp"))
        self.assertTrue(tribes.page.startswith("https://unbaked.games/games/"))

    def test_unbaked_skips_agent_environments(self):
        record = {"slug": "x", "title": "X", "projects": [
            {"name": "env", "url": "https://github.com/a/env", "methods": ["Agent environment"]},
            {"name": "port", "url": "https://github.com/a/port", "methods": ["Source port", "Agent environment"],
             "last_push": "2026-11-01"},
        ]}
        items = unbaked.parse_game(record)
        self.assertEqual([(i.title, i.pushed) for i in items], [("port", "2026-11-01")])

    def test_playported_page(self):
        url = "https://playported.com/ports/2177-robox/"
        port = playported.parse_port(url, (FIX / "playported_port.html").read_text())
        self.assertEqual(port["url"], "https://github.com/Eggscantfly/robox-recompiled")
        self.assertEqual((port["title"], port["game"], port["platform"]), ("robox-recompiled", "Robox", "Wii"))
        self.assertEqual((port["method"], port["author"], port["slug"]), ("Recomp", "Eggscantfly", "2177"))

    def test_playported_non_git_link(self):
        port = playported.parse_port("https://playported.com/ports/1-x/", (FIX / "playported_port_site.html").read_text())
        self.assertEqual(port["url"], "https://t3hd0gg.com/project-r/")
        self.assertEqual(port["method"], "Decomp")

    def test_playported_sitemap_only_ports(self):
        pages = playported.parse_sitemap((FIX / "playported_sitemap.xml").read_text())
        self.assertEqual(len(pages), 3)
        self.assertTrue(all("/ports/" in u and v for u, v in pages.items()))

    def test_playported_reads_only_changed_pages(self):
        sitemap = (FIX / "playported_sitemap.xml").read_text()
        pages = playported.parse_sitemap(sitemap)
        first, *rest = pages
        cache = {u: {"version": pages[u], "data": {"url": "https://github.com/a/" + str(n), "title": "t", "game": "",
                                                   "platform": "", "method": "", "author": "", "added": "", "slug": ""}}
                 for n, u in enumerate(rest)}
        ctx = Context()
        ctx.text = lambda url, timeout=60: (sitemap, True)
        with mock.patch.object(net, "get_text", return_value=(FIX / "playported_port.html").read_text()) as get:
            items = playported.fetch(ctx, cache=cache)
        self.assertEqual([c.args[0] for c in get.call_args_list], [first])
        self.assertEqual(len(items), 3)

    def test_quiver_csv_column_detection(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
            csv.writer(f).writerows([["Name", "Repository URL", "Slug"], ["Zelda", "https://github.com/a/b", "zelda"], ["Empty", "", "x"]])
        items = quiver.from_csv(f.name)
        self.assertEqual([(i.title, i.key, i.slug) for i in items], [("Zelda", "github.com/a/b", "zelda")])

    def test_quiver_asks_only_for_new_apps(self):
        status = {"items": [
            {"slug": "old", "provider": "github", "repository": "a/old", "latestUpstream": {"releasedAt": 1792434254000, "version": "v2"}},
            {"slug": "new", "provider": "gitea", "host": "codeberg.org", "repository": "b/new"},
        ], "isDone": True}
        apps = {"items": [{"slug": "new", "name": "New", "projectName": "New Recomp", "games": [{"title": "Game"}],
                           "addedAt": 1795104620539}], "isDone": True}
        calls = []

        def fake(url, **kw):
            calls.append(url)
            return status if "/release-status" in url else apps

        cache = {"old": {"version": "", "data": {"projectName": "Old Recomp", "games": ["Old Game"]}}}
        with mock.patch.object(net, "get_json", side_effect=fake):
            items = quiver.fetch(Context(), cache=cache)
        self.assertEqual(len(calls), 2)
        self.assertIn("sort=added", calls[1])
        by = {i.slug: i for i in items}
        self.assertEqual((by["old"].title, by["old"].released, by["old"].version), ("Old Recomp", "2026-10-19", "v2"))
        self.assertEqual((by["new"].title, by["new"].url, by["new"].added), ("New Recomp", "https://codeberg.org/b/new", "2026-11-19"))
        calls.clear()
        with mock.patch.object(net, "get_json", side_effect=fake):
            quiver.fetch(Context(), cache=cache)
        self.assertEqual(len(calls), 1)  # every app known: only /release-status


class ConditionalDownloads(unittest.TestCase):
    def test_304_reuses_the_saved_body(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d)
            ctx = Context(store)
            with mock.patch.object(net, "request", return_value=net.Response(200, b'{"a": 1}', {"etag": '"x"'})):
                self.assertEqual(ctx.json("https://e/c.json"), ({"a": 1}, True))
            with mock.patch.object(net, "request", return_value=net.Response(304)) as req:
                self.assertEqual(ctx.json("https://e/c.json"), ({"a": 1}, False))
            self.assertEqual(req.call_args.kwargs["headers"], {"If-None-Match": '"x"'})
            with mock.patch.object(net, "request", return_value=net.Response(200, b'{"a": 2}', {})) as req:
                self.assertEqual(Context(store, full=True).json("https://e/c.json"), ({"a": 2}, True))
            self.assertEqual(req.call_args.kwargs["headers"], {})
            store.db.close()


class DecisionsFile(unittest.TestCase):
    def test_two_maintainers_dont_undo_each_other(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "shared.json"
            alice, bob = Decisions(path), Decisions(path)
            alice.set(WAITING, [{"key": "github.com/a/a", "title": "A"}], reason="release", by="alice")
            bob.set(BLACKLIST, [{"key": "github.com/b/b", "title": "B"}], note="fork")
            self.assertEqual(set(Decisions(path).entries), {"github.com/a/a", "github.com/b/b"})
            bob.remove(["github.com/a/a"])
            self.assertTrue(alice.reload())
            self.assertEqual(list(alice.entries), ["github.com/b/b"])

    def test_old_blacklist_is_imported(self):
        with tempfile.TemporaryDirectory() as d:
            old = Path(d) / "blacklist.json"
            old.write_text(json.dumps({"github.com/x/y": {"title": "Y", "url": "u", "reason": "dup", "date": "2026-11-19"}}))
            dec = Decisions(Path(d) / "decisions.json", legacy=[old])
            self.assertEqual(dec.get("github.com/x/y")["list"], BLACKLIST)
            self.assertEqual(dec.get("github.com/x/y")["note"], "dup")


class Compare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def put(self, items, refreshed="2026-11-19 10:00"):
        by: dict = {}
        for i in items:
            by.setdefault(i.source, []).append(i.to_dict())
        for source, rows in by.items():
            self.store.replace_items(source, rows)
        self.store.set_meta("refresh", {"refreshed_at": refreshed})

    def test_merges_sources_and_matches_quiver(self):
        self.put([
            Item("quiver", "https://github.com/a/zelda", "Zelda Recomp", game="Majora's Mask", slug="zelda"),
            Item("recomplauncher", "https://github.com/A/Zelda", "Zelda 64", page="https://rl/zelda"),
            Item("playported", "https://github.com/x/new-thing/releases", "New Thing", game="Majora's Mask", page="https://pp/1"),
            Item("unbaked", "github.com/x/new-thing", "new-thing", page="https://ub/1", added="2026-11-01", pushed="2026-11-10"),
            Item("decompbuddy", "github.com/x/new-thing", "nt", added="2026-10-01", released="2026-11-05", version="v1"),
        ])
        r = engine.compare(self.store)
        self.assertEqual([c.key for c in r.missing], ["github.com/x/new-thing"])
        new = r.missing[0]
        self.assertEqual(new.sources, ["decompbuddy", "unbaked", "playported"])  # display order
        self.assertEqual((new.added, new.released, new.version, new.pushed), ("2026-10-01", "2026-11-05", "v1", "2026-11-10"))
        self.assertEqual([q.slug for q in new.same_game], ["zelda"])
        listed = next(c for c in r.candidates if c.in_quiver)
        self.assertEqual(listed.in_quiver[0].slug, "zelda")

    def test_other_sites_are_kept_apart(self):
        self.put([
            Item("quiver", "https://git.eden-emu.dev/eden/eden", "Eden", extra={"provider": "gitea"}),
            Item("quiver", "https://sourceforge.net/projects/hua3/", "HUA3", extra={"provider": "manual"}),
            Item("playported", "https://foxhollow.com/game", "Fox"),
            Item("playported", "https://sourceforge.net/projects/other/", "SF"),
            Item("playported", "https://git.eden-emu.dev/someone/else", "Eden fork"),
            Item("playported", "https://codeberg.org/a/b", "CB"),
        ])
        r = engine.compare(self.store)
        self.assertEqual(sorted(c.title for c in r.missing), ["CB", "Eden fork"])
        self.assertEqual(sorted(c.title for c in r.other_sites), ["Fox", "SF"])

    def test_waiting_and_blacklist_leave_the_lists(self):
        self.put([Item("playported", "https://github.com/x/y", "Y"), Item("playported", "https://foxhollow.com/g", "G")])
        r = engine.compare(self.store)
        engine.decide(self.store, WAITING, r.missing, reason="release", note="no zip yet")
        engine.decide(self.store, BLACKLIST, r.other_sites, note="not a port")
        r = engine.compare(self.store)
        self.assertEqual((r.missing, r.other_sites), ([], []))
        self.assertEqual(r.decided(WAITING)[0].decision["reason"], "release")
        self.assertEqual(r.decided(BLACKLIST)[0].decision["note"], "not a port")
        engine.undecide(self.store, ["github.com/x/y"])
        self.assertEqual(len(engine.compare(self.store).missing), 1)

    def test_decided_projects_stay_visible_when_no_longer_listed(self):
        self.store.decisions.set(WAITING, [{"key": "github.com/gone/away", "title": "Gone", "url": "https://github.com/gone/away"}])
        r = engine.compare(self.store)
        gone = r.decided(WAITING)[0]
        self.assertEqual((gone.title, gone.listed), ("Gone", False))
        self.assertEqual(r.missing, [])

    def test_new_since_last_refresh(self):
        self.store.add_seen({"github.com/x/old": "initial", "github.com/x/new": "2026-11-19 10:00"})
        self.put([Item("playported", "https://github.com/x/old", "Old"), Item("playported", "https://github.com/x/new", "New")])
        r = engine.compare(self.store)
        self.assertEqual([c.title for c in r.missing if r.is_new(c)], ["New"])

    def test_refresh_keeps_last_items_of_a_failing_source(self):
        self.put([Item("playported", "https://github.com/x/y", "Y")])
        self.store.settings["sources"] = {"recomplauncher": False, "decompbuddy": False, "unbaked": False, "playported": True}

        def fake(store, name, ctx):
            if name == "quiver":
                return [Item("quiver", "https://github.com/q/q", "Q")]
            raise RuntimeError("site down")

        with mock.patch.object(engine, "_fetch_one", side_effect=fake):
            result = engine.refresh(self.store)
        self.assertEqual(result["errors"], ["playported: site down"])
        self.assertEqual({i["source"] for i in self.store.items()}, {"quiver", "playported"})
        self.assertEqual(self.store.seen(), {"github.com/x/y": "initial"})

    def test_csv_exports(self):
        self.put([Item("quiver", "https://github.com/a/b", "AB", game="G", slug="ab", released="2026-01-02"),
                  Item("unbaked", "https://github.com/c/d", "CD")])
        r = engine.compare(self.store)
        out = Path(self.tmp.name) / "q.csv"
        engine.write_quiver_csv(str(out), r.quiver)
        row = next(csv.DictReader(out.open()))
        self.assertEqual((row["url"], row["latest_release"]), ("https://github.com/a/b", "2026-01-02"))
        engine.write_candidates_csv(str(out), r.missing)
        row = next(csv.DictReader(out.open()))
        self.assertEqual((row["title"], row["status"], row["sources"], row["host"]), ("CD", "missing", "unbaked", "github.com"))


class Migration(unittest.TestCase):
    def test_json_files_of_0_1_move_into_the_database(self):
        with tempfile.TemporaryDirectory() as d:
            item = Item("playported", "https://github.com/x/y", "Y").to_dict()
            (Path(d) / "snapshot.json").write_text(json.dumps({"refreshed_at": "2026-11-19 16:39", "items": [item]}))
            (Path(d) / "seen.json").write_text(json.dumps({"github.com/x/y": "initial"}))
            (Path(d) / "cache").mkdir()
            (Path(d) / "cache" / "playported.json").write_text(json.dumps({"https://pp/1": {"lastmod": "m", "port": {"url": "u"}}}))
            store = Store(d)
            self.assertEqual([i["key"] for i in store.items("playported")], ["github.com/x/y"])
            self.assertEqual(store.seen(), {"github.com/x/y": "initial"})
            self.assertEqual(store.pages("playported"), {"https://pp/1": {"version": "m", "data": {"url": "u"}}})
            self.assertEqual(store.meta("refresh")["refreshed_at"], "2026-11-19 16:39")
            store.db.close()


if __name__ == "__main__":
    unittest.main()
