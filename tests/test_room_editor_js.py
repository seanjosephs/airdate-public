"""The essay editor's pure functions, run through node like the card helper.

These are the rules that keep a save from harming a note: only what changed
is sent, `status`, `scheduled_at` and `source_note` never are, the draft
details the note is missing are saved, and a section that is switched off
keeps its values. Every call runs in a fixed time zone because the status
line compares calendar days.
"""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "static" / "room" / "editor.js"
TZ = "America/Los_Angeles"
NODE = shutil.which("node")

if NODE is None:  # pragma: no cover - depends on the machine
    raise RuntimeError("node is required for tests/test_room_editor_js.py and was not found on PATH")


def run(expression: str):
    script = f"""
const E = require({json.dumps(str(HELPER))});
const result = (() => {{ {expression} }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
"""
    env = dict(os.environ, TZ=TZ)
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True, env=env)
    return json.loads(completed.stdout)


def js(value) -> str:
    return json.dumps(value)


# A note that already carries every draft detail, so opening it and saving
# with no edits sends nothing.
COMPLETE_FM = {
    "title": "Borrowed Light",
    "subtitle": "a subtitle",
    "summary": "A line about the essay.",
    "status": "Writers Likey",
    "publication": "https://example.substack.com",
    "audience": "everyone",
    "comment_permissions": "everyone",
    "email_subject": "Borrowed Light",
    "email_preview_text": "A line about the essay.",
    "hero_image": "_assets/substack/hero.png",
    "thumbnail_alt": "Borrowed Light",
    "seo_title": "Borrowed Light",
    "seo_description": "A line about the essay.",
    "social_title": "Borrowed Light",
    "social_description": "A line about the essay.",
    "thumbnail_prompt": "a lamp on a desk",
    "tags": ["craft", "light"],
    "source_note": "[[Notebook 12]]",
    "scheduled_at": "2026-10-05",
    "totem": "Fox",
}


def essay(frontmatter=None, **fields):
    base = {
        "id": "path-hash-1",
        "title": "Borrowed Light",
        "subtitle": "",
        "summary": "A line about the essay.",
        "status": "Writers Likey",
        "body": "The first line.\n\nThe second.",
        "frontmatter": dict(COMPLETE_FM if frontmatter is None else frontmatter),
        "metadata_defaults": {},
        "mtime": 100.5,
        "content_hash": "hash-1",
    }
    base.update(fields)
    return base


def open_(e) -> str:
    return f"const s = E.fromEssay({js(e)});"


class OpeningTests(unittest.TestCase):
    def test_a_complete_note_opens_clean(self):
        out = run(open_(essay()) + "return [E.isDirty(s), E.writerEdited(s), E.savePayload(s).updates];")
        self.assertEqual(out, [False, False, {}])

    def test_the_baseline_is_taken_before_the_suggestions(self):
        e = essay(frontmatter={"title": "Bare"}, metadata_defaults={
            "summary": "from the body", "seo_title": "Bare", "email_subject": "Bare",
            "tags": ["one"],
        })
        out = run(open_(e) + "return [s.baseline.summary, s.values.summary, s.values.seo_title, s.values.tags];")
        self.assertEqual(out, ["", "A line about the essay.", "Bare", ["one"]])

    def test_a_note_missing_details_is_dirty_but_the_writer_has_edited_nothing(self):
        e = essay(frontmatter={"title": "Bare"}, metadata_defaults={"seo_title": "Bare"})
        out = run(open_(e) + "return [E.isDirty(s), E.writerEdited(s), E.savePayload(s).updates];")
        dirty, edited, updates = out
        self.assertTrue(dirty)
        self.assertFalse(edited)
        # The suggestions and the form's own defaults are saved into the note,
        # as the old editor did.
        self.assertEqual(updates["seo_title"], "Bare")
        self.assertEqual(updates["summary"], "A line about the essay.")
        self.assertEqual(updates["audience"], "everyone")
        self.assertEqual(updates["comment_permissions"], "everyone")
        # A field the note already has is not sent again.
        self.assertNotIn("title", updates)

    def test_the_form_defaults_are_in_the_baseline(self):
        out = run(open_(essay(frontmatter={})) + "return [s.baseline.audience, s.baseline.publish_on_web, s.baseline.send_email, s.baseline.free_preview, s.baseline.post_type];")
        self.assertEqual(out, ["everyone", True, True, False, "text"])

    def test_values_from_the_note_are_normalised(self):
        fm = dict(COMPLETE_FM, tags="craft, light, craft", totem=" Fox ", publish_on_web="true")
        out = run(open_(essay(frontmatter=fm)) + "return [s.values.tags, s.values.totem, s.values.publish_on_web, E.isDirty(s)];")
        self.assertEqual(out, [["craft", "light"], "fox", True, False])


class SavePayloadTests(unittest.TestCase):
    def payload(self, change: str, e=None):
        return run(open_(e or essay()) + change + " return E.savePayload(s);")

    def test_one_change_sends_one_field(self):
        out = self.payload("s.values.subtitle = 'a new subtitle';")
        self.assertEqual(out["updates"], {"subtitle": "a new subtitle"})
        self.assertNotIn("body", out)

    def test_the_file_state_goes_with_every_save(self):
        out = self.payload("s.values.subtitle = 'x';")
        self.assertEqual((out["expected_mtime"], out["expected_content_hash"]), (100.5, "hash-1"))

    def test_status_schedule_and_source_note_are_never_sent(self):
        out = self.payload(
            "s.values.status = 'Live'; s.values.scheduled_at = '2026-10-12'; s.values.source_note = '';"
            " s.values.published_date = '2026-01-01'; s.values.substack_url = 'https://x';"
            " s.values.subtitle = 'changed';"
        )
        self.assertEqual(out["updates"], {"subtitle": "changed"})

    def test_source_note_is_never_sent_even_from_a_bare_note(self):
        out = self.payload("", essay(frontmatter={"source_note": "[[Notebook]]"}))
        self.assertNotIn("source_note", out["updates"])
        self.assertNotIn("status", out["updates"])
        self.assertNotIn("scheduled_at", out["updates"])

    def test_a_field_the_writer_cleared_is_sent_blank(self):
        out = self.payload("s.values.seo_description = '';")
        self.assertEqual(out["updates"], {"seo_description": ""})

    def test_a_field_the_writer_did_not_touch_is_never_sent_blank(self):
        # The note has no slug and no canonical url; the editor must not send
        # them, blank or otherwise.
        out = self.payload("s.values.subtitle = 'x';")
        for key in ("slug", "canonical_url", "notes", "section", "test_email_recipients"):
            self.assertNotIn(key, out["updates"])

    def test_a_change_in_a_switched_off_section_is_still_saved(self):
        # Sections only decide what is drawn. The state holds every field.
        out = run(
            open_(essay()) + " const v = E.sectionVisibility({mode: 'simplified'});"
            " s.values.seo_title = 'kept'; return [v.seo_social, E.savePayload(s).updates];"
        )
        self.assertEqual(out, [False, {"seo_title": "kept"}])

    def test_the_body_goes_only_when_it_changed(self):
        out = self.payload("s.body = s.body + '\\nA third.';")
        self.assertEqual(out["body"], "The first line.\n\nThe second.\nA third.")
        self.assertEqual(out["updates"], {})

    def test_taking_the_totem_off_sends_none(self):
        out = self.payload("s.values.totem = '';")
        self.assertEqual(out["updates"], {"totem": "none"})

    def test_a_note_pad_choice_is_sent(self):
        out = self.payload("s.values.note_pad = 'index'; s.values.note_color = 'green';")
        self.assertEqual(out["updates"], {"note_pad": "index", "note_color": "green"})

    def test_tags_are_sent_as_a_list(self):
        out = self.payload("s.values.tags = E.addTags(s.values.tags, 'new, craft');")
        self.assertEqual(out["updates"], {"tags": ["craft", "light", "new"]})

    def test_whitespace_alone_is_not_a_change(self):
        out = self.payload("s.values.title = '  Borrowed Light  ';")
        self.assertEqual(out["updates"], {})


class AfterSaveTests(unittest.TestCase):
    def test_a_save_adopts_the_new_id_file_state_and_baseline(self):
        out = run(
            open_(essay()) + " s.values.subtitle = 'sent';"
            " const sent = E.snapshot(s);"
            " s.values.subtitle = 'typed while saving';"
            " const next = E.adoptSave(s, sent, {old_id: 'path-hash-1', new_id: 'uid-9', mtime: 200, content_hash: 'hash-2',"
            "   saved_frontmatter: Object.assign({}, s.frontmatter, {subtitle: 'sent', airdate_uid: 'uid-9'})});"
            " return [next.id, next.fileState, next.baseline.subtitle, next.values.subtitle, E.savePayload(next).updates,"
            "   E.savePayload(next).expected_content_hash];"
        )
        self.assertEqual(out[0], "uid-9")
        self.assertEqual(out[1], {"mtime": 200, "content_hash": "hash-2"})
        self.assertEqual(out[2], "sent")
        # What was typed while the save was in flight is still unsaved.
        self.assertEqual(out[3], "typed while saving")
        self.assertEqual(out[4], {"subtitle": "typed while saving"})
        self.assertEqual(out[5], "hash-2")

    def test_after_a_save_the_details_are_on_the_note_and_nothing_is_dirty(self):
        e = essay(frontmatter={"title": "Bare"}, metadata_defaults={"seo_title": "Bare"})
        out = run(
            open_(e) + " const p = E.savePayload(s); const sent = E.snapshot(s);"
            " const next = E.adoptSave(s, sent, {new_id: 'uid-1', mtime: 2, content_hash: 'h2',"
            "   saved_frontmatter: Object.assign({}, s.frontmatter, p.updates)});"
            " return [E.isDirty(next), E.writerEdited(next)];"
        )
        self.assertEqual(out, [False, False])

    def test_a_hero_attach_is_already_on_disk(self):
        out = run(
            open_(essay()) + " s.values.subtitle = 'unsaved';"
            " const next = E.adoptHero(s, {updates: {hero_image: '_assets/substack/new.png', social_image: '_assets/substack/new.png'},"
            "   saved: {new_id: 'uid-2', mtime: 3, content_hash: 'h3', saved_frontmatter: Object.assign({}, s.frontmatter, {hero_image: '_assets/substack/new.png'})}});"
            " return [next.id, next.values.hero_image, next.fileState.content_hash, E.savePayload(next).updates];"
        )
        self.assertEqual(out[:3], ["uid-2", "_assets/substack/new.png", "h3"])
        self.assertEqual(out[3], {"subtitle": "unsaved"})

    def test_the_writer_editing_is_what_the_close_guard_asks_about(self):
        out = run(open_(essay()) + " const before = E.writerEdited(s); s.body += ' more'; return [before, E.writerEdited(s)];")
        self.assertEqual(out, [False, True])


class SectionTests(unittest.TestCase):
    def vis(self, cfg, **options):
        return run(f"return E.sectionVisibility({js(cfg)}, {js(options)});")

    def test_simplified_does_not_show_advanced(self):
        v = self.vis({"mode": "simplified", "sections": {"advanced": True}})
        self.assertEqual(v["mode"], "simplified")
        self.assertFalse(v["advanced"])
        for key in ("email_comments", "seo_social", "thumbnail", "notes"):
            self.assertFalse(v[key], key)
        self.assertTrue(v["totem"])
        self.assertTrue(v["on_the_board"])

    def test_complete_shows_every_section(self):
        v = self.vis({"mode": "complete"})
        for key in ("totem", "on_the_board", "advanced", "email_comments", "seo_social", "thumbnail", "notes"):
            self.assertTrue(v[key], key)

    def test_custom_takes_the_writers_switches_over_simplified(self):
        v = self.vis({"mode": "custom", "sections": {"seo_social": True, "on_the_board": False, "notes": "yes"}})
        self.assertEqual(v["mode"], "custom")
        self.assertTrue(v["seo_social"])
        self.assertFalse(v["on_the_board"])
        self.assertFalse(v["notes"], "only a real switch counts")
        self.assertFalse(v["advanced"])

    def test_the_default_is_simplified(self):
        self.assertEqual(self.vis(None)["mode"], "simplified")
        self.assertEqual(self.vis({"mode": "fancy"})["mode"], "simplified")

    def test_no_totems_means_no_totem_field(self):
        self.assertFalse(self.vis({"mode": "complete"}, totemsEnabled=False)["totem"])

    def test_the_line_says_what_is_switched_off(self):
        simple = run("return E.hiddenSentence(E.sectionVisibility({mode: 'simplified'}));")
        self.assertEqual(simple, "you chose simplified metadata. advanced, email and comments, seo and social, "
                                 "thumbnail and internal notes are switched off.")
        self.assertEqual(run("return E.hiddenSentence(E.sectionVisibility({mode: 'complete'}));"), "")
        one = run("return E.hiddenSentence(E.sectionVisibility({mode: 'custom', sections: "
                  "{advanced: true, email_comments: true, seo_social: true, thumbnail: true}}));")
        self.assertEqual(one, "you chose custom metadata. internal notes is switched off.")


class GearSheetTests(unittest.TestCase):
    """The gear sheet (slice 4c): two presets, per-section switches, and any
    flip reads custom. What it sends is what POST /api/settings/editor takes."""

    SWITCHES = ["totem", "on_the_board", "advanced", "email_comments", "seo_social", "thumbnail", "notes"]

    def test_a_preset_is_just_its_name(self):
        self.assertEqual(run("return E.presetSettings('complete');"), {"mode": "complete", "sections": {}})
        self.assertEqual(run("return E.presetSettings('simplified');"), {"mode": "simplified", "sections": {}})
        self.assertEqual(run("return E.presetSettings('custom');"), {"mode": "simplified", "sections": {}})

    def test_the_switches_follow_the_preset(self):
        simple = run("return E.gearState({mode: 'simplified'});")
        self.assertEqual(simple["mode"], "simplified")
        self.assertEqual([k for k in self.SWITCHES if simple["switches"][k]], ["totem", "on_the_board"])
        complete = run("return E.gearState({mode: 'complete'});")
        self.assertTrue(all(complete["switches"][k] for k in self.SWITCHES))

    def test_simplified_does_not_show_advanced_even_with_a_stale_switch(self):
        state = run("return E.gearState({mode: 'simplified', sections: {advanced: true}});")
        self.assertFalse(state["switches"]["advanced"])

    def test_flipping_a_switch_reads_custom_and_carries_every_switch(self):
        flipped = run("return E.flipSection({mode: 'simplified', sections: {}}, 'seo_social');")
        self.assertEqual(flipped["mode"], "custom")
        self.assertEqual(sorted(flipped["sections"]), sorted(self.SWITCHES))
        self.assertTrue(flipped["sections"]["seo_social"])
        self.assertFalse(flipped["sections"]["advanced"])
        # And the editor shows exactly that.
        shown = run("const next = E.flipSection({mode: 'complete'}, 'notes'); return E.sectionVisibility(next);")
        self.assertFalse(shown["notes"])
        self.assertTrue(shown["advanced"])

    def test_flipping_back_stays_custom(self):
        twice = run("const a = E.flipSection({mode: 'simplified'}, 'thumbnail'); return E.flipSection(a, 'thumbnail');")
        self.assertEqual(twice["mode"], "custom")
        self.assertFalse(twice["sections"]["thumbnail"])

    def test_scheduled_time_is_not_a_switch(self):
        self.assertIsNone(run("return E.flipSection({mode: 'simplified'}, 'scheduled_time');"))
        self.assertNotIn("scheduled_time", run("return E.gearState({mode: 'complete'});")["switches"])

    def test_the_totem_switch_needs_totems(self):
        self.assertFalse(run("return E.gearState({mode: 'complete'}, {totemsEnabled: false}).totemsEnabled;"))
        self.assertTrue(run("return E.gearState({mode: 'complete'}).totemsEnabled;"))


class EditorTourWordsTests(unittest.TestCase):
    """Step 1 of the editor tour names the phase this essay is actually in."""

    REST = "airdate stamps it when you star it, when you schedule it, and when it airs. you never set it by hand."

    def test_the_writers_room_reads_as_drawn(self):
        self.assertEqual(run("return E.stampTourText('Writers Room');"), f"writers room today. {self.REST}")

    def test_every_other_phase_says_where_it_stands(self):
        for status, name in (("Writers Likey", "writers likey"), ("Ready for Air", "ready for air"),
                             ("Live", "live"), ("Archived", "saved for a rainy day")):
            with self.subTest(status=status):
                self.assertEqual(run(f"return E.stampTourText({js(status)});"), f"{name} today. {self.REST}")

    def test_an_unknown_phase_drops_the_first_sentence(self):
        self.assertEqual(run("return E.stampTourText('');"), self.REST)


class SmallRulesTests(unittest.TestCase):
    def test_the_word_line(self):
        self.assertEqual(run("return E.wordLine('one two three');"), "3 words · about 1 min")
        self.assertEqual(run("return E.wordLine('word');"), "1 word · about 1 min")
        self.assertEqual(run("return E.wordLine(Array(1300).fill('w').join(' '));"), "1,300 words · about 5 min")
        self.assertEqual(run("return E.wordCount('don\\'t stop — café 12');"), 5)

    def test_the_script_height_is_clamped(self):
        self.assertEqual(run("return [E.clampScriptHeight(100), E.clampScriptHeight(700.4), E.clampScriptHeight(9999), E.clampScriptHeight('x')];"),
                         [240, 700, 2400, None])

    def test_the_note_falls_back_to_the_settings_default(self):
        self.assertEqual(run("return E.noteFor({note_pad: '', note_color: 'plaid'}, {default_pad: 'index', default_color: 'green'});"),
                         {"pad": "index", "color": "green"})
        self.assertEqual(run("return E.noteFor({note_pad: 'Paper', note_color: 'pink'}, {});"),
                         {"pad": "paper", "color": "pink"})

    def test_the_air_date(self):
        self.assertEqual(run("return E.airDate({status: 'Writers Likey', scheduled_at: ''});"),
                         {"onBoard": False, "text": "not on the board yet", "hint": ""})
        self.assertEqual(run("return E.airDate({status: 'Ready for Air', scheduled_at: '2026-09-21'});"),
                         {"onBoard": True, "text": "mon sep 21",
                          "hint": "to move it, unschedule on the board and drag it to another week."})

    def test_the_status_line(self):
        now = "new Date(2026, 8, 30, 23, 30)"
        self.assertEqual(run(f"return E.statusLine({{status: 'Writers Room'}}, {now});"),
                         "in the writers room. move it to writers likey before it can be scheduled.")
        self.assertEqual(run(f"return E.statusLine({{status: 'Ready for Air', scheduled_at: '2026-10-05'}}, {now});"),
                         "on the board. airs mon oct 5.")
        self.assertEqual(run(f"return E.statusLine({{status: 'Ready for Air', scheduled_at: '2026-09-30'}}, {now});"),
                         "on the board. airs today.")

    def test_the_saved_line(self):
        self.assertEqual(run("return E.savedLine(new Date(2026, 8, 30, 14, 4));"), "saved to obsidian 2:04pm")
        self.assertEqual(run("return E.savedLine(new Date(2026, 8, 30, 0, 15));"), "saved to obsidian 12:15am")

    def test_the_deep_link(self):
        self.assertEqual(run("return E.essayFromSearch('?essay=abc%20d');"), "abc d")
        self.assertEqual(run("return E.essayFromSearch('');"), "")
        self.assertEqual(run("return E.editorUrl('a b');"), "/airdate?essay=a%20b")


if __name__ == "__main__":
    unittest.main()
