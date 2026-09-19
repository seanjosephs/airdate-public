"""Settings: config.json loading, validation, env precedence, and how the
server behaves when the writer's taxonomy is on, off or custom."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import airdate_config  # noqa: E402

ENV_KEYS = (
    "OBSIDIAN_ESSAYS_DIR",
    "AIRDATE_VAULT_DIR",
    "AIRDATE_ESSAYS_FOLDER",
    "OBSIDIAN_VAULT_NAME",
    "SUBSTACK_PUB",
    "AIRDATE_CONNECTOR_PORT",
)


class CleanEnv:
    """Hide the settings env vars for the duration of a test."""

    def __enter__(self):
        self.saved = {key: os.environ.pop(key) for key in ENV_KEYS if key in os.environ}
        return self

    def __exit__(self, *exc):
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(self.saved)


def make_vault(root: Path, essays: str = "Essays") -> Path:
    vault = root / "My Vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / essays).mkdir(parents=True)
    return vault


class ConfigFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_are_neutral_and_valid(self):
        config, exists, error = airdate_config.load_config(self.data)
        self.assertFalse(exists)
        self.assertEqual(error, "")
        self.assertEqual(airdate_config.validate_config(config), [])
        self.assertEqual(config["vault"]["path"], "")
        self.assertEqual(config["substack"]["publication"], "")
        self.assertIsNone(config["calendar"]["publish_day"])
        self.assertEqual(config["tag_presets"], [])
        self.assertEqual(config["links"], [])
        self.assertTrue(config["totems"]["enabled"])
        self.assertEqual(len(config["totems"]["items"]), 5)

    def test_unreadable_file_is_reported_not_raised(self):
        airdate_config.config_path(self.data).write_text("{nope", encoding="utf-8")
        config, exists, error = airdate_config.load_config(self.data)
        self.assertTrue(exists)
        self.assertIn("could not be read", error)
        self.assertEqual(config["vault"]["path"], "")

    def test_partial_file_keeps_default_shape(self):
        airdate_config.config_path(self.data).write_text(json.dumps({"calendar": {"publish_day": "friday"}}), encoding="utf-8")
        config, _, _ = airdate_config.load_config(self.data)
        self.assertEqual(config["calendar"]["publish_day"], "friday")
        self.assertEqual(config["vault"]["essays_folder"], "Essays")

    def test_save_is_private(self):
        path = airdate_config.save_config(self.data, airdate_config.DEFAULT_CONFIG)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_validation_names_each_problem(self):
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        config["vault"]["essays_folder"] = "../outside"
        config["calendar"]["publish_day"] = "Funday"
        config["totems"]["items"][0]["color"] = "red"
        config["totems"]["default"] = "nope"
        config["categories"]["items"] = [{"name": "Published"}, {"name": "a/b"}]
        config["tag_presets"] = [{"name": "Too many", "tags": ["a", "b", "c", "d", "e", "f"]}]
        config["links"] = [{"label": "x", "url": "javascript:alert(1)"}]
        errors = " ".join(airdate_config.validate_config(config))
        for fragment in ("essays_folder", "publish_day", "color", "totems.default", "reserved", "plain folder name",
                         "up to five", "links[0].url"):
            self.assertIn(fragment, errors)

    def test_env_overrides_file(self):
        with CleanEnv():
            os.environ["SUBSTACK_PUB"] = "https://env.substack.com"
            os.environ["OBSIDIAN_ESSAYS_DIR"] = "/tmp/Vault/Writing/Essays"
            os.environ["AIRDATE_VAULT_DIR"] = "/tmp/Vault"
            config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
            config["substack"]["publication"] = "https://file.substack.com"
            out = airdate_config.apply_env_overrides(config)
        self.assertEqual(out["substack"]["publication"], "https://env.substack.com")
        self.assertEqual(out["vault"]["essays_folder"], "Writing/Essays")

    def test_form_keeps_totem_keys_fixed(self):
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        out = airdate_config.settings_from_form(config, {
            "totems": [{"key": "circle", "label": "Politics", "color": "#112233", "image": "/Essays/_assets/p.png"},
                       {"key": "not-a-slot", "label": "ignored"}],
            "totems_enabled": False,
            "publication": "me.substack.com/",
            "vault_path": "/tmp/Some Vault",
        })
        first = out["totems"]["items"][0]
        self.assertEqual((first["key"], first["label"], first["color"], first["image"]),
                         ("circle", "Politics", "#112233", "Essays/_assets/p.png"))
        self.assertEqual([t["key"] for t in out["totems"]["items"]], ["circle", "triangle", "square", "diamond", "star"])
        self.assertFalse(out["totems"]["enabled"])
        self.assertEqual(out["substack"]["publication"], "https://me.substack.com")
        self.assertEqual(out["vault"]["name"], "Some Vault")

    def test_check_vault(self):
        vault = make_vault(self.data)
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        config["vault"]["path"] = str(vault)
        self.assertTrue(airdate_config.check_vault(config)["essays_ok"])
        config["vault"]["path"] = str(self.data)
        self.assertIn("not an Obsidian vault", airdate_config.check_vault(config)["vault_message"])

    def test_form_sets_category_mode(self):
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        out = airdate_config.settings_from_form(config, {"category_mode": "off"})
        self.assertEqual(out["categories"]["mode"], "off")
        self.assertEqual(airdate_config.validate_config(out), [])
        bad = airdate_config.settings_from_form(config, {"category_mode": "sometimes"})
        self.assertTrue(any("categories.mode" in e for e in airdate_config.validate_config(bad)))

    def test_form_saves_tag_presets(self):
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        out = airdate_config.settings_from_form(config, {"tag_presets": [
            {"name": " Politics ", "color": "#112233", "tags": " power, elections ,, "},
            {"name": "", "color": "#8092b0", "tags": []},
            {"name": "Craft", "color": "", "tags": ["writing"]},
            "not a preset",
        ]})
        self.assertEqual(out["tag_presets"], [
            {"name": "Politics", "color": "#112233", "tags": ["power", "elections"]},
            {"name": "Craft", "color": "", "tags": ["writing"]},
        ])
        self.assertEqual(airdate_config.validate_config(out), [])
        # A form without the field leaves saved presets alone; an empty list clears them.
        self.assertEqual(airdate_config.settings_from_form(out, {"publish_day": "monday"})["tag_presets"], out["tag_presets"])
        self.assertEqual(airdate_config.settings_from_form(out, {"tag_presets": []})["tag_presets"], [])

    def test_tag_preset_problems_are_reported(self):
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        cases = {
            "used twice": [{"name": "Craft", "tags": []}, {"name": "craft", "tags": []}],
            "up to five": [{"name": "Big", "tags": "a,b,c,d,e,f"}],
            "name is required": [{"name": "", "tags": "orphan"}],
            "#a1b2c3": [{"name": "Hue", "color": "blue", "tags": []}],
        }
        for expected, presets in cases.items():
            errors = airdate_config.validate_config(airdate_config.settings_from_form(config, {"tag_presets": presets}))
            self.assertTrue(any(expected in e for e in errors), f"{expected}: {errors}")

    def test_wizard_output_is_a_valid_config(self):
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        out = airdate_config.settings_from_form(config, {
            "vault_path": "/tmp/Some Vault", "essays_folder": ".", "category_mode": "off",
            "totems_enabled": False, "publish_day": "monday", "publication": "",
        })
        self.assertEqual(airdate_config.validate_config(out), [])
        self.assertEqual((out["categories"]["mode"], out["totems"]["enabled"], out["calendar"]["publish_day"]),
                         ("off", False, "monday"))
        self.assertIsNone(airdate_config.DEFAULT_CONFIG["calendar"]["publish_day"])


class ServerSettingsTests(unittest.TestCase):
    """Drive server.apply_config directly; restores the module afterwards."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.vault = make_vault(cls.root, "Writing/Essays")
        (cls.vault / "Writing" / "Essays" / "Politics").mkdir()
        (cls.vault / "Writing" / "Essays" / "_assets").mkdir()
        (cls.vault / "Writing" / "Essays" / "Published").mkdir()
        cls.env = CleanEnv().__enter__()
        os.environ["AIR_DATE_DATA_DIR"] = str(cls.root / "runtime")
        import server  # noqa: PLC0415
        cls.server = server

    @classmethod
    def tearDownClass(cls):
        cls.env.__exit__(None, None, None)
        cls.server.load_and_apply_config()
        cls.tmp.cleanup()

    def configure(self, **changes):
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        config["vault"]["path"] = str(self.vault)
        config["vault"]["essays_folder"] = "Writing/Essays"
        for dotted, value in changes.items():
            target = config
            *parents, leaf = dotted.split("__")
            for part in parents:
                target = target[part]
            target[leaf] = value
        self.server.apply_config(config, True)
        return self.server

    def fresh_install(self):
        """A data dir with no config.json, as the wizard finds it."""
        data = Path(tempfile.mkdtemp(dir=self.root))
        patcher = mock.patch.object(self.server, "DATA_DIR", data)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server.load_and_apply_config()
        return data

    def test_fresh_install_gets_the_wizard(self):
        self.fresh_install()
        self.assertTrue(self.server.setup_payload()["wizard"])
        self.configure()
        self.assertFalse(self.server.setup_payload()["wizard"])
        # A config.json that exists but needs fixing opens settings, not the wizard.
        self.server.apply_config(copy.deepcopy(airdate_config.DEFAULT_CONFIG), True)
        self.assertTrue(self.server.SETUP_REQUIRED)
        self.assertFalse(self.server.setup_payload()["wizard"])

    def test_check_settings_saves_nothing(self):
        data = self.fresh_install()
        with mock.patch.object(self.server, "EXPOSE_LOCAL_PATHS", True):
            good = self.server.check_settings({"vault_path": str(self.vault), "essays_folder": "Writing/Essays"})
            missing = self.server.check_settings({"vault_path": str(self.vault), "essays_folder": "Nope"})
            not_vault = self.server.check_settings({"vault_path": str(self.root), "essays_folder": "Essays"})
            bad_day = self.server.check_settings({"vault_path": str(self.vault), "publish_day": "someday"})
        self.assertTrue(good["vault_ok"] and good["essays_ok"] and not good["errors"])
        self.assertEqual(good["vault_name"], "My Vault")
        self.assertTrue(good["connector_folder"].endswith(".obsidian/plugins/airdate-connector"))
        self.assertTrue(missing["vault_ok"] and not missing["essays_ok"])
        self.assertIn("not an Obsidian vault", not_vault["vault_message"])
        self.assertEqual(not_vault["connector_folder"], "")
        self.assertTrue(bad_day["errors"])
        self.assertFalse((data / "config.json").exists())
        self.assertTrue(self.server.SETUP_REQUIRED)

    def test_check_settings_hides_paths_when_they_are_not_exposed(self):
        self.fresh_install()
        with mock.patch.object(self.server, "EXPOSE_LOCAL_PATHS", False):
            result = self.server.check_settings({"vault_path": str(self.vault), "essays_folder": "Writing/Essays"})
        self.assertFalse(result["vault_ok"])
        self.assertEqual(result["connector_folder"], "")

    def test_wizard_creates_the_essays_folder_before_saving(self):
        data = self.fresh_install()
        with mock.patch.object(self.server, "EXPOSE_LOCAL_PATHS", True):
            self.server.create_essays_folder({"vault_path": str(self.vault), "essays_folder": "Fresh/Essays"})
            self.assertTrue((self.vault / "Fresh" / "Essays").is_dir())
            with self.assertRaises(ValueError):
                self.server.create_essays_folder({"vault_path": str(self.root), "essays_folder": "Essays"})
            with self.assertRaises(ValueError):
                self.server.create_essays_folder({"vault_path": str(self.vault), "essays_folder": "../Outside"})
        self.assertFalse((self.root / "Essays").exists())
        self.assertFalse((self.root / "Outside").exists())
        self.assertFalse((data / "config.json").exists())

    def totem_note(self, name, frontmatter):
        server = self.configure()
        note = self.vault / "Writing" / "Essays" / "Politics" / name
        note.write_text(f"---\ntitle: {name}\n{frontmatter}---\nbody\n", encoding="utf-8")
        self.addCleanup(note.unlink)
        essay_id = server.essay_id_for(f"Politics/{name}")
        return server, note, essay_id

    @staticmethod
    def note_totem(server, note):
        return server.split_frontmatter(note.read_text(encoding="utf-8"))[0].get("totem")

    def test_totem_pick_writes_through_the_save_path(self):
        server, note, essay_id = self.totem_note("Pick.md", "")
        state = server.file_state(note)
        server.save_essay_updates(essay_id, {"totem": "Triangle"}, None, state["mtime"], state["content_hash"])
        self.assertEqual(self.note_totem(server, note), "triangle")
        essays, _ = server.discover_essays()
        self.assertEqual(next(e for e in essays if e["relative_path"] == "Politics/Pick.md")["totem_raw"], "triangle")
        # A stale file state refuses, as it does for the editor.
        with self.assertRaises(server.EssayConflictError):
            server.save_essay_updates(essay_id, {"totem": "star"}, None, state["mtime"], state["content_hash"])
        self.assertEqual(self.note_totem(server, note), "triangle")

    def test_unknown_totem_is_shown_and_kept_until_the_writer_picks(self):
        server, note, essay_id = self.totem_note("Foreign.md", "totem: elephant\n")
        essays, _ = server.discover_essays()
        card = next(e for e in essays if e["relative_path"] == "Politics/Foreign.md")
        self.assertEqual(card["totem_raw"], "elephant")
        # A save that does not pick one of the five leaves the value alone.
        server.save_essay_updates(essay_id, {"totem": "giraffe", "subtitle": "s"})
        server.save_essay_updates(essay_id, {"totem": ""})
        self.assertEqual(self.note_totem(server, note), "elephant")
        server.save_essay_updates(essay_id, {"totem": "none"})
        self.assertIsNone(self.note_totem(server, note))

    def test_totem_pick_is_ignored_when_totems_are_off(self):
        server, note, essay_id = self.totem_note("Off.md", "totem: circle\n")
        self.configure(totems__enabled=False)
        server.save_essay_updates(essay_id, {"totem": "none"})
        server.save_essay_updates(essay_id, {"totem": "star"})
        self.assertEqual(self.note_totem(server, note), "circle")

    def test_configured_vault_is_ready(self):
        server = self.configure()
        self.assertFalse(server.SETUP_REQUIRED)
        self.assertEqual(server.OBSIDIAN_VAULT_NAME, "My Vault")

    def test_obsidian_link_uses_the_real_essays_folder(self):
        server = self.configure()
        url = server.obsidian_url_for("Politics/Note.md")
        self.assertIn("vault=My%20Vault", url)
        self.assertIn("file=Writing%2FEssays%2FPolitics%2FNote.md", url)

    def test_hidden_rules_are_opt_in(self):
        server = self.configure()
        self.assertEqual(server.classify_essay("My essay on hubs.md", "My essay on hubs", {}), "active")
        self.assertEqual(server.classify_essay("!Index.md", "Index", {}), "active")
        server = self.configure(vault__hidden={"filename_prefixes": ["!"], "title_contains": ["moc"],
                                               "toplevel_title_contains": ["essay"]})
        self.assertEqual(server.classify_essay("!Index.md", "Index", {}), "hidden")
        self.assertEqual(server.classify_essay("Topics MOC.md", "Topics MOC", {}), "hidden")
        self.assertEqual(server.classify_essay("My essay.md", "My essay", {}), "hidden")
        self.assertEqual(server.classify_essay("Politics/My essay.md", "My essay", {}), "active")
        self.assertEqual(server.classify_essay("Politics/Note.md", "Note", {"type": "research"}), "hidden")

    def test_totems_off_preserve_frontmatter(self):
        server = self.configure(totems__enabled=False)
        self.assertEqual(server.ensure_totem("circle"), "")
        self.assertEqual(server.infer_totem("circle", "a.md", "t", [], ""), "")
        self.assertNotIn("totem", server.sanitize_updates({"totem": "circle", "title": "x"}))
        self.assertEqual(server.ui_config_payload()["totems"]["items"], [])

    def test_placeholder_totems_are_explicit_only(self):
        server = self.configure()
        self.assertEqual(server.infer_totem("", "a.md", "Grief", ["love"], "body"), "")
        self.assertEqual(server.infer_totem("Star", "a.md", "t", [], ""), "star")
        self.assertEqual(server.sanitize_updates({"totem": "star"})["totem"], "star")
        self.assertNotIn("totem", server.sanitize_updates({"totem": "fox"}))
        icons = [t["image"] for t in server.ui_config_payload()["totems"]["items"]]
        self.assertEqual(icons[0], "/static/totems/placeholder-1.svg")

    def test_custom_totems_infer_from_keywords_and_default(self):
        items = [
            {"key": "fire", "label": "Fire", "color": "#cc0000", "image": "Writing/Essays/_assets/fire.png", "keywords": {"debate": 3}},
            {"key": "water", "label": "Water", "color": "#0000cc", "image": "", "keywords": {"love": 3}},
        ]
        server = self.configure(totems__items=items, totems__default="water")
        self.assertEqual(server.infer_totem("", "a.md", "A debate", [], ""), "fire")
        self.assertEqual(server.infer_totem("", "a.md", "Nothing here", [], ""), "water")
        self.assertEqual(server.ui_config_payload()["totems"]["items"][0]["image"], "/vault-asset/Writing/Essays/_assets/fire.png")

    def test_categories_from_folders_and_config(self):
        server = self.configure(categories__items=[{"name": "Craft", "keywords": {"writing": 3}}])
        self.assertEqual(server.category_folders(), ["Craft", "Politics"])
        self.assertEqual(server.suggest_category("a.md", "On writing", [], "")[0], "Craft")
        server = self.configure(categories__mode="off")
        self.assertEqual(server.category_folders(), [])

    def test_thumbnail_prompt_uses_the_writers_publication(self):
        server = self.configure(substack__publication_name="The Weekly Thing")
        prompt = server.build_thumbnail_prompt({"title": "T", "summary": "S"}, {"title": "T", "totem": "circle"})
        self.assertIn("thumbnail for The Weekly Thing.", prompt)
        self.assertIn("Totem lens: Circle.", prompt)

    def test_vault_asset_stays_inside_the_vault(self):
        image = self.vault / "Writing" / "Essays" / "_assets" / "icon.png"
        image.write_bytes(b"\x89PNG")
        (self.vault / ".obsidian" / "secret.png").write_bytes(b"x")
        server = self.configure()
        self.assertEqual(server.resolve_vault_asset("Writing/Essays/_assets/icon.png"), image.resolve())
        self.assertIsNone(server.resolve_vault_asset("../runtime/config.json"))
        self.assertIsNone(server.resolve_vault_asset(".obsidian/secret.png"))
        self.assertIsNone(server.resolve_vault_asset("Writing/Essays/Politics"))

    def test_vault_root_as_essays_folder(self):
        root_vault = self.root / "Root Vault"
        (root_vault / ".obsidian").mkdir(parents=True)
        (root_vault / ".trash").mkdir()
        (root_vault / "Take Back the Pen.md").write_text("---\ntitle: T\n---\nx\n", encoding="utf-8")
        (root_vault / ".trash" / "Deleted.md").write_text("gone\n", encoding="utf-8")
        (root_vault / ".obsidian" / "Stray.md").write_text("config\n", encoding="utf-8")
        form = airdate_config.settings_from_form(copy.deepcopy(airdate_config.DEFAULT_CONFIG),
                                                 {"vault_path": str(root_vault), "essays_folder": "."})
        self.assertEqual(form["vault"]["essays_folder"], ".")
        self.assertEqual(airdate_config.validate_config(form), [])
        self.server.apply_config(form, True)
        self.assertFalse(self.server.SETUP_REQUIRED)
        essays, _ = self.server.discover_essays()
        self.assertEqual([e["relative_path"] for e in essays], ["Take Back the Pen.md"])
        self.assertIn("file=Take%20Back%20the%20Pen.md", self.server.obsidian_url_for("Take Back the Pen.md"))
        self.assertIn("vault=Root%20Vault", self.server.obsidian_url_for("Take Back the Pen.md"))

    def test_missing_vault_requires_setup(self):
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        self.server.apply_config(config, False)
        self.assertTrue(self.server.SETUP_REQUIRED)


if __name__ == "__main__":
    unittest.main()
