import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quiver_scout import engine, forges, linkcheck, names, net, replacements
from quiver_scout.takendown import TakenDown
from quiver_scout.decisions import BLACKLIST, WAITING, Decisions
from quiver_scout.sources import (Context, Item, clonedgames, decompbuddy, osgameclones, playported, quiver,
                                  recomplauncher, trilarion, unbaked)
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


class Names(unittest.TestCase):
    def test_platforms_share_one_name(self):
        for forms, name in [
            (["ps2", "PS2", "PlayStation 2"], "PlayStation 2"),
            (["gba", "GBA", "Game Boy Advance"], "Game Boy Advance"),
            (["x360", "Xbox 360"], "Xbox 360"),
            (["og xbox", "xbox", "Xbox"], "Xbox"),
            (["n64", "N64", "Nintendo 64"], "Nintendo 64"),
            (["pc", "PC (Windows)", "windows"], "PC"),
            (["gen", "Genesis", "Mega Drive"], "Genesis / Mega Drive"),
        ]:
            self.assertEqual({names.platform(f) for f in forms}, {name}, forms)

    def test_lists_are_normalized_and_deduplicated(self):
        self.assertEqual(names.platform("ps1, PlayStation, n64"), "PlayStation, Nintendo 64")
        self.assertEqual(names.platform("ngp"), "NGP")  # unknown short names read as abbreviations
        self.assertEqual(names.platform("Acorn 32-bit"), "Acorn 32-bit")

    def test_methods(self):
        self.assertEqual(names.method("port"), "Port")
        self.assertEqual(names.method("Recompilation"), "Recomp")
        self.assertEqual(names.method("Static recomp"), "Static recomp")
        self.assertEqual(names.method("Matching decomp"), "Matching decomp")


class CloneSites(unittest.TestCase):
    def test_osgameclones(self):
        items = osgameclones.parse((FIX / "osgameclones.html").read_text())
        nes = items[0]
        self.assertEqual((nes.title, nes.game, nes.method), ("MetroidNESRecomp", "Metroid", "Remake"))
        self.assertEqual((nes.url, nes.page), ("https://github.com/mstan/MetroidNESRecomp", "https://osgameclones.com/metroid"))
        self.assertEqual(names.platform(nes.platform), "Arcade, Game Boy, NES")
        clone = items[-1]
        self.assertEqual((clone.method, clone.extra), ("Clone", {"clone": True}))

    def test_clonedgames(self):
        dec = clonedgames.parse_page("https://clonedgames.com/redriver2", (FIX / "clonedgames_redriver2.html").read_text())
        self.assertEqual((dec["title"], dec["game"], dec["url"], dec["method"], dec["clone"]),
                         ("REDRIVER2", "Driver 2", "https://github.com/OpenDriver2/REDRIVER2", "Decomp", False))
        port = clonedgames.parse_page("https://clonedgames.com/eduke32", (FIX / "clonedgames_eduke32.html").read_text())
        self.assertEqual((port["game"], port["clone"]), ("Duke Nukem 3D", True))
        self.assertIsNone(clonedgames.parse_page("https://clonedgames.com/x", "<html>no project</html>"))

    def test_clonedgames_sitemap_skips_sections(self):
        xml = "".join(f"<url><loc>https://clonedgames.com/{p}</loc><lastmod>2026</lastmod></url>"
                      for p in ("redriver2", "originals/doom", "decompilations/n64", "blog", "contact"))
        self.assertEqual(list(clonedgames.parse_sitemap(xml)), ["https://clonedgames.com/redriver2"])

    def test_trilarion(self):
        items = trilarion.parse_letter("D", (FIX / "trilarion_D.html").read_text())
        d2x, dfu = items
        self.assertEqual((d2x.game, d2x.url), ("Descent; Descent II", "https://svn.code.sf.net/p/d2x-xl/code"))
        self.assertEqual((dfu.url, dfu.page), ("https://github.com/Interkarma/daggerfall-unity",
                                               "https://trilarion.github.io/opensourcegames/games/D.html#daggerfall_unity"))
        self.assertEqual((dfu.platform, dfu.extra["runs_on"]), ("", "Windows, Linux, macOS"))


class Forges(unittest.TestCase):
    def answer(self, table):
        def fake(url, **kw):
            v = table.get(url)
            if isinstance(v, Exception):
                raise v
            if v is None:
                raise net.HTTPStatusError(f"{url}: HTTP 404", 404)
            return net.Response(200, json.dumps(v).encode() if not isinstance(v, bytes) else v)
        return fake

    def test_probe_tells_forgejo_gitea_and_neither(self):
        table = {
            "https://gitea.example/api/v1/version": {"version": "1.22.3"},
            "https://forgejo.example/api/v1/version": {"version": "9.0.1+gitea-1.22.0"},
            "https://shop.example/api/v1/version": b"<html>not here</html>",
            "https://down.example/api/v1/version": net.FetchError("connection refused"),
        }
        with mock.patch.object(net, "request", side_effect=self.answer(table)):
            self.assertEqual(forges.probe("gitea.example"), "gitea")
            self.assertEqual(forges.probe("forgejo.example"), "forgejo")
            self.assertEqual(forges.probe("shop.example"), "")
            self.assertEqual(forges.probe("nothing.example"), "")  # 404
            self.assertIsNone(forges.probe("down.example"))  # no answer: ask again next time

    def test_check_asks_once_and_rechecks_old_negatives(self):
        from datetime import date
        known = {"old.example": {"kind": "", "checked": "2026-01-01"}, "fresh.example": {"kind": "", "checked": "2026-11-18"},
                 "git.example": {"kind": "gitea", "checked": "2025-01-01"}}
        known["quiet.example"] = {"kind": "", "checked": "2026-11-17", "no_answer": True}
        known["quieter.example"] = {"kind": "", "checked": "2026-11-19", "no_answer": True}
        answers = {"old.example": "forgejo", "new.example": None}
        with mock.patch.object(forges, "probe", side_effect=lambda h: answers.get(h, "")) as p:
            forges.check({"old.example", "fresh.example", "git.example", "new.example", "quiet.example", "quieter.example"},
                         known, today=date(2026, 11, 19))
        self.assertEqual(sorted(c.args[0] for c in p.call_args_list), ["new.example", "old.example", "quiet.example"])
        self.assertEqual(known["old.example"]["kind"], "forgejo")
        self.assertTrue(known["new.example"]["no_answer"])  # asked again tomorrow

    def test_only_repository_addresses_are_asked(self):
        self.assertTrue(forges.looks_like_repo("git.example.dev/owner/repo"))
        self.assertFalse(forges.looks_like_repo("smash.fun"))
        self.assertFalse(forges.looks_like_repo("user.github.io/game"))
        self.assertFalse(forges.looks_like_repo("someone.itch.io/game"))


class LinkCheck(unittest.TestCase):
    def graphql(self, payload):
        class R:
            def __init__(self, body): self.body = body
            def read(self): return self.body
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return mock.patch("urllib.request.urlopen", return_value=R(json.dumps(payload).encode()))

    def test_github_graphql_reads_every_state(self):
        repos = [("github.com/a/ok", "a", "ok"), ("github.com/a/old", "a", "old"), ("github.com/a/gone", "a", "gone"),
                 ("github.com/a/dmca", "a", "dmca"), ("github.com/a/arch", "a", "arch"), ("github.com/a/off", "a", "off"),
                 ("github.com/a/odd", "a", "odd")]
        payload = {"data": {
            "r0": {"nameWithOwner": "a/ok", "url": "https://github.com/a/ok", "isArchived": False, "isDisabled": False, "isLocked": False},
            "r1": {"nameWithOwner": "b/new", "url": "https://github.com/b/new", "isArchived": False, "isDisabled": False, "isLocked": False},
            "r2": None, "r3": None,
            "r4": {"nameWithOwner": "a/arch", "url": "u", "isArchived": True, "isDisabled": False, "isLocked": False},
            "r5": {"nameWithOwner": "a/off", "url": "u", "isArchived": False, "isDisabled": True, "isLocked": False},
            "r6": None},
            "errors": [{"type": "NOT_FOUND", "path": ["r2"], "message": "Could not resolve to a Repository"},
                       {"type": "FORBIDDEN", "path": ["r3"], "message": "Repository access blocked"},
                       {"type": "SOMETHING", "path": ["r6"], "message": "timeout"}]}
        with self.graphql(payload):
            out = linkcheck.github_graphql(repos, "token")
        self.assertEqual({k.split("/")[-1]: v["status"] for k, v in out.items()},
                         {"ok": "ok", "old": "moved", "gone": "gone", "dmca": "blocked", "arch": "archived", "off": "disabled"})
        self.assertEqual(out["github.com/a/old"]["moved_to"], "https://github.com/b/new")

    def test_gitlab_and_gitea(self):
        def fake(url, **kw):
            if "missing" in url:
                raise net.HTTPStatusError("404", 404)
            if "gitlab" in url:
                return net.Response(200, json.dumps({"path_with_namespace": "g/p", "archived": True, "web_url": "w"}).encode())
            return net.Response(200, json.dumps({"full_name": "new/name", "archived": False, "html_url": "https://codeberg.org/new/name"}).encode())
        with mock.patch.object(net, "request", side_effect=fake):
            self.assertEqual(linkcheck.gitlab("gitlab.com/g/p")["status"], "archived")
            self.assertEqual(linkcheck.gitlab("gitlab.com/g/missing")["status"], "gone")
            self.assertEqual(linkcheck.gitea("codeberg.org/old/name"), {"status": "moved", "moved_to": "https://codeberg.org/new/name"})
            self.assertEqual(linkcheck.gitea("codeberg.org/x/missing")["status"], "gone")

    def test_unreachable_needs_two_days(self):
        first = linkcheck.merge(None, {"status": "suspect"}, "2026-11-18")
        self.assertEqual(first["status"], "suspect")
        self.assertEqual(linkcheck.merge(first, {"status": "suspect"}, "2026-11-18")["status"], "suspect")
        self.assertEqual(linkcheck.merge(first, {"status": "suspect"}, "2026-11-19")["status"], "unreachable")
        self.assertEqual(linkcheck.merge(first, {"status": "ok"}, "2026-11-19")["status"], "ok")

    def test_due(self):
        from datetime import date
        today = date(2026, 11, 19)
        self.assertTrue(linkcheck.due(None, today))
        self.assertFalse(linkcheck.due({"status": "ok", "checked": "2026-11-10"}, today))
        self.assertTrue(linkcheck.due({"status": "ok", "checked": "2026-11-01"}, today))
        self.assertFalse(linkcheck.due({"status": "gone", "checked": "2026-10-01"}, today))

    def test_check_without_token_uses_pages_and_stops_when_told(self):
        calls = []

        def web(key, url):
            calls.append(key)
            raise linkcheck.RateLimited("slow down")
        with mock.patch.object(linkcheck, "github_web", side_effect=web), \
                mock.patch.object(linkcheck, "page", return_value={"status": "gone"}):
            out = linkcheck.check([("github.com/a/b", "u"), ("github.com/a/c", "u"), ("example.com/x", "u")], {}, {})
        self.assertEqual(out, {"example.com/x": {"status": "gone"}})


class TakenDownFile(unittest.TestCase):
    def test_shared_list_merges_and_drops_fixed_links(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "taken-down.json"
            a, b = TakenDown(path), TakenDown(path)
            a.update({"github.com/x/1": {"status": "gone", "checked": "2026-11-19"}}, {"github.com/x/1": "https://github.com/x/1"}, by="alice")
            b.update({"github.com/x/2": {"status": "blocked", "checked": "2026-11-19"},
                      "github.com/x/3": {"status": "ok", "checked": "2026-11-19"}}, {})
            self.assertEqual(set(TakenDown(path).entries), {"github.com/x/1", "github.com/x/2"})
            a.update({"github.com/x/1": {"status": "ok", "checked": "2026-11-20"}}, {})
            self.assertEqual(set(TakenDown(path).entries), {"github.com/x/2"})


class Replacements(unittest.TestCase):
    def test_finds_by_owner_and_name_listing_and_search(self):
        known = {"codeberg.org/dev/game-recomp": "unbaked", "github.com/other/game-recomp": "Decomp Buddy",
                 "gitlab.com/dev/another": "OSGL", "github.com/dev/game-recomp": "in Quiver",
                 "github.com/gone/game-recomp": "PlayPorted"}
        exists = {("gitlab.com", "dev", "game-recomp"): "https://gitlab.com/dev/game-recomp"}
        searched = {"codeberg.org": ["https://codeberg.org/mirror/Game-Recomp"]}
        with mock.patch.object(replacements, "_exists", side_effect=lambda h, o, n, t: exists.get((h, o, n))), \
                mock.patch.object(replacements, "_search", side_effect=lambda h, n, t: searched.get(h, [])):
            found = replacements.find("github.com/dev/game-recomp", known, {"github.com/gone/game-recomp"}, {})
        by = {r["url"]: (r["how"], r["where"]) for r in found}
        self.assertEqual(found[0]["how"], "same owner and name")
        self.assertEqual(by["https://codeberg.org/dev/game-recomp"], ("same owner and name", "unbaked"))
        self.assertEqual(by["https://gitlab.com/dev/game-recomp"][0], "same owner and name")
        self.assertEqual(by["https://github.com/other/game-recomp"], ("same name", "Decomp Buddy"))
        self.assertEqual(by["https://gitlab.com/dev/another"], ("same owner", "OSGL"))
        self.assertEqual(by["https://codeberg.org/mirror/Game-Recomp"][0], "same name")
        self.assertNotIn("https://github.com/gone/game-recomp", by)  # itself taken down
        self.assertNotIn("https://github.com/dev/game-recomp", by)  # the one that's gone

    def test_data_only_asks_nothing(self):
        with mock.patch.object(replacements, "_exists") as e, mock.patch.object(replacements, "_search") as s_:
            found = replacements.find("github.com/a/b", {"codeberg.org/a/b": "unbaked"}, set(), {}, ask=False)
        e.assert_not_called(); s_.assert_not_called()
        self.assertEqual([r["url"] for r in found], ["https://codeberg.org/a/b"])

    def test_github_search_needs_a_token(self):
        with mock.patch.object(replacements, "_json") as j:
            self.assertEqual(replacements._search("github.com", "x", {}), [])
        j.assert_not_called()


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
        self.store.settings["sources"] = {s: s == "playported" for s in self.store.settings["sources"]}

        def fake(store, name, ctx):
            if name == "quiver":
                return [Item("quiver", "https://github.com/q/q", "Q")]
            raise RuntimeError("site down")

        with mock.patch.object(engine, "_fetch_one", side_effect=fake):
            result = engine.refresh(self.store)
        self.assertEqual(result["errors"], ["playported: site down"])
        self.assertEqual({i["source"] for i in self.store.items()}, {"quiver", "playported"})
        self.assertEqual(self.store.seen(), {"github.com/x/y": "initial"})

    def test_names_and_dates_are_tidied(self):
        self.put([Item("playported", "https://github.com/x/y", "Y", platform="ps2", method="recomp",
                       added="2026-11-07T22:02:09")])
        c = engine.compare(self.store).missing[0]
        self.assertEqual((c.platform, c.method, c.added), ("PlayStation 2", "Recomp", "2026-11-07"))

    def test_clones_are_left_out_unless_asked_for(self):
        self.put([Item("osgameclones", "https://github.com/c/clone", "C", extra={"clone": True}),
                  Item("osgameclones", "https://github.com/r/remake", "R")])
        self.assertEqual([c.title for c in engine.compare(self.store).missing], ["R"])
        self.store.settings["include_clones"] = True
        self.assertEqual(sorted(c.title for c in engine.compare(self.store).missing), ["C", "R"])


    def test_forge_sites_count_as_supported(self):
        self.put([Item("playported", "https://git.example.dev/a/b", "Forge project"),
                  Item("playported", "https://shop.example/game", "Web page")])
        self.store.set_meta("forges", {"git.example.dev": {"kind": "forgejo", "checked": "2026-11-19"}})
        r = engine.compare(self.store)
        self.assertEqual([(c.title, c.forge) for c in r.missing], [("Forge project", "forgejo")])
        self.assertEqual([c.title for c in r.other_sites], ["Web page"])

    def test_runs_on_and_android(self):
        self.put([Item("decompbuddy", "https://github.com/a/b", "B", extra={"builds": "Windows, Android"}),
                  Item("trilarion", "https://github.com/a/b", "B", extra={"runs_on": "Linux, macOS"}),
                  Item("playported", "https://github.com/c/sm64-android", "SM64 port"),
                  Item("playported", "https://github.com/d/pc-only", "PC only")])
        by = {c.title: c for c in engine.compare(self.store).missing}
        self.assertEqual(by["B"].runs_on, "PC, Android, Linux, Mac")
        self.assertEqual({t for t, c in by.items() if c.android}, {"B", "SM64 port"})
        self.assertEqual(by["B"].systems, ["PC", "Android", "Linux", "Mac"])
        self.assertEqual(by["SM64 port"].systems, ["Android"])  # from its name
        self.assertEqual(by["PC only"].systems, [])


    def test_taken_down_projects_leave_missing(self):
        self.put([Item("playported", "https://github.com/x/gone", "Gone"), Item("playported", "https://github.com/x/fine", "Fine")])
        self.store.save_links({"github.com/x/gone": {"status": "gone", "checked": "2026-11-19"},
                               "github.com/x/fine": {"status": "ok", "checked": "2026-11-19"}})
        r = engine.compare(self.store)
        self.assertEqual([c.title for c in r.missing], ["Fine"])
        self.assertEqual([c.title for c in r.taken_down], ["Gone"])

    def test_shared_list_is_used_when_newer(self):
        self.put([Item("playported", "https://github.com/x/y", "Y")])
        self.store.save_links({"github.com/x/y": {"status": "ok", "checked": "2026-11-01"}})
        self.store.taken_down.update({"github.com/x/y": {"status": "blocked", "checked": "2026-11-19"}}, {}, by="bob")
        r = engine.compare(self.store)
        self.assertEqual([(c.title, c.link["by"]) for c in r.taken_down], [("Y", "bob")])

    def test_renamed_repository_matches_quiver(self):
        self.put([Item("quiver", "https://github.com/new/name", "Q", slug="q"), Item("playported", "https://github.com/old/name", "Old")])
        self.store.save_links({"github.com/old/name": {"status": "moved", "moved_to": "https://github.com/New/Name", "checked": "2026-11-19"}})
        r = engine.compare(self.store)
        self.assertEqual(r.missing, [])
        self.assertEqual(next(c for c in r.candidates if c.title == "Old").in_quiver[0].slug, "q")

    def test_check_links_saves_and_shares(self):
        self.put([Item("playported", "https://github.com/x/gone", "Gone"), Item("quiver", "https://github.com/q/q", "Q")])
        with mock.patch.object(linkcheck, "check", return_value={"github.com/x/gone": {"status": "gone"},
                                                                 "github.com/q/q": {"status": "ok"}}) as c:
            counts = engine.check_links(self.store)
        self.assertEqual(sorted(k for k, _ in c.call_args.args[0]), ["github.com/q/q", "github.com/x/gone"])
        self.assertEqual((counts["gone"], counts["ok"]), (1, 1))
        self.assertEqual(list(self.store.taken_down.entries), ["github.com/x/gone"])
        with mock.patch.object(linkcheck, "check", return_value={}) as c:
            self.assertEqual(engine.check_links(self.store)["checked"], 0)  # nothing due: nothing asked
        c.assert_not_called()

    def test_bitbucket_is_another_site(self):
        self.put([Item("playported", "https://bitbucket.org/a/b", "BB"), Item("playported", "https://codeberg.org/a/b", "CB")])
        r = engine.compare(self.store)
        self.assertEqual(([c.title for c in r.missing], [c.title for c in r.other_sites]), (["CB"], ["BB"]))


    def test_import_merges_newer_and_keeps_yours(self):
        self.store.decisions.set(WAITING, [{"key": "github.com/a/mine", "title": "Mine"}], reason="release")
        self.store.decisions.entries["github.com/a/both"] = {"list": BLACKLIST, "title": "Both", "date": "2026-11-10"}
        self.store.decisions._write()
        theirs = {"version": 1, "decisions": {
            "github.com/a/new": {"list": WAITING, "title": "New", "date": "2026-11-01", "reason": "host"},
            "github.com/a/both": {"list": WAITING, "title": "Both", "date": "2026-11-15", "reason": "source"},
            "github.com/a/mine": {"list": BLACKLIST, "title": "Mine", "date": "2026-01-01"}}}
        path = Path(self.tmp.name) / "sent.json"
        path.write_text(json.dumps(theirs))
        kind, counts = engine.import_file(self.store, str(path))
        self.assertEqual((kind, counts), ("decisions", {"added": 1, "updated": 1, "kept": 1}))
        d = self.store.decisions.entries
        self.assertEqual((d["github.com/a/both"]["list"], d["github.com/a/mine"]["list"]), (WAITING, WAITING))

        path.write_text(json.dumps({"version": 1, "links": {"github.com/x/y": {"status": "gone", "checked": "2026-11-19"},
                                                            "github.com/x/z": {"status": "ok", "checked": "2026-11-19"}}}))
        self.assertEqual(engine.import_file(self.store, str(path)), ("taken-down", {"added": 1, "updated": 0, "kept": 0}))
        out = Path(self.tmp.name) / "copy.json"
        self.assertEqual(engine.export_file(self.store, "taken-down", str(out)), 1)
        self.assertIn("github.com/x/y", json.loads(out.read_text())["links"])
        path.write_text("[1, 2]")
        with self.assertRaises(ValueError):
            engine.import_file(self.store, str(path))


    def test_quiver_apps_taken_down_and_their_replacements(self):
        self.put([Item("quiver", "https://github.com/dev/game", "Game", slug="game"),
                  Item("quiver", "https://github.com/dev/fine", "Fine", slug="fine"),
                  Item("unbaked", "https://codeberg.org/dev/game", "Game mirror")])
        with mock.patch.object(linkcheck, "check", return_value={"github.com/dev/game": {"status": "blocked"},
                                                                 "github.com/dev/fine": {"status": "ok"},
                                                                 "codeberg.org/dev/game": {"status": "ok"}}), \
                mock.patch.object(replacements, "_exists", return_value=None), \
                mock.patch.object(replacements, "_search", return_value=[]):
            counts = engine.check_links(self.store)
        self.assertEqual(counts["replacements"], 1)
        r = engine.compare(self.store)
        self.assertEqual([q.slug for q in r.quiver_down], ["game"])
        link = r.links["github.com/dev/game"]
        self.assertEqual([(x["url"], x["where"]) for x in link["replacements"]], [("https://codeberg.org/dev/game", "unbaked")])
        self.assertEqual(self.store.taken_down.get("github.com/dev/game")["replacements"][0]["how"], "same owner and name")
        # The next check of the same link keeps what was found, and isn't looked for again the same day.
        with mock.patch.object(linkcheck, "check", return_value={"github.com/dev/game": {"status": "blocked"}}), \
                mock.patch.object(replacements, "find") as f:
            engine.check_links(self.store, keys=["github.com/dev/game"])
        f.assert_not_called()
        self.assertTrue(engine.compare(self.store).links["github.com/dev/game"]["replacements"])


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
