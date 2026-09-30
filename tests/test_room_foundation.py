"""Slice 1 foundations: the room's config keys and the arrivals sidecar.

The config keys must merge forward, because an existing install already has a
config.json written before these keys existed and the writer should never have
to re-run setup for them.

The arrivals sidecar is how a card knows the age of its paper. It records when
an essay first arrived in airdate, it lives in the runtime dir and never in the
vault, and its clock only ever goes up.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import airdate_config  # noqa: E402


def point_server_at(server, root: Path) -> Path:
    """Repoint the cached server module at a fresh vault under `root`.

    server.py reads its folders at import and the module is shared by every
    test class in the run, so an earlier class's temp vault - already deleted -
    is what it would otherwise scan. apply_config is how the existing suites
    move it (see test_config.ServerSettingsTests.configure). Returns the essays
    folder notes should be written into."""
    import copy as _copy
    import airdate_config as _cfg
    vault = root / "vault"
    (vault / ".obsidian").mkdir(parents=True, exist_ok=True)
    (vault / "Essays").mkdir(parents=True, exist_ok=True)
    config = _cfg.DEFAULT_CONFIG and _copy.deepcopy(_cfg.DEFAULT_CONFIG)
    config["vault"]["path"] = str(vault)
    config["vault"]["essays_folder"] = "Essays"
    server.apply_config(config, True)
    return Path(server.OBSIDIAN_ESSAYS_DIR)


class ConfigMergesForwardTests(unittest.TestCase):
    """A config.json written before the room existed still loads."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, payload):
        airdate_config.config_path(self.dir).parent.mkdir(parents=True, exist_ok=True)
        airdate_config.config_path(self.dir).write_text(json.dumps(payload), encoding="utf-8")

    def test_the_room_keys_have_defaults(self):
        for key in ("board", "editor", "red_pen", "paper"):
            with self.subTest(key=key):
                self.assertIn(key, airdate_config.DEFAULT_CONFIG)

    def test_an_old_config_gains_the_room_keys(self):
        # Exactly what an install from before this slice has on disk.
        self.write({"config_version": 1, "vault": {"path": "/somewhere", "essays_folder": "Essays"}})
        config, existed, error = airdate_config.load_config(self.dir)
        self.assertTrue(existed)
        self.assertEqual(error, "")
        self.assertEqual(config["vault"]["path"], "/somewhere")
        for key in ("board", "editor", "red_pen", "paper"):
            with self.subTest(key=key):
                self.assertIn(key, config)

    def test_the_writers_own_values_survive_the_merge(self):
        self.write({"config_version": 1, "board": {"weeks_shown": 6}})
        config, _, _ = airdate_config.load_config(self.dir)
        self.assertEqual(config["board"]["weeks_shown"], 6)
        # and the keys they did not set still arrive
        self.assertIn("default_pad", config["board"])

    def test_red_pen_is_on_by_default_with_no_custom_lines(self):
        config, _, _ = airdate_config.load_config(self.dir)
        self.assertIs(config["red_pen"]["enabled"], True)
        self.assertEqual(config["red_pen"]["lines"], [])

    def test_paper_does_not_freshen_on_promotion_by_default(self):
        config, _, _ = airdate_config.load_config(self.dir)
        self.assertIs(config["paper"]["fresh_on_promotion"], False)

    def test_the_editor_starts_simplified(self):
        config, _, _ = airdate_config.load_config(self.dir)
        self.assertEqual(config["editor"]["mode"], "simplified")

    def test_the_example_config_carries_the_room_keys(self):
        example = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        for key in ("board", "editor", "red_pen", "paper"):
            with self.subTest(key=key):
                self.assertIn(key, example)


class ArrivalsTests(unittest.TestCase):
    """When an essay first showed up in airdate. Paper age counts from here."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        # The runtime dir sits BESIDE the vault in a real install, never inside
        # it - the sidecar must never land in the writer's essays folder.
        root = Path(cls.tmp.name)
        (root / "vault").mkdir()
        (root / "runtime").mkdir()
        os.environ["OBSIDIAN_ESSAYS_DIR"] = str(root / "vault")
        os.environ["AIR_DATE_DATA_DIR"] = str(root / "runtime")
        import server  # noqa: PLC0415
        cls.server = server

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.server.reset_arrivals()

    def test_the_first_sighting_is_recorded(self):
        first = self.server.arrival_for("essay-a", "2026-09-01T10:00:00+00:00")
        self.assertEqual(first, "2026-09-01T10:00:00+00:00")

    def test_the_clock_only_goes_up(self):
        self.server.arrival_for("essay-a", "2026-09-01T10:00:00+00:00")
        # A later sighting must not move the arrival forward.
        again = self.server.arrival_for("essay-a", "2026-09-20T10:00:00+00:00")
        self.assertEqual(again, "2026-09-01T10:00:00+00:00")

    def test_an_earlier_sighting_does_not_move_it_either(self):
        # Rescheduling, re-indexing and uid mints must never reset the paper.
        self.server.arrival_for("essay-a", "2026-09-01T10:00:00+00:00")
        earlier = self.server.arrival_for("essay-a", "2026-08-01T10:00:00+00:00")
        self.assertEqual(earlier, "2026-09-01T10:00:00+00:00")

    def test_arrivals_survive_a_reload(self):
        self.server.arrival_for("essay-a", "2026-09-01T10:00:00+00:00")
        self.server.load_arrivals.cache_clear() if hasattr(self.server.load_arrivals, "cache_clear") else None
        self.assertEqual(
            self.server.arrival_for("essay-a", "2026-09-30T10:00:00+00:00"),
            "2026-09-01T10:00:00+00:00",
        )

    def test_an_id_remap_carries_the_arrival_across(self):
        self.server.arrival_for("hash-id", "2026-09-01T10:00:00+00:00")
        self.server.carry_arrival("hash-id", "uid-id")
        self.assertEqual(
            self.server.arrival_for("uid-id", "2026-09-30T10:00:00+00:00"),
            "2026-09-01T10:00:00+00:00",
        )

    def test_reset_clears_every_clock(self):
        self.server.arrival_for("essay-a", "2026-09-01T10:00:00+00:00")
        self.server.reset_arrivals()
        self.assertEqual(
            self.server.arrival_for("essay-a", "2026-09-30T10:00:00+00:00"),
            "2026-09-30T10:00:00+00:00",
        )

    def test_the_sidecar_lives_in_the_runtime_dir_not_the_vault(self):
        self.server.arrival_for("essay-a", "2026-09-01T10:00:00+00:00")
        self.assertTrue(self.server.ARRIVALS_LOG.exists())
        self.assertNotIn(
            str(self.server.OBSIDIAN_ESSAYS_DIR),
            str(self.server.ARRIVALS_LOG),
        )


if __name__ == "__main__":
    unittest.main()


class TotemArtTests(unittest.TestCase):
    """Where a totem's art comes from: the writer's vault, then what we ship.

    A fresh clone has no vault art, so the shipped set is what makes airdate
    look right out of the box. A placeholder is the last resort, for a totem
    key nothing ships art for.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "vault").mkdir()
        (root / "runtime").mkdir()
        os.environ["OBSIDIAN_ESSAYS_DIR"] = str(root / "vault")
        os.environ["AIR_DATE_DATA_DIR"] = str(root / "runtime")
        import server  # noqa: PLC0415
        cls.server = server

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a_missing_vault_path_falls_back_to_the_shipped_art(self):
        self.assertEqual(
            self.server.totem_image_url("fox", "Essays/_assets/airdate/totems/fox-1024.webp", 0),
            "/static/totems/fox-512.webp",
        )

    def test_no_vault_path_at_all_uses_the_shipped_art(self):
        self.assertEqual(self.server.totem_image_url("phoenix", "", 4), "/static/totems/phoenix-512.webp")

    def test_a_key_with_no_shipped_art_still_gets_a_placeholder(self):
        self.assertEqual(self.server.totem_image_url("fire", "", 0), "/static/totems/placeholder-1.svg")

    def test_all_five_shipped_totems_are_present(self):
        for index, key in enumerate(("fox", "octopus", "bison", "elephant", "phoenix")):
            with self.subTest(totem=key):
                self.assertEqual(
                    self.server.totem_image_url(key, "", index),
                    f"/static/totems/{key}-512.webp",
                )

    def test_a_vault_path_that_resolves_beats_the_shipped_art(self):
        # resolve_vault_asset does the real containment and suffix checks; here
        # we only care that a resolving path is preferred over what we ship.
        original = self.server.resolve_vault_asset
        self.server.resolve_vault_asset = lambda rel: Path("/somewhere") / rel
        try:
            self.assertEqual(
                self.server.totem_image_url("fox", "Essays/_assets/airdate/totems/fox-1024.webp", 0),
                "/vault-asset/Essays/_assets/airdate/totems/fox-1024.webp",
            )
        finally:
            self.server.resolve_vault_asset = original

    def test_the_writers_art_is_preferred_for_a_custom_key_too(self):
        original = self.server.resolve_vault_asset
        self.server.resolve_vault_asset = lambda rel: Path("/somewhere") / rel
        try:
            self.assertEqual(
                self.server.totem_image_url("fire", "Essays/_assets/fire.png", 0),
                "/vault-asset/Essays/_assets/fire.png",
            )
        finally:
            self.server.resolve_vault_asset = original


class ResetRoomTests(unittest.TestCase):
    """The settings action that restarts every paper clock. It must be asked for."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "vault").mkdir()
        (root / "runtime").mkdir()
        os.environ["OBSIDIAN_ESSAYS_DIR"] = str(root / "vault")
        os.environ["AIR_DATE_DATA_DIR"] = str(root / "runtime")
        import server  # noqa: PLC0415
        cls.server = server

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.server.reset_arrivals()
        self.server.arrival_for("essay-a", "2026-09-01T10:00:00+00:00")
        self.server.arrival_for("essay-b", "2026-09-02T10:00:00+00:00")

    def test_without_confirmation_nothing_is_cleared(self):
        result = self.server.reset_room({})
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.server.load_arrivals()), 2)

    def test_a_truthy_string_is_not_confirmation(self):
        # Only the literal true counts; "yes" or "1" from a sloppy client does not.
        self.assertFalse(self.server.reset_room({"confirm": "yes"})["ok"])
        self.assertEqual(len(self.server.load_arrivals()), 2)

    def test_confirmed_it_clears_every_clock_and_says_how_many(self):
        result = self.server.reset_room({"confirm": True})
        self.assertEqual(result, {"ok": True, "cleared": 2})
        self.assertEqual(self.server.load_arrivals(), {})


class ArrivalSurvivesAMintTests(unittest.TestCase):
    """The first write to an old note mints its uid, which changes its id.

    carry_arrival existed for exactly this and was never called, so that first
    write - most often the star - handed the essay brand-new paper.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "vault").mkdir()
        (root / "runtime").mkdir()
        os.environ["OBSIDIAN_ESSAYS_DIR"] = str(root / "vault")
        os.environ["AIR_DATE_DATA_DIR"] = str(root / "runtime")
        import server  # noqa: PLC0415
        from unittest import mock  # noqa: PLC0415
        cls.server = server
        cls.vault = point_server_at(server, root)
        # The arrivals sidecar path is also fixed at import; give this class
        # its own, and start from an empty cache.
        cls._patch = mock.patch.object(server, "ARRIVALS_LOG", root / "runtime" / "arrivals.json")
        cls._patch.start()
        server._ARRIVALS_CACHE = None

    @classmethod
    def tearDownClass(cls):
        cls._patch.stop()
        cls.server._ARRIVALS_CACHE = None
        cls.server.load_and_apply_config()
        cls.tmp.cleanup()

    def old_note(self, name):
        path = self.vault / name
        path.write_text(f'---\ntitle: "{name}"\n---\nbody\n', encoding="utf-8")  # no uid yet
        rows = self.server.discover_essays()
        rows = rows[0] if isinstance(rows, tuple) else rows
        essay = next(r for r in rows if r["relative_path"] == name)
        arrivals = self.server.load_arrivals()
        arrivals[essay["id"]] = "2026-01-01T00:00:00+00:00"
        self.server.save_arrivals(arrivals)
        return essay["id"]

    def arrival_after(self, name):
        rows = self.server.discover_essays()
        rows = rows[0] if isinstance(rows, tuple) else rows
        return next(r for r in rows if r["relative_path"] == name)["arrived_at"]

    def test_a_status_change_that_mints_keeps_the_paper(self):
        old_id = self.old_note("Mint A.md")
        result = self.server.set_essay_status(old_id, "Writers Likey", None, None, None)
        self.assertNotEqual(result.get("new_id"), old_id)  # the id really did change
        self.assertEqual(self.arrival_after("Mint A.md"), "2026-01-01T00:00:00+00:00")

    def test_an_ordinary_save_that_mints_keeps_the_paper(self):
        old_id = self.old_note("Mint B.md")
        self.server.save_essay_updates(old_id, {"subtitle": "first touch"})
        self.assertEqual(self.arrival_after("Mint B.md"), "2026-01-01T00:00:00+00:00")


class OpenMapConfigTests(unittest.TestCase):
    """editor.sections holds whatever sections the writer switched. Its default
    is empty, and _merge used to keep only keys a default already had - so it
    could never hold anything."""

    def test_editor_sections_survive_a_load(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            d = Path(tmp.name)
            airdate_config.config_path(d).parent.mkdir(parents=True, exist_ok=True)
            airdate_config.config_path(d).write_text(
                json.dumps({"editor": {"mode": "custom", "sections": {"seo_social": True, "notes": False}}}),
                encoding="utf-8",
            )
            config, _, _ = airdate_config.load_config(d)
            self.assertEqual(config["editor"]["sections"], {"seo_social": True, "notes": False})
        finally:
            tmp.cleanup()

    def test_unknown_top_level_keys_are_still_dropped(self):
        # The open-map rule is for empty default dicts only, not a free-for-all.
        tmp = tempfile.TemporaryDirectory()
        try:
            d = Path(tmp.name)
            airdate_config.config_path(d).parent.mkdir(parents=True, exist_ok=True)
            airdate_config.config_path(d).write_text(json.dumps({"surprise": {"a": 1}}), encoding="utf-8")
            config, _, _ = airdate_config.load_config(d)
            self.assertNotIn("surprise", config)
        finally:
            tmp.cleanup()


class DefaultTotemsTests(unittest.TestCase):
    """A fresh install starts with the five totems airdate ships art for.

    Blank roles and keywords: the art and colours are the default set, the
    taxonomy is the writer's own to fill in."""

    EXPECTED = {
        "fox": "#FF5B45", "octopus": "#3FD9EC", "bison": "#8FD16A",
        "elephant": "#8EA2FF", "phoenix": "#B98CFF",
    }

    def test_the_default_keys_are_the_shipped_five(self):
        keys = [t["key"] for t in airdate_config.DEFAULT_CONFIG["totems"]["items"]]
        self.assertEqual(keys, list(self.EXPECTED))

    def test_the_colours_match_the_design_tokens(self):
        for item in airdate_config.DEFAULT_CONFIG["totems"]["items"]:
            with self.subTest(totem=item["key"]):
                self.assertEqual(item["color"].upper(), self.EXPECTED[item["key"]].upper())

    def test_no_taxonomy_ships(self):
        for item in airdate_config.DEFAULT_CONFIG["totems"]["items"]:
            with self.subTest(totem=item["key"]):
                self.assertEqual(item["role"], "")
                self.assertEqual(item["keywords"], {})
                self.assertEqual(item["image"], "")

    def test_every_default_has_shipped_art(self):
        for item in airdate_config.DEFAULT_CONFIG["totems"]["items"]:
            with self.subTest(totem=item["key"]):
                self.assertTrue((ROOT / "static" / "totems" / f"{item['key']}-512.webp").is_file())

    def test_the_example_config_matches(self):
        example = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual([t["key"] for t in example["totems"]["items"]], list(self.EXPECTED))


class RoomFocusCssTests(unittest.TestCase):
    """Two faults in the shared focus rule, found in the slice 2 review."""

    CSS = (ROOT / "static" / "room" / "room.css").read_text(encoding="utf-8")

    def focus_block(self):
        start = self.CSS.index(":focus-visible {")
        return self.CSS[start: self.CSS.index("}", start)]

    def test_focus_does_not_change_a_controls_shape(self):
        # A border-radius here reshaped whatever took focus: nav corners jumped
        # from 8px to 5px. The outline follows the element's own radius anyway.
        self.assertNotIn("border-radius", self.focus_block())

    def test_a_sticky_itself_gets_the_paper_ring(self):
        # ".sticky :focus-visible" only matches things INSIDE a sticky. The
        # post-it is itself a focusable sticky.
        self.assertIn(".sticky:focus-visible", self.CSS)
        self.assertIn(".note:focus-visible", self.CSS)
