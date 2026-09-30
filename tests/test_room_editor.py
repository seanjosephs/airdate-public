"""Slice 4a server work: the essay editor page and saving it.

What the server owns for the editor:

- the board note an essay goes up as (`note_pad`, `note_color`) is an editor
  field, and a value that is not a pad or a colour is refused, never stored;
- the editor's own settings (`editor.mode`, `editor.sections`,
  `editor.script_height`) reach the browser, are validated, and can be
  changed on their own through POST /api/settings/editor without the whole
  settings form;
- a save that sends only what changed leaves every other byte of the note
  alone, `source_note` included;
- a note that changed in Obsidian after the editor opened it is a 409, and
  the first save of a note mints its uid and reports the id change.
"""

import copy
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import airdate_config  # noqa: E402
from test_room_foundation import point_server_at  # noqa: E402


# A note written the way a writer's notes look: a comment, mixed quoting, a
# block list, and a source_note the editor no longer shows.
NOTE = """---
title: "Borrowed Light"
subtitle: 'the first draft'
summary: A line about the essay.
# a comment the writer left
status: Writers Likey
tags:
  - craft
  - light
source_note: "[[Notebook 12]] - the conversation on the porch"
audience: everyone
note_pad: sticky
---
The first line of the page.

The second paragraph.
"""


class EditorCase(unittest.TestCase):
    """A fresh vault and fresh sidecars for every test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "vault").mkdir()
        (root / "runtime").mkdir()
        os.environ["OBSIDIAN_ESSAYS_DIR"] = str(root / "vault")
        os.environ["AIR_DATE_DATA_DIR"] = str(root / "runtime")
        import server  # noqa: PLC0415
        self.server = server
        self.root = root
        self.runtime = root / "runtime"
        self.vault = point_server_at(server, root)
        self._patches = [
            mock.patch.object(server, "ARRIVALS_LOG", self.runtime / "arrivals.json"),
            mock.patch.object(server, "STARRED_LOG", self.runtime / "starred.json"),
            mock.patch.object(server, "ESSAY_INDEX_FILE", self.runtime / "essay_index.json"),
        ]
        for patch in self._patches:
            patch.start()
        server._ARRIVALS_CACHE = None
        server._STARRED_CACHE = None
        server._essay_cache = None

    def tearDown(self):
        for patch in self._patches:
            patch.stop()
        self.server._ARRIVALS_CACHE = None
        self.server._STARRED_CACHE = None
        self.server._essay_cache = None
        self.server.load_and_apply_config()
        self.tmp.cleanup()

    def write_note(self, name, text):
        (self.vault / name).write_text(text, encoding="utf-8")
        essays, _ = self.server.refresh_essay_index()
        return next(r for r in essays if r["relative_path"] == name)["id"]

    def read(self, name):
        return (self.vault / name).read_text(encoding="utf-8")

    def frontmatter(self, name):
        return self.server.split_frontmatter(self.read(name))[0]


class NotePadFieldTests(EditorCase):
    """The board note is chosen in the editor's right rail and saved with the
    note. Only the three pads and five colours the board can draw."""

    def test_pad_and_colour_are_editor_fields(self):
        self.assertIn("note_pad", self.server.EDITOR_ALLOWED_FIELDS)
        self.assertIn("note_color", self.server.EDITOR_ALLOWED_FIELDS)

    def test_a_pad_and_colour_are_saved(self):
        essay_id = self.write_note("Pad.md", NOTE)
        self.server.save_essay_updates(essay_id, {"note_pad": "index", "note_color": "green"})
        fm = self.frontmatter("Pad.md")
        self.assertEqual(fm["note_pad"], "index")
        self.assertEqual(fm["note_color"], "green")

    def test_every_pad_and_every_colour_is_accepted(self):
        essay_id = self.write_note("Every.md", NOTE)
        for pad in ("sticky", "paper", "index"):
            for color in ("canary", "blue", "orange", "pink", "green"):
                with self.subTest(pad=pad, color=color):
                    result = self.server.save_essay_updates(essay_id, {"note_pad": pad, "note_color": color})
                    essay_id = result["new_id"]
                    fm = self.frontmatter("Every.md")
                    self.assertEqual((fm["note_pad"], fm["note_color"]), (pad, color))

    def test_case_and_space_are_forgiven(self):
        essay_id = self.write_note("Case.md", NOTE)
        self.server.save_essay_updates(essay_id, {"note_pad": " Paper ", "note_color": "PINK"})
        fm = self.frontmatter("Case.md")
        self.assertEqual((fm["note_pad"], fm["note_color"]), ("paper", "pink"))

    def test_a_pad_that_is_not_a_pad_is_refused_and_nothing_is_written(self):
        essay_id = self.write_note("Napkin.md", NOTE)
        before = self.read("Napkin.md")
        with self.assertRaises(ValueError) as caught:
            self.server.save_essay_updates(essay_id, {"note_pad": "napkin", "subtitle": "changed"})
        self.assertIn("sticky, paper or index card", str(caught.exception))
        self.assertEqual(self.read("Napkin.md"), before)

    def test_a_colour_that_is_not_on_the_deck_is_refused(self):
        essay_id = self.write_note("Plaid.md", NOTE)
        before = self.read("Plaid.md")
        with self.assertRaises(ValueError) as caught:
            self.server.save_essay_updates(essay_id, {"note_color": "plaid"})
        self.assertIn("canary, blue, orange, pink or green", str(caught.exception))
        self.assertEqual(self.read("Plaid.md"), before)

    def test_a_non_string_pad_is_refused(self):
        essay_id = self.write_note("Number.md", NOTE)
        with self.assertRaises(ValueError):
            self.server.save_essay_updates(essay_id, {"note_pad": 3})

    def test_a_blank_pad_goes_back_to_the_default(self):
        essay_id = self.write_note("Blank.md", NOTE)
        self.server.save_essay_updates(essay_id, {"note_pad": ""})
        self.assertNotIn("note_pad", self.frontmatter("Blank.md"))

    def test_the_index_row_carries_the_saved_pad(self):
        essay_id = self.write_note("Row.md", NOTE)
        result = self.server.save_essay_updates(essay_id, {"note_pad": "paper", "note_color": "blue"})
        essays, _ = self.server.refresh_essay_index()
        row = next(r for r in essays if r["id"] == result["new_id"])
        self.assertEqual((row["note_pad"], row["note_color"]), ("paper", "blue"))


class EditorConfigTests(unittest.TestCase):
    """editor.mode, editor.sections and editor.script_height."""

    def valid(self, **editor):
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        config["vault"]["path"] = "/somewhere"
        config["editor"].update(editor)
        return config

    def errors(self, **editor):
        return [e for e in airdate_config.validate_config(self.valid(**editor)) if e.startswith("editor.")]

    def test_the_default_editor_is_valid(self):
        self.assertEqual(self.errors(), [])
        self.assertIn("script_height", airdate_config.DEFAULT_CONFIG["editor"])
        self.assertIsNone(airdate_config.DEFAULT_CONFIG["editor"]["script_height"])

    def test_the_three_modes_are_valid(self):
        for mode in ("simplified", "complete", "custom"):
            with self.subTest(mode=mode):
                self.assertEqual(self.errors(mode=mode), [])

    def test_another_mode_is_an_error(self):
        self.assertEqual(len(self.errors(mode="fancy")), 1)
        self.assertEqual(len(self.errors(mode=None)), 1)

    def test_sections_must_be_switches(self):
        self.assertEqual(self.errors(sections={"seo_social": True, "notes": False}), [])
        self.assertEqual(len(self.errors(sections=["seo_social"])), 1)
        self.assertEqual(len(self.errors(sections={"seo_social": "yes"})), 1)
        self.assertEqual(len(self.errors(sections={"seo_social": 1})), 1)

    def test_the_script_height_is_a_whole_number_in_range(self):
        for good in (None, 240, 560, 2400):
            with self.subTest(good=good):
                self.assertEqual(self.errors(script_height=good), [])
        for bad in (239, 2401, 0, -5, 600.5, "600", True):
            with self.subTest(bad=bad):
                self.assertEqual(len(self.errors(script_height=bad)), 1)

    def test_an_old_config_gains_the_script_height(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = airdate_config.config_path(Path(tmp))
            path.write_text(json.dumps({"editor": {"mode": "complete"}}), encoding="utf-8")
            config, _, _ = airdate_config.load_config(Path(tmp))
        self.assertEqual(config["editor"]["mode"], "complete")
        self.assertIn("script_height", config["editor"])

    def test_the_example_config_carries_the_script_height(self):
        example = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertIn("script_height", example["editor"])


class EditorSettingsRouteTests(EditorCase):
    """POST /api/settings/editor changes the editor's settings and nothing else."""

    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for name in ("OBSIDIAN_ESSAYS_DIR", "AIRDATE_VAULT_DIR"):
            os.environ.pop(name, None)
        self.data = patch_dir = self.root / "data"
        patch_dir.mkdir()
        self.data_patch = mock.patch.object(self.server, "DATA_DIR", patch_dir)
        self.data_patch.start()
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        config["vault"]["path"] = str(self.root / "vault")
        config["vault"]["essays_folder"] = "Essays"
        config["board"]["weeks_shown"] = 5
        config["substack"]["publication"] = "https://example.substack.com"
        airdate_config.save_config(patch_dir, config)
        self.server.load_and_apply_config()
        self.assertFalse(self.server.SETUP_REQUIRED, self.server.CONFIG_ERRORS)

    def tearDown(self):
        self.data_patch.stop()
        self.env.stop()
        super().tearDown()

    def on_disk(self):
        return json.loads(airdate_config.config_path(self.data).read_text(encoding="utf-8"))

    def test_the_browser_gets_the_editor_settings(self):
        editor = self.server.ui_config_payload()["editor"]
        self.assertEqual(editor, {"mode": "simplified", "sections": {}, "script_height": None})

    def test_the_script_height_is_remembered(self):
        before = self.on_disk()
        result = self.server.save_editor_settings({"script_height": 720})
        self.assertTrue(result["ok"], result)
        after = self.on_disk()
        self.assertEqual(after["editor"]["script_height"], 720)
        # Everything else in the file is as it was.
        before["editor"]["script_height"] = 720
        self.assertEqual(after, before)
        # And the running app took it.
        self.assertEqual(self.server.ui_config_payload()["editor"]["script_height"], 720)
        self.assertEqual(result["editor"]["script_height"], 720)

    def test_mode_and_sections_change_together(self):
        result = self.server.save_editor_settings({"mode": "custom", "sections": {"seo_social": True, "advanced": False}})
        self.assertTrue(result["ok"], result)
        editor = self.on_disk()["editor"]
        self.assertEqual(editor["mode"], "custom")
        self.assertEqual(editor["sections"], {"seo_social": True, "advanced": False})

    def test_a_partial_change_keeps_the_other_editor_keys(self):
        self.server.save_editor_settings({"mode": "complete"})
        self.server.save_editor_settings({"script_height": 400})
        editor = self.on_disk()["editor"]
        self.assertEqual((editor["mode"], editor["script_height"]), ("complete", 400))

    def test_bad_values_are_refused_and_nothing_is_written(self):
        before = self.on_disk()
        for bad in ({"script_height": 10}, {"mode": "fancy"}, {"sections": {"seo_social": "on"}},
                    {"sections": {"a_section_that_does_not_exist": True}}, {"vault": {"path": "/elsewhere"}},
                    {}):
            with self.subTest(bad=bad):
                result = self.server.save_editor_settings(bad)
                self.assertFalse(result["ok"], result)
                self.assertTrue(result["errors"])
                self.assertEqual(self.on_disk(), before)

    def test_an_unreadable_config_is_not_overwritten(self):
        path = airdate_config.config_path(self.data)
        path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.server.save_editor_settings({"script_height": 600})
        self.assertEqual(path.read_text(encoding="utf-8"), "{ not json")


class HttpCase(EditorCase):
    def setUp(self):
        super().setUp()

        class QuietHandler(self.server.Handler):
            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, path, payload=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read())


class EditorSettingsHttpTests(HttpCase):
    def test_the_route_saves_the_height(self):
        with tempfile.TemporaryDirectory() as data:
            with mock.patch.object(self.server, "DATA_DIR", Path(data)):
                status, body = self.call("/api/settings/editor", {"script_height": 800})
                self.assertEqual(status, 200, body)
                self.assertEqual(body["editor"]["script_height"], 800)
                saved = json.loads(airdate_config.config_path(Path(data)).read_text(encoding="utf-8"))
                self.assertEqual(saved["editor"]["script_height"], 800)

    def test_a_bad_height_answers_400(self):
        with tempfile.TemporaryDirectory() as data:
            with mock.patch.object(self.server, "DATA_DIR", Path(data)):
                status, body = self.call("/api/settings/editor", {"script_height": 99999})
                self.assertEqual(status, 400, body)
                self.assertFalse(airdate_config.config_path(Path(data)).exists())

    def test_setup_mode_refuses_rather_than_writing_a_config(self):
        with tempfile.TemporaryDirectory() as data:
            with mock.patch.object(self.server, "DATA_DIR", Path(data)), \
                    mock.patch.object(self.server, "SETUP_REQUIRED", True):
                status, body = self.call("/api/settings/editor", {"script_height": 800})
                self.assertEqual(status, 409, body)
                self.assertEqual(body["error_kind"], "setup_required")
                self.assertFalse(airdate_config.config_path(Path(data)).exists())


class ChangedFieldsOnlyTests(EditorCase):
    """The editor sends only what the writer changed, and a blank value
    deletes a key - so a save must leave every other byte alone."""

    def test_one_changed_field_changes_one_line(self):
        essay_id = self.write_note("One.md", NOTE)
        before = self.read("One.md")
        detail = self.server.get_essay_detail(essay_id)
        self.server.save_essay_updates(
            essay_id, {"subtitle": "the second draft"}, None, None, detail["content_hash"],
        )
        after = self.read("One.md").splitlines()
        # The note gains its uid on its first save, as every note does. Set
        # that line aside and exactly one line differs.
        uid_lines = [line for line in after if line.startswith("airdate_uid:")]
        self.assertEqual(len(uid_lines), 1)
        after = [line for line in after if not line.startswith("airdate_uid:")]
        before = before.splitlines()
        self.assertEqual(len(after), len(before))
        changed = [(a, b) for a, b in zip(before, after) if a != b]
        self.assertEqual(len(changed), 1, changed)
        self.assertEqual(changed[0][0], "subtitle: 'the first draft'")
        self.assertIn("the second draft", changed[0][1])

    def test_source_note_survives_a_save_byte_for_byte(self):
        essay_id = self.write_note("Source.md", NOTE)
        source_line = 'source_note: "[[Notebook 12]] - the conversation on the porch"'
        self.server.save_essay_updates(essay_id, {"summary": "a new line"})
        self.assertIn(source_line, self.read("Source.md").splitlines())

    def test_the_body_is_untouched_when_it_is_not_sent(self):
        essay_id = self.write_note("Body.md", NOTE)
        body_before = self.server.split_frontmatter(self.read("Body.md"))[1]
        self.server.save_essay_updates(essay_id, {"note_color": "pink"})
        self.assertEqual(self.server.split_frontmatter(self.read("Body.md"))[1], body_before)

    def test_sending_a_value_equal_to_the_disk_changes_nothing(self):
        essay_id = self.write_note("Same.md", NOTE)
        first = self.server.save_essay_updates(essay_id, {"audience": "everyone"})
        before = self.read("Same.md")
        second = self.server.save_essay_updates(first["new_id"], {"audience": "everyone", "title": "Borrowed Light"})
        self.assertEqual(second["changes"], {})
        self.assertEqual(self.read("Same.md"), before)

    def test_the_detail_carries_a_hero_url(self):
        assets = self.vault / "_assets" / "substack"
        assets.mkdir(parents=True)
        (assets / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        text = NOTE.replace("audience: everyone", "audience: everyone\nhero_image: _assets/substack/hero.png")
        essay_id = self.write_note("Hero.md", text)
        detail = self.server.get_essay_detail(essay_id)
        prefix = self.vault.resolve().relative_to(self.server.VAULT_DIR.resolve()).as_posix()
        expected = "_assets/substack/hero.png" if prefix == "." else f"{prefix}/_assets/substack/hero.png"
        self.assertEqual(detail["hero_url"], f"/vault-asset/{expected}")

    def test_a_hero_path_is_read_under_the_essays_folder_first(self):
        # attach-hero writes the path relative to the essays folder, which in
        # a real vault sits one level down from the vault root.
        essays = self.server.VAULT_DIR / "Writing"
        (essays / "_assets" / "substack").mkdir(parents=True)
        (essays / "_assets" / "substack" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        with mock.patch.object(self.server, "OBSIDIAN_ESSAYS_DIR", essays):
            url = self.server.hero_asset_url("_assets/substack/hero.png")
        self.assertEqual(url, "/vault-asset/Writing/_assets/substack/hero.png")

    def test_a_hero_that_is_not_there_has_no_url(self):
        text = NOTE.replace("audience: everyone", "audience: everyone\nhero_image: _assets/substack/gone.png")
        essay_id = self.write_note("Gone.md", text)
        self.assertEqual(self.server.get_essay_detail(essay_id)["hero_url"], "")


class ConflictAndRemapTests(HttpCase):
    """The save path's two id and file-state rules, on the wire."""

    def test_a_note_changed_in_obsidian_answers_409_and_keeps_the_obsidian_text(self):
        essay_id = self.write_note("Changed.md", NOTE)
        detail = self.server.get_essay_detail(essay_id)
        # The writer edits the note in Obsidian while the editor is open.
        edited = self.read("Changed.md").replace("The second paragraph.", "Written in Obsidian.")
        (self.vault / "Changed.md").write_text(edited, encoding="utf-8")
        status, body = self.call(f"/api/essays/{essay_id}/save", {
            "updates": {"subtitle": "from the editor"},
            "expected_mtime": detail["mtime"],
            "expected_content_hash": detail["content_hash"],
        })
        self.assertEqual(status, 409, body)
        self.assertEqual(body["reason"], "file_changed")
        self.assertNotIn("refused", body)
        self.assertIn("Written in Obsidian.", body["current_body"])
        self.assertEqual(self.read("Changed.md"), edited)

    def test_the_first_save_reports_the_uid_and_the_next_save_is_not_a_conflict(self):
        essay_id = self.write_note("Mint.md", NOTE)
        detail = self.server.get_essay_detail(essay_id)
        self.assertEqual(detail["uid"], "")
        status, first = self.call(f"/api/essays/{essay_id}/save", {
            "updates": {"subtitle": "one"},
            "expected_mtime": detail["mtime"],
            "expected_content_hash": detail["content_hash"],
        })
        self.assertEqual(status, 200, first)
        self.assertEqual(first["old_id"], essay_id)
        self.assertNotEqual(first["new_id"], essay_id)
        self.assertEqual(first["new_id"], self.frontmatter("Mint.md")["airdate_uid"])
        # The editor adopts the new id, mtime and hash, and saves again.
        status, second = self.call(f"/api/essays/{first['new_id']}/save", {
            "updates": {"subtitle": "two"},
            "expected_mtime": first["mtime"],
            "expected_content_hash": first["content_hash"],
        })
        self.assertEqual(status, 200, second)
        self.assertEqual(second["old_id"], second["new_id"])
        self.assertEqual(self.frontmatter("Mint.md")["subtitle"], "two")
        # The fresh detail answers to the new id.
        status, fresh = self.call(f"/api/essays/{first['new_id']}")
        self.assertEqual(status, 200, fresh)
        self.assertEqual(fresh["id"], first["new_id"])

    def test_the_editor_can_ask_for_the_fresh_row(self):
        essay_id = self.write_note("Row Wire.md", NOTE)
        status, body = self.call(f"/api/essays/{essay_id}/save", {
            "updates": {"title": "Borrowed Light, Again", "note_color": "blue"},
            "return_row": True,
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(body["row"]["id"], body["new_id"])
        self.assertEqual(body["row"]["title"], "Borrowed Light, Again")
        self.assertEqual(body["row"]["note_color"], "blue")

    def test_the_old_page_save_is_unchanged_and_carries_no_row(self):
        essay_id = self.write_note("No Row.md", NOTE)
        status, body = self.call(f"/api/essays/{essay_id}/save", {"updates": {"subtitle": "x"}})
        self.assertEqual(status, 200, body)
        self.assertNotIn("row", body)

    def test_the_detail_carries_the_row_for_the_stamp(self):
        essay_id = self.write_note("Detail Row.md", NOTE)
        status, body = self.call(f"/api/essays/{essay_id}")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["row"]["id"], essay_id)
        self.assertTrue(body["row"]["arrived_at"])

    def test_an_invalid_pad_answers_400_with_the_sentence(self):
        essay_id = self.write_note("Wire Pad.md", NOTE)
        status, body = self.call(f"/api/essays/{essay_id}/save", {"updates": {"note_pad": "napkin"}})
        self.assertEqual(status, 400, body)
        self.assertIn("sticky, paper or index card", body["error"])


if __name__ == "__main__":
    unittest.main()


class HeroUrlResolvesFromTheEssaysFolderTests(EditorCase):
    """The index row's hero_url and the editor's must agree, and both must work
    when the essays folder is not the vault root - which is the normal layout
    (a vault with an Essays folder inside it).

    attach-hero stores hero_image relative to the ESSAYS folder. The index row
    used to hand that string straight to /vault-asset/, which resolves against
    the VAULT root, so on a vault with an Essays folder every hero link would
    have been a 404."""

    def place_image(self, relative_to_essays):
        path = self.vault / relative_to_essays
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
        return path

    def row(self, name):
        essays, _ = self.server.refresh_essay_index()
        return next(r for r in essays if r["relative_path"] == name)

    def test_the_essays_folder_is_not_the_vault_root_here(self):
        # Guard the premise: if this ever stops holding, the test proves nothing.
        self.assertNotEqual(self.server.OBSIDIAN_ESSAYS_DIR.resolve(), self.server.VAULT_DIR.resolve())

    def test_an_attach_hero_path_resolves_on_the_index_row(self):
        self.place_image("_assets/substack/pic.webp")
        self.write_note("Hero.md", '---\ntitle: "Hero"\nhero_image: "_assets/substack/pic.webp"\n---\nbody\n')
        url = self.row("Hero.md")["hero_url"]
        self.assertTrue(url.startswith("/vault-asset/"), url)
        # The URL must name a file the vault-asset route can actually serve.
        rel = self.server.urllib.parse.unquote(url[len("/vault-asset/"):]) if hasattr(self.server, "urllib") else url[len("/vault-asset/"):]
        self.assertIsNotNone(self.server.resolve_vault_asset(rel))

    def test_a_hand_typed_vault_relative_path_also_resolves(self):
        self.place_image("_assets/substack/typed.webp")
        prefix = self.server.OBSIDIAN_ESSAYS_DIR.resolve().relative_to(self.server.VAULT_DIR.resolve()).as_posix()
        self.write_note("Typed.md", f'---\ntitle: "Typed"\nhero_image: "{prefix}/_assets/substack/typed.webp"\n---\nbody\n')
        self.assertTrue(self.row("Typed.md")["hero_url"].startswith("/vault-asset/"))

    def test_a_missing_file_gives_no_link_rather_than_a_broken_one(self):
        self.write_note("Gone.md", '---\ntitle: "Gone"\nhero_image: "_assets/substack/nope.webp"\n---\nbody\n')
        self.assertEqual(self.row("Gone.md")["hero_url"], "")

    def test_the_index_row_and_the_editor_agree(self):
        self.place_image("_assets/substack/same.webp")
        essay_id = self.write_note("Same.md", '---\ntitle: "Same"\nhero_image: "_assets/substack/same.webp"\n---\nbody\n')
        detail = self.server.get_essay_detail(essay_id)
        self.assertEqual(self.row("Same.md")["hero_url"], detail["hero_url"])
        self.assertTrue(detail["hero_url"])
