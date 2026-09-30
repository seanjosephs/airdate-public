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
