"""Which tour stops run, and that a running tour is modal to the pointer.

Exercised through node like the other browser helpers. The modality tests
install a small fake DOM on globalThis before requiring the module, so the
module's `root` is that fake and `start()` can actually run.
"""

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "static" / "airdate-tour.js"


def run_tour(expression: str):
    script = f"""
const tour = require({json.dumps(str(HELPER))});
process.stdout.write(JSON.stringify({expression}));
"""
    completed = subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def stops_without(*missing):
    return run_tour(f"tour.availableStops((stop) => !{json.dumps(list(missing))}.includes(stop.key)).map((stop) => stop.key)")


# A DOM just big enough for start() to run. body.append records what the tour
# adds, so the test can aim a click at the callout's own button.
FAKE_DOM = r"""
function makeEl(tag) {
  const el = {
    tagName: tag, className: '', hidden: false, style: {}, dataset: {}, textContent: '',
    _html: '', _q: new Map(), children: [],
    classList: { toggle() {}, add() {}, remove() {} },
    offsetWidth: 300, offsetHeight: 160,
    setAttribute() {}, focus() { globalThis.document.activeElement = el; }, remove() {},
    scrollIntoView() {}, getClientRects: () => [{}],
    getBoundingClientRect: () => globalThis.__rect
      || ({ top: 10, left: 10, width: 100, height: 40, bottom: 50, right: 110 }),
    addEventListener() {}, removeEventListener() {},
    append(...n) { el.children.push(...n); },
    querySelectorAll(sel) { return [el.querySelector(sel)]; },
    querySelector(sel) {
      if (!el._q.has(sel)) el._q.set(sel, makeEl('stub'));
      return el._q.get(sel);
    },
    contains(node) {
      if (node === el) return true;
      for (const c of el._q.values()) if (c.contains(node)) return true;
      return false;
    },
  };
  Object.defineProperty(el, 'innerHTML', { get: () => el._html, set: (v) => { el._html = v; } });
  return el;
}
const listeners = [];
const removed = [];
const body = makeEl('body');
const anchor = makeEl('div');
globalThis.innerWidth = 1200;
globalThis.innerHeight = 800;
globalThis.localStorage = { _d: {}, getItem(k) { return this._d[k] ?? null; },
  setItem(k, v) { this._d[k] = v; }, removeItem(k) { delete this._d[k]; } };
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.document = {
  body, activeElement: null, createElement: makeEl, querySelector: () => anchor,
  addEventListener(type, fn, capture) { listeners.push({ type, fn, capture }); },
  removeEventListener(type, capture) { removed.push(type); },
};
function fire(type, target) {
  let prevented = false, stopped = false;
  const ev = { type, target, preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } };
  for (const l of listeners) if (l.type === type) l.fn(ev);
  return { prevented, stopped };
}
const pointerTypes = () => listeners.filter((l) => l.type !== 'keydown');
const callout = () => body.children[1];
"""


def run_tour_dom(expression: str):
    script = f"""
{FAKE_DOM}
const tour = require({json.dumps(str(HELPER))});
process.stdout.write(JSON.stringify({expression}));
"""
    completed = subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


class TourIsModalToThePointerTests(unittest.TestCase):
    """The ring's dimming is drawn with a pointer-transparent box-shadow, so
    without this guard a click reaches the page: Sean opened a card mid-tour and
    its flipped back covered the element the next stop was pointing at."""

    def test_a_click_outside_the_callout_never_reaches_the_page(self):
        result = run_tour_dom("(() => { tour.start(); const r = fire('click', makeEl('div'));"
                              " return [r.prevented, r.stopped]; })()")
        self.assertEqual(result, [True, True])

    def test_the_callout_own_buttons_still_work(self):
        result = run_tour_dom("(() => { tour.start();"
                              " const r = fire('click', callout().querySelector('.tour-next'));"
                              " return [r.prevented, r.stopped]; })()")
        self.assertEqual(result, [False, False], "the tour's own next button must not be swallowed")

    def test_every_way_of_reaching_the_page_by_pointer_is_covered(self):
        types = run_tour_dom("(() => { tour.start(); return pointerTypes().map((l) => l.type).sort(); })()")
        self.assertEqual(types, ["click", "dblclick", "dragstart", "mousedown", "mouseup", "pointerdown"])

    def test_the_guard_runs_in_the_capture_phase(self):
        # Bubble-phase would be too late: the page's own handler fires first.
        self.assertTrue(run_tour_dom("(() => { tour.start();"
                                     " return pointerTypes().every((l) => l.capture === true); })()"))

    def test_scrolling_is_left_alone_so_the_ring_can_follow_the_page(self):
        types = run_tour_dom("(() => { tour.start(); return pointerTypes().map((l) => l.type); })()")
        for blocked in ("wheel", "scroll", "touchmove"):
            self.assertNotIn(blocked, types)

    def test_ending_the_tour_gives_the_page_back(self):
        removed = run_tour_dom("(() => { tour.start(); tour.end('dismissed');"
                               " return removed.sort(); })()")
        for kind in ("click", "dblclick", "dragstart", "keydown", "mousedown", "mouseup", "pointerdown"):
            self.assertIn(kind, removed, f"{kind} listener outlived the tour")


class TourCalloutStaysOnScreenTests(unittest.TestCase):
    """Scrolling is deliberately left working during a tour, so an anchor can end
    up above the viewport. The callout must not follow it off the top."""

    def top_for(self, rect: str) -> float:
        return run_tour_dom(
            f"(() => {{ globalThis.__rect = {rect}; tour.start();"
            " return parseFloat(callout().style.top); })()"
        )

    def test_anchor_scrolled_above_the_viewport_keeps_the_callout_visible(self):
        # bottom is far negative: the old code took this branch unclamped.
        top = self.top_for("({top:-400,left:10,width:100,height:40,bottom:-360,right:110})")
        self.assertGreaterEqual(top, 8, "callout was placed above the top of the window")

    def test_anchor_below_the_fold_keeps_the_callout_visible(self):
        top = self.top_for("({top:1400,left:10,width:100,height:40,bottom:1440,right:110})")
        self.assertGreaterEqual(top, 8)
        self.assertLessEqual(top, 800, "callout was placed below the bottom of the window")

    def test_the_ordinary_case_still_sits_under_its_anchor(self):
        top = self.top_for("({top:100,left:10,width:100,height:40,bottom:140,right:110})")
        self.assertEqual(top, 158)  # bottom + pad(6) + 12


class TourStopTests(unittest.TestCase):
    ALL = ["essays", "card", "lifecycle", "calendar", "filing", "filters", "editor", "shelf", "settings"]

    def test_the_nine_stops_in_spec_order(self):
        self.assertEqual(stops_without(), self.ALL)

    def test_every_stop_has_a_short_callout(self):
        for stop in run_tour("tour.STOPS"):
            self.assertTrue(stop["anchor"] and stop["title"], stop)
            sentences = [part for part in stop["text"].replace("?", ".").split(". ") if part.strip()]
            self.assertLessEqual(len(sentences), 3, stop["key"])

    def test_stops_without_an_element_on_screen_are_skipped(self):
        # Folders off: nothing to file. No publish day: no calendar.
        self.assertEqual(stops_without("filing", "calendar"), [k for k in self.ALL if k not in ("filing", "calendar")])
        # An empty catalog has no card to point at.
        self.assertEqual(stops_without("card", "lifecycle", "editor", "filing"), ["essays", "calendar", "filters", "shelf", "settings"])

    def test_state_is_browser_only(self):
        self.assertEqual(run_tour("tour.TOUR_KEY"), "airdate.tour")
        # Without a browser there is no storage and nothing throws.
        self.assertEqual(run_tour("[tour.tourState(), tour.start === undefined]"), ["", False])


if __name__ == "__main__":
    unittest.main()
