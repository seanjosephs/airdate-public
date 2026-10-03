"""Slice 4c: the editor tour, and what it shares with the home tour.

Two halves:

- the server remembers that the editor tour has been shown, once per vault,
  as `editor.tour_done` in config.json, set through POST /api/settings/editor;
- static/airdate-tour.js gained an options argument for the editor's five
  steps. With no options it is the home tour exactly as it was: the same ten
  stops, the same key in this browser's localStorage, the same 6px ring and
  the same callout markup. tests/test_airdate_tour.py pins that tour and is
  not edited; the tests here pin the parts it does not reach and the options.

The tour tests run through node with the fake DOM from test_airdate_tour.
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import airdate_config  # noqa: E402
from test_airdate_tour import FAKE_DOM, HELPER  # noqa: E402
from test_room_editor import EditorCase, HttpCase  # noqa: E402


# ---- the server -------------------------------------------------------------


class TourDoneConfigTests(unittest.TestCase):
    """editor.tour_done: a boolean, false until the tour has been shown."""

    def valid(self, **editor):
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        config["vault"]["path"] = "/somewhere"
        config["editor"].update(editor)
        return config

    def errors(self, **editor):
        return [e for e in airdate_config.validate_config(self.valid(**editor)) if e.startswith("editor.")]

    def test_the_default_is_not_done(self):
        self.assertIs(airdate_config.DEFAULT_CONFIG["editor"]["tour_done"], False)
        self.assertEqual(self.errors(), [])

    def test_true_and_false_are_valid(self):
        for good in (True, False):
            with self.subTest(good=good):
                self.assertEqual(self.errors(tour_done=good), [])

    def test_anything_else_is_refused(self):
        for bad in (None, 1, 0, "true", "yes", [], {}):
            with self.subTest(bad=bad):
                self.assertEqual(len(self.errors(tour_done=bad)), 1)

    def test_validate_editor_checks_it_on_its_own(self):
        self.assertEqual(airdate_config.validate_editor({"mode": "simplified", "tour_done": True}), [])
        self.assertEqual(len(airdate_config.validate_editor({"mode": "simplified", "tour_done": "done"})), 1)

    def test_an_old_config_gains_it_as_not_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = airdate_config.config_path(Path(tmp))
            path.write_text(json.dumps({"editor": {"mode": "complete", "script_height": 600}}), encoding="utf-8")
            config, _, _ = airdate_config.load_config(Path(tmp))
        self.assertIs(config["editor"]["tour_done"], False)
        self.assertEqual((config["editor"]["mode"], config["editor"]["script_height"]), ("complete", 600))

    def test_the_example_config_carries_it(self):
        example = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertIs(example["editor"]["tour_done"], False)


class TourDoneRouteTests(EditorCase):
    """POST /api/settings/editor records the tour, and the browser is told."""

    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for name in ("OBSIDIAN_ESSAYS_DIR", "AIRDATE_VAULT_DIR"):
            os.environ.pop(name, None)
        self.data = self.root / "data"
        self.data.mkdir()
        self.data_patch = mock.patch.object(self.server, "DATA_DIR", self.data)
        self.data_patch.start()
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        config["vault"]["path"] = str(self.root / "vault")
        config["vault"]["essays_folder"] = "Essays"
        config["substack"]["publication"] = "https://example.substack.com"
        airdate_config.save_config(self.data, config)
        self.server.load_and_apply_config()
        self.assertFalse(self.server.SETUP_REQUIRED, self.server.CONFIG_ERRORS)

    def tearDown(self):
        self.data_patch.stop()
        self.env.stop()
        super().tearDown()

    def on_disk(self):
        return json.loads(airdate_config.config_path(self.data).read_text(encoding="utf-8"))

    def test_the_browser_is_told_it_has_not_run(self):
        self.assertIs(self.server.ui_config_payload()["editor"]["tour_done"], False)

    def test_recording_it_changes_that_key_and_nothing_else(self):
        before = self.on_disk()
        result = self.server.save_editor_settings({"tour_done": True})
        self.assertTrue(result["ok"], result)
        self.assertIs(result["editor"]["tour_done"], True)
        after = self.on_disk()
        before["editor"]["tour_done"] = True
        self.assertEqual(after, before)

    def test_it_survives_a_reload(self):
        self.server.save_editor_settings({"tour_done": True})
        self.server.load_and_apply_config()
        self.assertIs(self.server.ui_config_payload()["editor"]["tour_done"], True)

    def test_the_gear_sheet_keeps_it(self):
        self.server.save_editor_settings({"tour_done": True})
        self.server.save_editor_settings({"mode": "custom", "sections": {"seo_social": True}})
        editor = self.server.ui_config_payload()["editor"]
        self.assertIs(editor["tour_done"], True)
        self.assertEqual(editor["mode"], "custom")

    def test_a_value_that_is_not_true_or_false_is_refused(self):
        before = self.on_disk()
        for bad in ("true", 1, None, "done", [True]):
            with self.subTest(bad=bad):
                result = self.server.save_editor_settings({"tour_done": bad})
                self.assertFalse(result["ok"], result)
                self.assertTrue(any("tour_done" in e for e in result["errors"]), result)
                self.assertEqual(self.on_disk(), before)

    def test_a_config_holding_a_bad_value_reads_as_not_done(self):
        # Hand-edited: the browser is never handed anything but a boolean.
        with mock.patch.dict(self.server.CONFIG, {"editor": {"mode": "simplified", "tour_done": "yes"}}):
            self.assertIs(self.server.editor_settings()["tour_done"], False)


class TourDoneHttpTests(HttpCase):
    def test_the_route_records_it(self):
        with tempfile.TemporaryDirectory() as data:
            with mock.patch.object(self.server, "DATA_DIR", Path(data)):
                status, body = self.call("/api/settings/editor", {"tour_done": True})
                self.assertEqual(status, 200, body)
                self.assertIs(body["editor"]["tour_done"], True)
                saved = json.loads(airdate_config.config_path(Path(data)).read_text(encoding="utf-8"))
                self.assertIs(saved["editor"]["tour_done"], True)

    def test_a_bad_value_answers_400_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as data:
            with mock.patch.object(self.server, "DATA_DIR", Path(data)):
                status, body = self.call("/api/settings/editor", {"tour_done": "yes"})
                self.assertEqual(status, 400, body)
                self.assertFalse(airdate_config.config_path(Path(data)).exists())


# ---- the tour module --------------------------------------------------------


def run_dom(expression: str):
    script = f"""
{FAKE_DOM}
const tour = require({json.dumps(str(HELPER))});
process.stdout.write(JSON.stringify({expression}));
"""
    completed = subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def run_plain(expression: str):
    script = f"""
const tour = require({json.dumps(str(HELPER))});
process.stdout.write(JSON.stringify({expression}));
"""
    completed = subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


# The home tour's callout as it was before the options argument existed,
# character for character. start() with no options must still build this.
HOME_CALLOUT = """<p class="tour-count" id="tour-count"></p>
      <h3 class="tour-title" id="tour-title" tabindex="-1"></h3>
      <p class="tour-text" id="tour-text"></p>
      <div class="tour-actions">
        <button type="button" class="settings-button tour-skip">skip tour</button>
        <span class="tour-nav"><button type="button" class="settings-button tour-back">back</button>
        <button type="button" class="settings-button tour-next">next</button></span>
      </div>"""

# The editor tour's options, as editor-view.js passes them.
EDITOR_OPTIONS = """{ stops: tour.EDITOR_STOPS, key: null, pad: 12, place: tour.placeBeside,
  returnToAnchor: true, arrows: true, className: 'ed-tour', buttonClass: 'ed-tour-button',
  skipLabel: 'skip the tour', countLabel: (n, total) => `step ${n} of ${total}` }"""


class HomeTourIsUnchangedTests(unittest.TestCase):
    """start() with no arguments is the old home tour."""

    def test_the_ten_stops_and_the_key_are_unchanged(self):
        self.assertEqual([s["key"] for s in run_plain("tour.STOPS")],
                         ["essays", "card", "lifecycle", "calendar", "filing", "filters", "editor", "shelf", "rainy", "settings"])
        self.assertEqual(run_plain("tour.TOUR_KEY"), "airdate.tour")

    def test_the_callout_markup_is_unchanged(self):
        html = run_dom("(() => { tour.start(); return callout().innerHTML; })()")
        self.assertEqual(html, HOME_CALLOUT)

    def test_the_classes_are_unchanged(self):
        names = run_dom("(() => { tour.start(); return [body.children[0].className, callout().className]; })()")
        self.assertEqual(names, ["tour-ring", "tour-callout"])

    def test_the_ring_still_sits_6px_outside_the_anchor(self):
        ring = run_dom("(() => { globalThis.__rect = {top:100,left:50,width:200,height:40,bottom:140,right:250};"
                       " tour.start(); return body.children[0].style; })()")
        self.assertEqual((ring["top"], ring["left"], ring["width"], ring["height"]), ("94px", "44px", "212px", "52px"))

    def test_the_count_reads_as_before(self):
        self.assertEqual(run_dom("(() => { tour.start(); return callout().querySelector('#tour-count').textContent; })()"),
                         "1 of 10")

    def test_its_state_stays_in_this_browser(self):
        stored = run_dom("(() => { tour.start(); tour.end('dismissed'); return localStorage._d; })()")
        self.assertEqual(stored, {"airdate.tour": "dismissed"})

    def test_arrow_keys_do_nothing_to_the_home_tour(self):
        result = run_editor("tour.start(); const taken = key('ArrowRight'); return [taken, callout().dataset.stop];")
        self.assertEqual(result, [False, "essays"])

    def test_focus_goes_back_where_it_was(self):
        result = run_dom("(() => { const before = makeEl('button'); document.activeElement = before;"
                         " tour.start(); tour.end('seen'); return document.activeElement === before; })()")
        self.assertTrue(result)


# A keydown that reports whether the tour took it.
KEY = """function key(name) {
  let prevented = false;
  const ev = { type: 'keydown', key: name, target: null, shiftKey: false,
    preventDefault() { prevented = true; }, stopPropagation() {} };
  for (const l of listeners) if (l.type === 'keydown') l.fn(ev);
  return prevented;
}
"""


def run_editor(expression: str):
    return run_dom(f"(() => {{ {KEY} {expression} }})()")


class EditorTourTests(unittest.TestCase):
    """The five editor steps, from the TourSteps artboard."""

    def test_the_five_steps_in_order(self):
        keys = [s["key"] for s in run_plain("tour.EDITOR_STOPS")]
        self.assertEqual(keys, ["stamp", "script", "save", "send", "board"])

    def test_the_headlines_and_sentences_are_the_artboards(self):
        stops = {s["key"]: s for s in run_plain("tour.EDITOR_STOPS")}
        self.assertEqual(stops["stamp"]["title"], "this stamp is where it stands.")
        self.assertEqual(stops["script"]["title"], "write here.")
        self.assertEqual(stops["script"]["text"], "this is the script. everything else on the page is packaging. "
                                                  "drag the bottom edge to make it taller, and airdate will remember.")
        self.assertEqual(stops["save"]["title"], "nothing saves itself.")
        self.assertEqual(stops["send"]["title"], "check before you send.")
        self.assertEqual(stops["board"]["title"], "pick its note.")
        for stop in stops.values():
            self.assertTrue(stop["anchor"].startswith("#editor "), stop)

    def test_the_sticky_sits_where_the_artboard_says(self):
        sides = {s["key"]: s["side"] for s in run_plain("tour.EDITOR_STOPS")}
        self.assertEqual(sides, {"stamp": "below", "script": "right", "save": "below", "send": "left", "board": "left"})

    def test_the_home_stops_are_not_the_editor_stops(self):
        self.assertTrue(run_plain("tour.STOPS !== tour.EDITOR_STOPS && tour.STOPS.length === 10"))

    def test_nothing_is_written_to_this_browser(self):
        stored = run_editor(f"tour.start({EDITOR_OPTIONS}); tour.end('seen'); return localStorage._d;")
        self.assertEqual(stored, {})

    def test_the_ring_sits_12px_outside_the_anchor(self):
        ring = run_editor("globalThis.__rect = {top:100,left:50,width:200,height:40,bottom:140,right:250};"
                          f" tour.start({EDITOR_OPTIONS}); return body.children[0].style;")
        self.assertEqual((ring["top"], ring["left"], ring["width"], ring["height"]), ("88px", "38px", "224px", "64px"))

    def test_the_sticky_wears_its_own_classes_and_words(self):
        result = run_editor(f"tour.start({EDITOR_OPTIONS}); const c = callout();"
                            " return [body.children[0].className, c.className, c.innerHTML,"
                            " c.querySelector('#tour-count').textContent, c.dataset.stop];")
        ring_class, callout_class, html, count, stop = result
        self.assertEqual(ring_class, "tour-ring ed-tour")
        self.assertEqual(callout_class, "tour-callout ed-tour")
        self.assertIn('class="ed-tour-button tour-skip">skip the tour</button>', html)
        self.assertNotIn("settings-button", html)
        self.assertEqual(count, "step 1 of 5")
        self.assertEqual(stop, "stamp")

    def test_arrow_keys_move_between_steps(self):
        result = run_editor(f"tour.start({EDITOR_OPTIONS}); const seen = [callout().dataset.stop];"
                            " seen.push(key('ArrowRight')); seen.push(callout().dataset.stop);"
                            " key('ArrowRight'); seen.push(callout().dataset.stop);"
                            " key('ArrowLeft'); seen.push(callout().dataset.stop);"
                            " key('ArrowLeft'); key('ArrowLeft'); seen.push(callout().dataset.stop);"
                            " return seen;")
        self.assertEqual(result, ["stamp", True, "script", "save", "script", "stamp"])

    def test_the_last_step_reads_done_and_the_first_has_no_back(self):
        result = run_editor(f"tour.start({EDITOR_OPTIONS}); const next = callout().querySelector('.tour-next');"
                            " const first = next.textContent; for (let i = 0; i < 6; i += 1) key('ArrowRight');"
                            " return [first, next.textContent, callout().dataset.stop];")
        self.assertEqual(result, ["next", "done", "board"])

    def test_escape_skips_the_tour_and_says_so(self):
        result = run_editor("const ends = []; const o = " + EDITOR_OPTIONS + ";"
                            " o.onEnd = (r, stop) => ends.push([r, stop.key]); tour.start(o); key('ArrowRight');"
                            " const prevented = key('Escape'); return [prevented, tour.isActive(), ends];")
        self.assertEqual(result, [True, False, [["dismissed", "script"]]])

    def test_focus_returns_to_the_anchor_of_the_step_you_were_on(self):
        result = run_editor("const before = makeEl('button'); document.activeElement = before;"
                            f" tour.start({EDITOR_OPTIONS}); key('ArrowRight'); tour.end('seen');"
                            " return [document.activeElement === anchor, document.activeElement === before];")
        self.assertEqual(result, [True, False])

    def test_is_active_follows_the_tour(self):
        result = run_editor(f"const a = tour.isActive(); tour.start({EDITOR_OPTIONS}); const b = tour.isActive();"
                            " tour.end('seen'); return [a, b, tour.isActive()];")
        self.assertEqual(result, [False, True, False])

    def test_the_editor_tour_is_modal_to_the_pointer_too(self):
        result = run_editor(f"tour.start({EDITOR_OPTIONS}); const r = fire('click', makeEl('div'));"
                            " const types = pointerTypes().map((l) => l.type).sort(); return [r.prevented, types];")
        self.assertEqual(result, [True, ["click", "dblclick", "dragstart", "mousedown", "mouseup", "pointerdown"]])

    def test_ending_it_gives_the_page_back(self):
        removed = run_editor(f"tour.start({EDITOR_OPTIONS}); tour.end('seen'); return removed.sort();")
        for kind in ("click", "dblclick", "dragstart", "keydown", "mousedown", "mouseup", "pointerdown"):
            self.assertIn(kind, removed)

    def test_a_stops_text_can_be_rewritten_for_this_essay(self):
        text = run_editor("const o = " + EDITOR_OPTIONS + "; o.textFor = (stop) => stop.key === 'stamp' ? 'live today.' : undefined;"
                          " tour.start(o); const first = callout().querySelector('#tour-text').textContent;"
                          " key('ArrowRight'); return [first, callout().querySelector('#tour-text').textContent];")
        self.assertEqual(text[0], "live today.")
        self.assertTrue(text[1].startswith("this is the script."))


class PlaceBesideTests(unittest.TestCase):
    """The sticky sits beside its anchor, 24px clear of the ring, never over
    it, and flips to the other side near an edge."""

    VIEW = "{ width: 1440, height: 900 }"
    SIZE = "width: 330, height: 250"

    def place(self, box, side, view=None):
        return run_plain(
            f"tour.placeBeside({{ box: {json.dumps(box)}, {self.SIZE}, pad: 12, stop: {{ side: {json.dumps(side)} }},"
            f" viewport: {view or self.VIEW} }})"
        )

    @staticmethod
    def overlaps(spot, box, pad=12):
        left, top, right, bottom = spot["left"], spot["top"], spot["left"] + 330, spot["top"] + 250
        return not (right <= box["left"] - pad or left >= box["right"] + pad
                    or bottom <= box["top"] - pad or top >= box["bottom"] + pad)

    def test_a_header_anchor_gets_it_below(self):
        box = {"top": 14, "left": 96, "right": 286, "bottom": 70, "width": 190, "height": 56}
        spot = self.place(box, "below")
        self.assertEqual(spot["side"], "below")
        self.assertEqual(spot["top"], 70 + 12 + 24)
        self.assertFalse(self.overlaps(spot, box))

    def test_a_rail_anchor_gets_it_to_the_left(self):
        box = {"top": 230, "left": 1108, "right": 1424, "bottom": 440, "width": 316, "height": 210}
        spot = self.place(box, "left")
        self.assertEqual(spot["side"], "left")
        self.assertEqual(spot["left"], 1108 - 12 - 24 - 330)
        self.assertFalse(self.overlaps(spot, box))

    def test_the_script_gets_it_to_the_right_over_the_rail(self):
        box = {"top": 350, "left": 56, "right": 1060, "bottom": 976, "width": 1004, "height": 626}
        spot = self.place(box, "right", "{ width: 1440, height: 1000 }")
        self.assertEqual(spot["side"], "right")
        self.assertEqual(spot["left"], 1060 + 12 + 24)
        self.assertFalse(self.overlaps(spot, box))

    def test_near_an_edge_it_flips_to_the_other_side(self):
        box = {"top": 300, "left": 20, "right": 200, "bottom": 360, "width": 180, "height": 60}
        spot = self.place(box, "left")
        self.assertEqual(spot["side"], "right")
        self.assertFalse(self.overlaps(spot, box))
        low = {"top": 800, "left": 96, "right": 286, "bottom": 856, "width": 190, "height": 56}
        spot = self.place(low, "below")
        self.assertEqual(spot["side"], "above")
        self.assertFalse(self.overlaps(spot, low))

    def test_it_never_leaves_the_window(self):
        box = {"top": 14, "left": 1300, "right": 1420, "bottom": 54, "width": 120, "height": 40}
        spot = self.place(box, "below")
        self.assertGreaterEqual(spot["left"], 8)
        self.assertLessEqual(spot["left"] + 330, 1440 - 8)
        self.assertFalse(self.overlaps(spot, box))


if __name__ == "__main__":
    unittest.main()
