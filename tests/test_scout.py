import csv
import json
import tempfile
import unittest
from pathlib import Path

from quiver_scout import engine
from quiver_scout.sources import Item, playported, quiver, recomplauncher, unbaked
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


class Sources(unittest.TestCase):
    def test_recomplauncher(self):
        items = recomplauncher.parse(json.loads((FIX / "recomplauncher_pc.json").read_text()))
        by = {i.slug: i for i in items}
        self.assertEqual(by["zelda64recomp"].key, "github.com/zelda64recomp/zelda64recomp")
        self.assertEqual(by["zelda64recomp"].page, "https://recomplauncher.com/game/zelda64recomp")
        self.assertTrue(any(i.url.startswith("https://gitlab.com/") for i in items))
        self.assertEqual(len(items), 3)  # the one without a repository is left out

    def test_unbaked_additions(self):
        items = unbaked.parse_additions(json.loads((FIX / "unbaked_additions.json").read_text()))
        self.assertEqual(len(items), 3)
        tribes = next(i for i in items if "tribes" in i.key)
        self.assertEqual((tribes.game, tribes.method), ("Tribes 2", "Static recomp"))
        self.assertTrue(tribes.page.startswith("https://unbaked.games/games/"))

    def test_unbaked_skips_agent_environments(self):
        record = {"slug": "x", "title": "X", "projects": [
            {"name": "env", "url": "https://github.com/a/env", "methods": ["Agent environment"]},
            {"name": "port", "url": "https://github.com/a/port", "methods": ["Source port", "Agent environment"]},
        ]}
        self.assertEqual([i.title for i in unbaked.parse_game(record)], ["port"])

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

    def test_quiver_csv_column_detection(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
            csv.writer(f).writerows([["Name", "Repository URL", "Slug"], ["Zelda", "https://github.com/a/b", "zelda"], ["Empty", "", "x"]])
        items = quiver.from_csv(f.name)
        self.assertEqual([(i.title, i.key, i.slug) for i in items], [("Zelda", "github.com/a/b", "zelda")])


class Compare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self, items, refreshed="2026-11-19 10:00"):
        self.store.snapshot = {"refreshed_at": refreshed, "items": [i.to_dict() for i in items], "errors": []}

    def test_merges_sources_and_matches_quiver(self):
        self.snapshot([
            Item("quiver", "https://github.com/a/zelda", "Zelda Recomp", game="Majora's Mask", slug="zelda"),
            Item("recomplauncher", "https://github.com/A/Zelda", "Zelda 64", page="https://rl/zelda"),
            Item("playported", "https://github.com/x/new-thing/releases", "New Thing", game="Majora's Mask", page="https://pp/1"),
            Item("unbaked", "github.com/x/new-thing", "new-thing", page="https://ub/1", added="2026-11-01"),
        ])
        r = engine.compare(self.store)
        self.assertEqual(len(r.candidates), 2)
        self.assertEqual([c.key for c in r.missing], ["github.com/x/new-thing"])
        new = r.missing[0]
        self.assertEqual(new.sources, ["unbaked", "playported"])  # display order
        self.assertEqual(set(new.pages), {"unbaked", "playported"})
        self.assertEqual([q.slug for q in new.same_game], ["zelda"])
        listed = next(c for c in r.candidates if c.in_quiver)
        self.assertEqual(listed.in_quiver[0].slug, "zelda")

    def test_blacklist_hides_from_missing_and_persists(self):
        self.snapshot([Item("playported", "https://github.com/x/y", "Y")])
        r = engine.compare(self.store)
        engine.blacklist(self.store, r.missing, "not a recomp")
        r = engine.compare(self.store)
        self.assertEqual(r.missing, [])
        self.assertEqual(r.blacklisted[0].blacklisted["reason"], "not a recomp")
        self.assertIn("github.com/x/y", Store(self.tmp.name).blacklist)
        engine.unblacklist(self.store, ["github.com/x/y"])
        self.assertEqual(len(engine.compare(self.store).missing), 1)

    def test_new_since_last_refresh(self):
        self.store.seen = {"github.com/x/old": "initial", "github.com/x/new": "2026-11-19 10:00"}
        self.snapshot([Item("playported", "https://github.com/x/old", "Old"), Item("playported", "https://github.com/x/new", "New")])
        r = engine.compare(self.store)
        self.assertEqual([c.title for c in r.missing if r.is_new(c)], ["New"])

    def test_refresh_keeps_last_items_of_a_failing_source(self):
        self.snapshot([Item("playported", "https://github.com/x/y", "Y")])
        self.store.settings["sources"] = {"recomplauncher": False, "unbaked": False, "playported": True}
        import quiver_scout.engine as e

        real = e._fetch_one

        def fake(store, name, progress):
            if name == "quiver":
                return [Item("quiver", "https://github.com/q/q", "Q")]
            raise RuntimeError("site down")

        e._fetch_one = fake
        try:
            snap = e.refresh(self.store)
        finally:
            e._fetch_one = real
        self.assertEqual(snap["errors"], ["playported: site down"])
        self.assertEqual({i["source"] for i in snap["items"]}, {"quiver", "playported"})
        self.assertEqual(self.store.seen, {"github.com/x/y": "initial"})

    def test_csv_exports(self):
        self.snapshot([Item("quiver", "https://github.com/a/b", "AB", game="G", slug="ab"), Item("unbaked", "https://github.com/c/d", "CD")])
        r = engine.compare(self.store)
        out = Path(self.tmp.name) / "q.csv"
        engine.write_quiver_csv(str(out), r.quiver)
        self.assertEqual(next(csv.DictReader(out.open()))["url"], "https://github.com/a/b")
        engine.write_candidates_csv(str(out), r.missing)
        row = next(csv.DictReader(out.open()))
        self.assertEqual((row["title"], row["status"], row["sources"]), ("CD", "missing", "unbaked"))


if __name__ == "__main__":
    unittest.main()
