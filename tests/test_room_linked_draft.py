"""A linked draft starts with the passage the writer selected.

A source note is never sent. Its row in the editor makes a linked draft: a new
note beside the source, tied to it by draft_of, whose body is the passage
selected in the script (empty when nothing is selected).

- editor.js decides what to send (linkedDraftPayload) and what the source row
  says about it, both through node.
- The create-draft route puts the selection in the new note and leaves the
  source note exactly as it was.
- editor-view.js runs in node against the swap tests' fake DOM: it reads the
  selection when the row is pressed, and says the draft started with it.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_room_hardening import HttpCase  # noqa: E402
from test_room_swap import run_room  # noqa: E402

EDITOR = ROOT / "static" / "room" / "editor.js"
NODE = shutil.which("node")

if NODE is None:  # pragma: no cover - depends on the machine
    raise RuntimeError("node is required for tests/test_room_linked_draft.py and was not found on PATH")


def js(value) -> str:
    return json.dumps(value)


def editor(body: str):
    script = f"""
const E = require({js(str(EDITOR))});
const result = (() => {{ {body} }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
"""
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


# ---- what to send -------------------------------------------------------------------


class LinkedDraftPayloadTests(unittest.TestCase):
    def payload(self, script, start, end):
        return editor(f"return E.linkedDraftPayload({js(script)}, {js(start)}, {js(end)});")

    def test_the_selected_passage_is_sent(self):
        self.assertEqual(self.payload("Alpha beta gamma", 6, 10), {"selected_text": "beta"})

    def test_lines_and_paragraphs_are_kept_as_written(self):
        script = "One.\n\nTwo lines\nof text.\n\nThree."
        self.assertEqual(self.payload(script, 6, 28), {"selected_text": script[6:28]})

    def test_a_caret_with_nothing_selected_sends_nothing(self):
        self.assertEqual(self.payload("Alpha beta gamma", 6, 6), {})

    def test_only_blank_space_selected_sends_nothing(self):
        self.assertEqual(self.payload("Alpha   \n  gamma", 5, 11), {})

    def test_offsets_past_the_end_are_held_to_the_script(self):
        self.assertEqual(self.payload("Alpha beta", 6, 400), {"selected_text": "beta"})
        self.assertEqual(self.payload("Alpha beta", 400, 500), {})

    def test_offsets_that_are_not_numbers_send_nothing(self):
        for start, end in ((None, None), ("x", "y"), (None, 5)):
            with self.subTest(start=start, end=end):
                self.assertEqual(self.payload("Alpha beta", start, end), {})

    def test_no_script_sends_nothing(self):
        self.assertEqual(editor("return E.linkedDraftPayload(null, 0, 5);"), {})


class SourceRowTests(unittest.TestCase):
    def row(self):
        return editor("""
const list = E.readinessList({ source_role: 'source', blockers: [{ key: 'source_role', field: 'source_role', message: 'x' }] },
  {}, {}, 'body');
return list.rows[0];
""")

    def test_the_source_row_tells_the_writer_a_selection_starts_the_draft(self):
        row = self.row()
        self.assertEqual(row["kind"], "source")
        self.assertIn("select", row["hint"])
        self.assertIn("passage", row["hint"])
        self.assertEqual(row["hint"], row["hint"].lower())
        self.assertNotIn("—", row["hint"])

    def test_the_row_still_says_what_it_did(self):
        row = self.row()
        self.assertIn("linked draft", row["text"])
        self.assertEqual(row["action"], "make a linked draft")


# ---- the route ------------------------------------------------------------------------------


class CreateDraftRouteTests(HttpCase):
    PASSAGE = "A passage the writer chose.\n\nWith two paragraphs."

    def make(self, payload):
        source_id = self.note("Long Source.md", "Writers Room", 'source_role: "source"')
        source_bytes = (self.vault / "Long Source.md").read_bytes()
        status, body = self.post(f"/api/essays/{source_id}/create-draft", payload)
        return status, body, source_bytes

    def draft(self, body):
        text = (self.vault / body["draft_relative_path"]).read_text(encoding="utf-8")
        return self.server.split_frontmatter(text)

    def test_the_selection_is_the_new_notes_body(self):
        status, body, _ = self.make({"selected_text": self.PASSAGE})
        self.assertEqual(status, 200, body)
        frontmatter, text = self.draft(body)
        self.assertEqual(text.strip(), self.PASSAGE)
        self.assertEqual(frontmatter["source_role"], "draft")
        self.assertEqual(frontmatter["draft_of"], "Long Source.md")
        self.assertEqual(frontmatter["status"], "Writers Room")
        self.assertEqual(frontmatter["title"], "Long Source - Draft")

    def test_no_selection_still_makes_an_empty_draft(self):
        status, body, _ = self.make({})
        self.assertEqual(status, 200, body)
        _, text = self.draft(body)
        self.assertEqual(text.strip(), "")

    def test_a_blank_selection_makes_an_empty_draft_too(self):
        status, body, _ = self.make({"selected_text": "  \n\n  "})
        self.assertEqual(status, 200, body)
        _, text = self.draft(body)
        self.assertEqual(text.strip(), "")

    def test_the_source_note_is_not_touched(self):
        status, body, before = self.make({"selected_text": self.PASSAGE})
        self.assertEqual(status, 200, body)
        self.assertEqual((self.vault / "Long Source.md").read_bytes(), before)

    def test_the_draft_is_its_own_row_with_its_own_id(self):
        status, body, _ = self.make({"selected_text": self.PASSAGE})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["draft_essay_id"])
        self.assertNotEqual(body["draft_essay_id"], body["source_essay_id"])
        self.assertEqual(body["draft"]["row"]["id"], body["draft_essay_id"])


# ---- the editor ------------------------------------------------------------------------------

OPEN_A_SOURCE = """
require(ROOT + '/static/room/editor-view.js');
byId('editor').hidden = true;
const posts = [];
globalThis.RoomApi.postJson = async (url, body) => {
  posts.push({ url, body });
  if (url.endsWith('/preflight')) {
    return { source_role: 'source', blockers: [{ key: 'source_role', field: 'source_role', message: 'x' }] };
  }
  if (url.endsWith('/create-draft')) return { draft_essay_id: 'made-1', draft: { title: 'Known - Draft' } };
  return {};
};
const link = makeEl('card-link');
link.href = new URL('/airdate?essay=known', location.href).href;
fireDoc('click', { type: 'click', button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false,
  defaultPrevented: false, target: { closest: (sel) => (sel === 'a.card-link' ? link : null) }, preventDefault() {} });
await settle();
const list = byId('ed-readiness-list');
const rowHtml = list.innerHTML;
const item = { innerHTML: '', querySelector: () => ({ focus() {} }) };
const button = { setAttribute() {}, removeAttribute() {}, closest: () => item };
const script = byId('ed-f-body');
script.value = 'Alpha beta gamma';
const press = async (start, end) => {
  script.selectionStart = start;
  script.selectionEnd = end;
  list.fire('click', { target: { closest: (sel) => (sel === '[data-make-draft]' ? button : null) } });
  await settle();
  return { posts: posts.filter((p) => p.url.endsWith('/create-draft')), made: item.innerHTML };
};
"""


class EditorMakesTheDraftTests(unittest.TestCase):
    def test_the_source_row_says_how_to_start_the_draft_from_a_passage(self):
        out = run_room(OPEN_A_SOURCE + "return rowHtml;")
        self.assertIn("data-make-draft", out)
        self.assertIn('class="ed-ready-hint"', out)
        self.assertIn("select a passage", out)

    def test_the_hint_is_the_buttons_description_for_a_screen_reader(self):
        out = run_room(OPEN_A_SOURCE + "return rowHtml;")
        self.assertIn('aria-describedby="ed-ready-hint"', out)
        self.assertIn('id="ed-ready-hint"', out)

    def test_the_selection_is_what_the_row_sends(self):
        out = run_room(OPEN_A_SOURCE + "return await press(6, 10);")
        self.assertEqual([p["body"] for p in out["posts"]], [{"selected_text": "beta"}])
        self.assertIn("made a linked draft: Known - Draft", out["made"])
        self.assertIn("started with your selection", out["made"])
        self.assertIn('data-open-draft="made-1"', out["made"])

    def test_with_nothing_selected_the_draft_starts_empty(self):
        out = run_room(OPEN_A_SOURCE + "return await press(4, 4);")
        self.assertEqual([p["body"] for p in out["posts"]], [{}])
        self.assertIn("made a linked draft: Known - Draft", out["made"])
        self.assertNotIn("selection", out["made"])


class SetupTests(unittest.TestCase):
    def bullet(self):
        # SETUP.md wraps its lines, so a phrase can span one.
        doc = (ROOT / "SETUP.md").read_text(encoding="utf-8")
        start = doc.index("- **Long source notes.**")
        return " ".join(doc[start:doc.index("\n- **", start + 1)].split())

    def test_setup_describes_long_sources_and_the_selection(self):
        bullet = self.bullet()
        for phrase in ("make a linked draft", "draft_of", "selected in the script", "not changed"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, bullet)

    def test_setup_says_which_rule_decides_what_is_a_source(self):
        # The order the server decides in (source_role_for): an explicit role
        # wins, then draft_of, then the file name and the length.
        bullet = self.bullet()
        for phrase in ("source_role: source", "source_role: standalone", "says nothing about its role",
                       "no `draft_of`", "RAW_", "any case", "10,000 words"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, bullet)

    def test_the_rule_it_describes_is_the_servers(self):
        # Read from the source, so a writer's own environment cannot change
        # what this checks.
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn('env_first("AIR_DATE_LONG_SOURCE_WORD_THRESHOLD", default="10000")', source)
        self.assertIn('SOURCE_ROLE_SET = {"source", "draft", "standalone"}', source)
        body = source[source.index("def source_role_for"):source.index("def ensure_totem")]
        self.assertLess(body.index("if explicit:"), body.index("if draft_of:"))
        self.assertLess(body.index("if draft_of:"), body.index("if is_long_source:"))
        self.assertIn('.lower().startswith("raw_")', body)


class StylesTests(unittest.TestCase):
    def test_the_hint_is_styled(self):
        css = (ROOT / "static" / "room" / "editor.css").read_text(encoding="utf-8")
        self.assertIn(".ed-ready-hint", css)


if __name__ == "__main__":
    unittest.main()
