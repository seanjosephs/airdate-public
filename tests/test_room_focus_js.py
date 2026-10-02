"""v0.6.1: where keyboard focus goes, run through node with a small fake DOM.

- When a card leaves the view (parked from the pool, brought back to the room
  from rainy day), focus used to fall to <body>. It goes to the slip's undo
  when the slip has one, else the next card's title, else the previous one,
  else the view's heading.
- The first-run wizard is a modal: the page behind it is inert while it is
  open, the way the editor does it, and given back when it closes.
"""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

if NODE is None:  # pragma: no cover - depends on the machine
    raise RuntimeError("node is required for tests/test_room_focus_js.py and was not found on PATH")


# Elements that know their selector matches, attributes, focus and inert.
FAKE_DOM = r"""
function makeEl(name, opts = {}) {
  const el = {
    name, inert: false, isConnected: true, attrs: {}, children: opts.children || [],
    matches: opts.matches || {},
    setAttribute(k, v) { el.attrs[k] = String(v); },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(el.attrs, k); },
    getAttribute(k) { return el.hasAttribute(k) ? el.attrs[k] : null; },
    focus() { globalThis.document.activeElement = el; },
    querySelector(sel) { return el.matches[sel] || null; },
  };
  return el;
}
const body = makeEl('body');
globalThis.document = { body, activeElement: body };
"""


def run(module: str, expression: str):
    script = f"""
{FAKE_DOM}
const mod = require({json.dumps(str(ROOT / module))});
const result = (() => {{ return {expression}; }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
"""
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True,
                               env=dict(os.environ))
    return json.loads(completed.stdout)


KEYS = "static/room/keys.js"
WIZARD = "static/airdate-wizard.js"


class NeighbourAfterLeavingTests(unittest.TestCase):
    def after(self, order, gone):
        return run(KEYS, f"mod.afterLeaving({json.dumps(order)}, {json.dumps(gone)})")

    def test_the_next_card_takes_focus(self):
        self.assertEqual(self.after(["a", "b", "c"], "b"), "c")

    def test_the_previous_card_when_it_was_last(self):
        self.assertEqual(self.after(["a", "b", "c"], "c"), "b")

    def test_nothing_when_it_was_the_only_card(self):
        self.assertIsNone(self.after(["a"], "a"))

    def test_nothing_when_it_was_not_showing(self):
        self.assertIsNone(self.after(["a", "b"], "z"))

    def test_ids_compare_as_text(self):
        self.assertEqual(self.after([1, 2], "1"), "2")


class FocusAfterLeavingTests(unittest.TestCase):
    SETUP = """
const undo = makeEl('undo');
const slipWithUndo = { element: makeEl('slip', { matches: { '.slip-undo': undo } }) };
const slipWithout = { element: makeEl('slip') };
const link = makeEl('next-link');
const cards = { next: makeEl('next-card', { matches: { '.card-link': link } }) };
const heading = makeEl('heading');
const card = (id) => cards[id] || null;
"""

    def focus(self, call):
        return run(KEYS, f"(() => {{ {self.SETUP} {call}; "
                         "const a = document.activeElement; return [a.name, a.getAttribute('tabindex')]; })()")

    def test_the_slip_undo_comes_first(self):
        self.assertEqual(
            self.focus("mod.focusAfterLeaving({ slip: slipWithUndo, neighbourId: 'next', card, heading })"),
            ["undo", None])

    def test_without_an_undo_the_next_card_title(self):
        self.assertEqual(
            self.focus("mod.focusAfterLeaving({ slip: slipWithout, neighbourId: 'next', card, heading })"),
            ["next-link", None])

    def test_without_a_card_the_heading_made_focusable(self):
        self.assertEqual(
            self.focus("mod.focusAfterLeaving({ slip: slipWithout, neighbourId: null, card, heading })"),
            ["heading", "-1"])

    def test_a_neighbour_that_is_no_longer_drawn_falls_to_the_heading(self):
        self.assertEqual(
            self.focus("mod.focusAfterLeaving({ slip: null, neighbourId: 'gone', card, heading })"),
            ["heading", "-1"])

    def test_focus_the_writer_moved_elsewhere_is_left_alone(self):
        self.assertEqual(
            self.focus("const search = makeEl('search'); search.focus(); "
                       "mod.focusAfterLeaving({ slip: slipWithUndo, neighbourId: 'next', card, heading })"),
            ["search", None])


class ViewsUseItTests(unittest.TestCase):
    """The pool's park and rainy day's back-to-room both hand focus on."""

    def test_the_pool_park_moves_focus(self):
        source = (ROOT / "static" / "room" / "pool.js").read_text(encoding="utf-8")
        park = source[source.index("async function parkEssay"):source.index("// ---- the needs-filing lane")]
        self.assertIn("Keys.afterLeaving(", park)
        self.assertIn("Keys.focusAfterLeaving(", park)

    def test_the_pool_undo_puts_focus_on_the_card_that_came_back(self):
        # Pressing the slip's undo removes the slip, and with it the focus.
        # The card that returned to the pool takes it, unless the writer has
        # already moved focus somewhere else.
        source = (ROOT / "static" / "room" / "pool.js").read_text(encoding="utf-8")
        back = source[source.index("async function backToRoom"):source.index("async function parkEssay")]
        self.assertIn("cardElement(fresh.id)", back)
        self.assertIn(".card-link", back)
        self.assertIn("document.activeElement", back)

    def test_rainy_day_back_to_room_moves_focus(self):
        source = (ROOT / "static" / "room" / "rainy-day.js").read_text(encoding="utf-8")
        back = source[source.index("async function backToRoom"):source.index("function bind()")]
        self.assertIn("Keys.afterLeaving(", back)
        self.assertIn("Keys.focusAfterLeaving(", back)

    def test_rainy_day_loads_keys_before_it_runs(self):
        html = (ROOT / "room.html").read_text(encoding="utf-8")
        self.assertLess(html.index("/static/room/keys.js"), html.index("/static/room/rainy-day.js"))


class WizardInertTests(unittest.TestCase):
    SETUP = """
const skip = makeEl('skip'); const sidebar = makeEl('sidebar'); const main = makeEl('main');
const editor = makeEl('editor'); const wizard = makeEl('wizard');
const already = makeEl('already'); already.inert = true;
body.children = [skip, sidebar, main, editor, wizard, already];
const page = () => body.children.map((el) => [el.name, el.inert]);
"""

    def page(self, call):
        return run(WIZARD, f"(() => {{ {self.SETUP} {call}; return page(); }})()")

    def test_opening_makes_everything_but_the_wizard_inert(self):
        self.assertEqual(
            self.page("mod.shelter(wizard)"),
            [["skip", True], ["sidebar", True], ["main", True], ["editor", True], ["wizard", False],
             ["already", True]])

    def test_closing_gives_the_page_back_as_it_was(self):
        self.assertEqual(
            self.page("const release = mod.shelter(wizard); release()"),
            [["skip", False], ["sidebar", False], ["main", False], ["editor", False], ["wizard", False],
             ["already", True]])

    def test_the_wizard_uses_it_on_open_and_finish(self):
        source = (ROOT / WIZARD).read_text(encoding="utf-8")
        opening = source[source.index("function open("):]
        self.assertIn("shelter(wizard)", opening)
        finish = opening[opening.index("async function finish()"):opening.index("async function guarded")]
        self.assertIn("release()", finish)


if __name__ == "__main__":
    unittest.main()
