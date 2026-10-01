"""Essay deep links, on the writers room (re-pinned in slice 7).

/airdate?essay=<id> opens that essay's editor over the room. The old page did
this through static/airdate-deep-link.js and a status notice; the room does
it in editor-view.js: a known id opens the editor, an unknown one closes it
again, says so on the red slip, and takes ?essay= out of the address so a
reload does not fail the same way. The old /airdate/room?essay= links reach
the same place through the server's redirect (tests/test_room_swap.py).

The old pins on `.deep-link-notice` and `#editor-shell` are gone with the old
page: the room has neither. Its editor is `#editor`, and its dialog
attributes are pinned here instead.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_room_swap import run_pure, run_room  # noqa: E402


def boot(search):
    return run_room(f"""
location.search = {json.dumps(search)};
byId('editor').hidden = true;
require(ROOT + '/static/room/editor-view.js');
await settle();
return {{ open: !byId('editor').hidden, calls: record.calls, history: record.history, slips: record.slips }};
""")


class RoomDeepLinkTests(unittest.TestCase):
    def test_a_known_id_opens_the_editor(self):
        out = boot("?essay=known")
        self.assertTrue(out["open"])
        self.assertIn("/api/essays/known", out["calls"])
        self.assertEqual(out["slips"], [])
        # The address already names the essay, so nothing is pushed.
        self.assertNotIn("push", [entry[0] for entry in out["history"]])

    def test_an_unknown_id_says_so_on_the_red_slip_and_clears_the_address(self):
        out = boot("?essay=missing-id")
        self.assertFalse(out["open"])
        self.assertEqual(len(out["slips"]), 1)
        self.assertEqual(out["slips"][0]["tone"], "red")
        self.assertIn("not in your essays folder", out["slips"][0]["text"])
        self.assertIn(["replace", "/airdate"], out["history"])

    def test_no_essay_parameter_is_an_ordinary_visit(self):
        out = boot("")
        self.assertFalse(out["open"])
        self.assertEqual(out["calls"], [])

    def test_a_blank_essay_parameter_is_an_ordinary_visit(self):
        self.assertEqual(run_pure("E.essayFromSearch('?essay=%20%20')"), "")
        self.assertFalse(boot("?essay=%20%20")["open"])

    def test_a_durable_id_is_read_exactly(self):
        durable = "c5e72f4c-c829-4bbf-b7ff-58a6a2bd2dc9"
        self.assertEqual(run_pure(f"E.essayFromSearch('?essay={durable}')"), durable)
        self.assertEqual(run_pure("E.essayFromSearch('?essay=essay-1')"), "essay-1")


class RoomEditorDialogTests(unittest.TestCase):
    def test_the_editor_is_a_labelled_modal_dialog(self):
        html = (ROOT / "room.html").read_text(encoding="utf-8")
        tag = next(line for line in html.splitlines() if 'id="editor"' in line and "<div" in line)
        self.assertIn('role="dialog"', tag)
        self.assertIn('aria-modal="true"', tag)
        self.assertIn('aria-labelledby="ed-title"', tag)
        self.assertIn('id="ed-title"', html)


if __name__ == "__main__":
    unittest.main()
