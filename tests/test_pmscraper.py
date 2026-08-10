#!/usr/bin/env python3
"""End-to-end tests for pmscraper against a synthetic KNULLI tree.

No network, no real SD card: every test builds a throwaway ports tree in a temp
dir, runs pmscraper.py as a subprocess (so exit codes and the whole flow are
exercised), and asserts on the resulting gamelist.xml.

    python3 -m unittest discover -s tests
    python3 tests/test_pmscraper.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PMSCRAPER = REPO / "pmscraper.py"

# 1x1 PNGs of two different byte-lengths, so "size changed" copy logic is real.
PNG_A = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100051f2fdf0000000049454e44ae426082")
PNG_B = PNG_A + b"\x00" * 16

# The tag names EmulationStation accepts (es-app/src/MetaData.cpp) plus the
# structural ones. Every child tag pmscraper emits must be in here.
ALLOWED_TAGS = {
    "path", "name", "desc", "genre", "tags", "image", "thumbnail", "marquee",
    "titleshot", "fanart", "rating", "releasedate", "developer", "publisher",
    "players", "favorite", "hidden", "kidgame", "playcount", "lastplayed",
    "sortname", "video", "genreid", "region", "lang",
}


def port_json(title, script, genres=None, porter="Someone", desc="A game.",
              screenshot="shot.png", covers=("cover.png",)):
    return {
        "name": f"{title}.zip",
        "attr": {
            "title": title,
            "desc": desc,
            "inst": "Ready to run.",
            "genres": genres or ["action"],
            "porter": [porter],
            "image": {"screenshot": screenshot, "covers": list(covers)},
        },
        "items": [script],
        "rating": {"average_rating": 4, "max_rating": 5},
        "source": {"date_added": "2023-05-01"},
    }


class Tree:
    """A synthetic <root>/roms/ports tree with a PortMaster config beside it."""

    def __init__(self, base):
        self.base = Path(base)
        self.ports = self.base / "roms" / "ports"
        self.cfg = self.base / "system" / ".local" / "share" / "PortMaster" / "config"
        self.images_pm = self.cfg / "images_pm"
        self.ports.mkdir(parents=True)
        self.images_pm.mkdir(parents=True)

    def add_sh(self, name, body="#!/bin/sh\necho hi\n"):
        p = self.ports / name
        p.write_text(body)
        return p

    def add_port(self, title, script, portdir=None, **kw):
        self.add_sh(script)
        portdir = portdir or title.lower().replace(" ", "")
        d = self.ports / portdir
        d.mkdir(exist_ok=True)
        (d / "port.json").write_text(json.dumps(port_json(title, script, **kw)))

    def add_art(self, cleanname, screenshot=True, cover=True):
        if screenshot:
            (self.images_pm / f"{cleanname}.screenshot.png").write_bytes(PNG_A)
        if cover:
            (self.images_pm / f"{cleanname}.cover.png").write_bytes(PNG_B)

    def seed_catalog(self, ports_dict):
        (self.cfg / "pmscraper_ports.json").write_text(
            json.dumps({"ports": ports_dict}))

    def write_gamelist(self, xml):
        (self.ports / "gamelist.xml").write_text(xml)

    def gamelist(self):
        return self.ports / "gamelist.xml"

    def games(self):
        root = ET.parse(str(self.gamelist())).getroot()
        return {g.findtext("path"): g for g in root.findall("game")}


def run(tree, *args, expect=None):
    cmd = [sys.executable, str(PMSCRAPER), "--ports-dir", str(tree.ports),
           "--cfg-dir", str(tree.cfg), "--no-reload", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if expect is not None:
        assert proc.returncode == expect, (
            f"exit {proc.returncode} != {expect}\n{proc.stdout}\n{proc.stderr}")
    return proc


class PMScraperTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tree = Tree(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    # --- core behaviours (PLAN verification 1) --------------------------- #

    def test_dry_run_writes_nothing(self):
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        run(t, expect=0)
        self.assertFalse(t.gamelist().exists())
        self.assertFalse((t.ports / "images").exists())

    def test_apply_produces_valid_xml_and_art(self):
        t = self.tree
        t.add_port("Balatro", "Balatro.sh", genres=["fps", "card"])
        t.add_art("balatro")
        run(t, "--apply", expect=0)
        games = t.games()
        self.assertIn("./Balatro.sh", games)
        g = games["./Balatro.sh"]
        self.assertEqual(g.findtext("name"), "Balatro")
        self.assertEqual(g.findtext("publisher"), "PortMaster")
        self.assertEqual(g.findtext("genre"), "FPS, Card")   # GENRE_FIXUPS
        self.assertEqual(g.findtext("tags"), "fps, card")     # raw
        self.assertTrue(g.findtext("image").endswith("Balatro-image.png"))
        self.assertTrue(g.findtext("thumbnail").endswith("Balatro-thumb.png"))
        self.assertEqual(g.findtext("titleshot"), g.findtext("image"))
        self.assertTrue((t.ports / "images" / "Balatro-image.png").is_file())
        self.assertAlmostEqual(float(g.findtext("rating")), 0.8, places=3)

    def test_preserves_favorite_playcount_and_handtyped_name(self):
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        t.write_gamelist(
            "<?xml version='1.0'?><gameList><game>"
            "<path>./Balatro.sh</path><name>MY BALATRO</name>"
            "<favorite>true</favorite><playcount>42</playcount>"
            "</game></gameList>")
        run(t, "--apply", expect=0)
        g = t.games()["./Balatro.sh"]
        self.assertEqual(g.findtext("name"), "MY BALATRO")   # not overwritten
        self.assertEqual(g.findtext("favorite"), "true")
        self.assertEqual(g.findtext("playcount"), "42")
        self.assertEqual(g.findtext("publisher"), "PortMaster")  # but topped up

    def test_second_run_is_noop(self):
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        run(t, "--apply", expect=0)
        proc = run(t, "--apply", expect=0)
        self.assertIn("0 added, 0 written", proc.stdout)

    def test_force_overwrites(self):
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        t.write_gamelist(
            "<?xml version='1.0'?><gameList><game>"
            "<path>./Balatro.sh</path><name>OLD</name></game></gameList>")
        run(t, "--apply", expect=0)
        self.assertEqual(t.games()["./Balatro.sh"].findtext("name"), "OLD")
        run(t, "--apply", "--force", expect=0)
        self.assertEqual(t.games()["./Balatro.sh"].findtext("name"), "Balatro")

    def test_malformed_gamelist_kept_as_broken(self):
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        t.write_gamelist("<gameList><game><path>./x.sh</not-closed>")
        run(t, "--apply", expect=0)
        self.assertTrue((t.ports / "gamelist.xml.broken").is_file())
        self.assertIn("./Balatro.sh", t.games())   # fresh, well-formed

    def test_online_recovers_port_without_json(self):
        t = self.tree
        t.add_sh("cavestory.sh")            # archive-named .sh, no port.json
        t.seed_catalog({"cavestory.zip": port_json("Cave Story", "cavestory.sh")})
        # offline: unknown; online: catalog (stem matches the archive name)
        self.assertIn("unknown", run(t).stdout)
        proc = run(t, "--online")
        self.assertIn("1 from catalog", proc.stdout)

    def test_online_fuzzy_matches_title(self):
        t = self.tree
        t.add_sh("Cave Story.sh")           # stem matches the title, not archive
        t.seed_catalog({"cavestory.zip": port_json("Cave Story", "cavestory.sh")})
        proc = run(t, "--online")
        self.assertIn("1 fuzzy", proc.stdout)
        # --no-fuzzy demotes it back to unknown
        self.assertIn("unknown    1", run(t, "--online", "--no-fuzzy").stdout)

    def test_string_image_does_not_crash_online(self):
        # descent/descent2 store attr.image as a bare string, not a dict; with a
        # local screenshot but no cover, --online calls download_art for the
        # cover, which must not choke on the string.
        t = self.tree
        t.add_sh("Descent.sh")
        d = t.ports / "descent"
        d.mkdir()
        info = port_json("Descent", "Descent.sh")
        info["attr"]["image"] = "descent.screenshot.png"   # string, not dict
        (d / "port.json").write_text(json.dumps(info))
        t.add_art("descent", screenshot=True, cover=False)
        t.seed_catalog({})
        run(t, "--apply", "--online", expect=0)             # must not crash
        g = t.games()["./Descent.sh"]
        self.assertEqual(g.findtext("name"), "Descent")
        self.assertTrue(g.findtext("image").endswith("Descent-image.png"))

    # --- classification (PLAN verification 2) ---------------------------- #

    def test_unknown_untouched_and_exit_1(self):
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        t.add_sh("my-test-launcher.sh")
        proc = run(t, "--apply", expect=1)      # unknown present -> exit 1
        self.assertNotIn("./my-test-launcher.sh", t.games())
        self.assertIn("unknown    1", proc.stdout)

    def test_stub_unknown_writes_tidied_name_only(self):
        t = self.tree
        t.add_sh("my-test_launcher.sh")
        run(t, "--apply", "--stub-unknown", expect=1)
        g = t.games()["./my-test_launcher.sh"]
        self.assertEqual(g.findtext("name"), "My Test Launcher")
        self.assertIsNone(g.find("image"))       # nothing else written

    def test_nested_flagged_and_not_stubbed(self):
        t = self.tree
        sub = t.ports / "CaveStory"
        sub.mkdir()
        (sub / "inner.sh").write_text("#!/bin/sh\n")
        proc = run(t, "--apply", "--stub-unknown", expect=1)
        self.assertIn("nested", proc.stdout)
        # nested unknown is never stubbed into the gamelist
        self.assertFalse(t.gamelist().exists() and
                         "CaveStory/inner.sh" in t.games())

    def test_stale_reported_and_pruned_only_on_flag(self):
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        t.write_gamelist(
            "<?xml version='1.0'?><gameList>"
            "<game><path>./Balatro.sh</path></game>"
            "<game><path>./Gone.sh</path><name>Gone</name></game>"
            "</gameList>")
        proc = run(t, "--apply", expect=0)
        self.assertIn("1  gamelist entry with no matching file", proc.stdout)
        self.assertIn("./Gone.sh", t.games())            # not pruned by default
        run(t, "--apply", "--prune", expect=0)
        self.assertNotIn("./Gone.sh", t.games())         # pruned now

    def test_since_scrapes_only_new_ports(self):
        # The auto-run hook's "new ports only": --since EPOCH restricts to ports
        # whose port.json was written at/after EPOCH.
        t = self.tree
        t.add_port("Oldgame", "Old.sh")
        t.add_port("Newgame", "New.sh")
        os.utime(t.ports / "oldgame" / "port.json", (1000, 1000))  # far in the past
        run(t, "--apply", "--since", "100000", expect=0)
        games = t.games()
        self.assertNotIn("./Old.sh", games)   # old port skipped
        self.assertIn("./New.sh", games)      # only the new one scraped

    def test_emit_progress_splits_stdout_and_stderr(self):
        # --emit-progress: machine PMPROG lines on stdout, human log on stderr.
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        proc = run(t, "--apply", "--emit-progress", expect=0)
        self.assertIn("PMPROG\t1\t1\tBalatro", proc.stdout)
        self.assertNotIn("PMPROG", proc.stderr)
        self.assertIn("pmscraper", proc.stderr)   # header went to stderr
        self.assertNotIn("ports dir", proc.stdout)  # stdout stays clean

    def test_only_missing_skips_complete(self):
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        # a "complete" entry: has image + desc already
        t.write_gamelist(
            "<?xml version='1.0'?><gameList><game>"
            "<path>./Balatro.sh</path><name>B</name>"
            "<image>./x.png</image><desc>d</desc></game></gameList>")
        proc = run(t, "--apply", "--only-missing", expect=0)
        self.assertIn("0 added, 0 written", proc.stdout)

    def test_partial_is_picked_up_by_only_missing(self):
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        t.write_gamelist(                                # has name, no image
            "<?xml version='1.0'?><gameList><game>"
            "<path>./Balatro.sh</path><name>B</name></game></gameList>")
        run(t, "--apply", "--only-missing", expect=0)
        self.assertTrue(t.games()["./Balatro.sh"].findtext("image"))

    def test_progress_is_safe_with_no_es(self):
        # --progress posts to 127.0.0.1:1234; with no ES listening it must
        # degrade silently, not crash or fail the run.
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        run(t, "--apply", "--progress", expect=0)
        self.assertTrue(t.games()["./Balatro.sh"].findtext("image"))

    def test_register_tools_adds_present_launchers_only(self):
        t = self.tree
        t.add_sh("PortMaster.sh")
        t.add_sh("PortMaster Scraper.sh")
        # a pre-existing hand-edited name must be preserved (non-destructive)
        t.write_gamelist(
            "<?xml version='1.0'?><gameList><game>"
            "<path>./PortMaster.sh</path><name>My PM</name></game></gameList>")
        run(t, "--apply", "--register-tools")
        games = t.games()
        self.assertEqual(games["./PortMaster.sh"].findtext("name"), "My PM")  # kept
        self.assertEqual(games["./PortMaster.sh"].findtext("genre"), "Utility")  # added
        self.assertEqual(games["./PortMaster Scraper.sh"].findtext("name"),
                         "PortMaster Scraper")

    def test_register_tools_skips_absent_launcher(self):
        t = self.tree
        t.add_sh("PortMaster Scraper.sh")   # no PortMaster.sh on disk
        run(t, "--apply", "--register-tools")
        self.assertNotIn("./PortMaster.sh", t.games())
        self.assertIn("./PortMaster Scraper.sh", t.games())

    def test_unregister_self_removes_only_scraper_entry(self):
        t = self.tree
        t.write_gamelist(
            "<?xml version='1.0'?><gameList>"
            "<game><path>./PortMaster.sh</path><name>PortMaster</name></game>"
            "<game><path>./PortMaster Scraper.sh</path><name>PortMaster Scraper</name></game>"
            "<game><path>./Balatro.sh</path><name>Balatro</name></game>"
            "</gameList>")
        run(t, "--apply", "--unregister-self")
        games = t.games()
        self.assertNotIn("./PortMaster Scraper.sh", games)   # removed
        self.assertIn("./PortMaster.sh", games)              # kept
        self.assertIn("./Balatro.sh", games)                 # kept

    def test_unregister_self_dry_run_keeps_entry(self):
        t = self.tree
        t.write_gamelist(
            "<?xml version='1.0'?><gameList><game>"
            "<path>./PortMaster Scraper.sh</path><name>X</name></game></gameList>")
        proc = run(t, "--unregister-self")   # no --apply
        self.assertIn("would remove", proc.stdout)
        self.assertIn("./PortMaster Scraper.sh", t.games())

    def test_register_tools_dry_run_writes_nothing(self):
        t = self.tree
        t.add_sh("PortMaster.sh")
        t.add_sh("PortMaster Scraper.sh")
        proc = run(t, "--register-tools")           # no --apply
        self.assertIn("would register", proc.stdout)
        self.assertFalse(t.gamelist().exists())

    # --- output hygiene (PLAN verification 3) ---------------------------- #

    def test_all_emitted_tags_are_valid(self):
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        run(t, "--apply", "--port-dates", expect=0)
        root = ET.parse(str(t.gamelist())).getroot()
        for game in root.findall("game"):
            for child in game:
                self.assertIn(child.tag, ALLOWED_TAGS,
                              f"unexpected tag <{child.tag}>")

    def test_prefer_covers_swaps_image_and_thumbnail(self):
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        run(t, "--apply", "--prefer-covers", expect=0)
        g = t.games()["./Balatro.sh"]
        self.assertTrue(g.findtext("image").endswith("Balatro-thumb.png"))
        self.assertTrue(g.findtext("thumbnail").endswith("Balatro-image.png"))

    def test_report_file_written(self):
        t = self.tree
        t.add_port("Balatro", "Balatro.sh")
        t.add_art("balatro")
        rep = Path(self._tmp.name) / "r.md"
        run(t, f"--report={rep}")
        self.assertTrue(rep.is_file())
        self.assertIn("| path | bucket", rep.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
