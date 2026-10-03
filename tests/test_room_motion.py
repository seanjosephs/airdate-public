"""The room's frame and motion: the locked deck, the grabber's flight, the
brand marks.

- the nav and the board (last week, this week, next week) live in the sidebar,
  which stays in view while the page scrolls, and the pool's search and its
  drop-down filters stay at the top of the essays;
- the grabber: the card turns into its post-it, the post-it flies to the board,
  and the cards left behind slide up into the gap (flight.js, pool.js tuck and
  untuck, placing.js and board-view.js calling them). Putting a note back runs
  it in reverse. With reduced motion it all happens at once;
- the umbrella is the favicon's orange, and "open in obsidian" wears the real
  obsidian logo.

flight.js holds its rules and runs through node; flip() runs
against a small fake DOM that records the animations it starts.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLIGHT = ROOT / "static" / "room" / "flight.js"
NODE = shutil.which("node")

if NODE is None:  # pragma: no cover - depends on the machine
    raise RuntimeError("node is required for tests/test_room_motion.py and was not found on PATH")


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def js(value) -> str:
    return json.dumps(value)


def node(script: str):
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def flight(expression: str):
    return node(f"""
const Flight = require({js(str(FLIGHT))});
const result = (() => {{ return {expression}; }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
""")


FAKE_CARDS = """
globalThis.window = globalThis;
globalThis.matchMedia = (query) => ({ matches: REDUCED });
const animations = [];
function card(id, rect) {
  return {
    id, rect, style: {},
    getBoundingClientRect() { return this.rect; },
    animate(frames, options) { animations.push({ id: this.id, frames, options }); return { addEventListener() {} }; },
  };
}
"""


def flipped(before_after, reduced=False):
    """Run Flight.flip over cards that move from the first rect to the second."""
    script = FAKE_CARDS.replace("REDUCED", "true" if reduced else "false") + f"""
const Flight = require({js(str(FLIGHT))});
const specs = {js(before_after)};
const cards = specs.map((s) => card(s.id, s.before));
const container = {{
  animate() {{}},
  querySelectorAll: () => cards,
}};
let ran = 0;
const out = Flight.flip(container, () => {{ ran += 1; cards.forEach((c, i) => {{ c.rect = specs[i].after; }}); return 'changed'; }});
process.stdout.write(JSON.stringify({{ out, ran, animations }}));
"""
    return node(script)


class FlightRulesTests(unittest.TestCase):
    def test_a_card_that_moved_slides_from_where_it_was(self):
        self.assertEqual(flight("Flight.slideFrom({left: 10, top: 300}, {left: 10, top: 100})"), {"dx": 0, "dy": 200})
        self.assertEqual(flight("Flight.slideFrom({left: 50, top: 100}, {left: 10, top: 100})"), {"dx": 40, "dy": 0})

    def test_a_card_that_did_not_move_does_not_animate(self):
        self.assertIsNone(flight("Flight.slideFrom({left: 10, top: 100}, {left: 10.4, top: 100.3})"))
        self.assertIsNone(flight("Flight.slideFrom(null, {left: 10, top: 100})"))

    def test_a_flight_starts_where_the_note_was_and_lands_on_the_target(self):
        path = flight("Flight.flightPath({left: 20, top: 40, width: 84, height: 76}, {left: 300, top: 120, width: 168, height: 152}, {width: 84, height: 76})")
        self.assertEqual(path["start"], {"x": 20, "y": 40, "sx": 1, "sy": 1})
        self.assertEqual(path["end"], {"x": 300, "y": 120, "sx": 2, "sy": 2})

    def test_the_arc_rises_above_the_straight_line_and_the_note_grows(self):
        path = flight("Flight.flightPath({left: 0, top: 200, width: 84, height: 76}, {left: 400, top: 200, width: 84, height: 76}, {width: 84, height: 76})")
        self.assertLess(path["mid"]["y"] + path["mid"]["sy"] * 76 / 2, 200 + 76 / 2)
        self.assertGreater(path["mid"]["sx"], 1)


class FlipTests(unittest.TestCase):
    def test_cards_below_a_gap_slide_up_and_the_change_runs_once(self):
        result = flipped([
            {"id": "a", "before": {"left": 0, "top": 0}, "after": {"left": 0, "top": 0}},
            {"id": "b", "before": {"left": 0, "top": 400}, "after": {"left": 0, "top": 100}},
        ])
        self.assertEqual(result["out"], "changed")
        self.assertEqual(result["ran"], 1)
        self.assertEqual([a["id"] for a in result["animations"]], ["b"])
        frames = result["animations"][0]["frames"]
        self.assertEqual(frames[0]["transform"], "translate(0px, 300px)")
        self.assertEqual(frames[1]["transform"], "none")

    def test_reduced_motion_changes_things_at_once_with_no_animation(self):
        result = flipped([
            {"id": "b", "before": {"left": 0, "top": 400}, "after": {"left": 0, "top": 100}},
        ], reduced=True)
        self.assertEqual(result["ran"], 1)
        self.assertEqual(result["animations"], [])


class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read("room.html")
        cls.room_css = read("static/room/room.css")
        cls.cards_css = read("static/room/cards.css")
        cls.pool = read("static/room/pool.js")
        cls.placing = read("static/room/placing.js")
        cls.board_view = read("static/room/board-view.js")

    def script_order(self):
        return re.findall(r'<script src="/static/(?:room/)?([\w-]+)\.js\?v=\d+" defer>', self.html)

    def test_flight_loads_before_the_pool(self):
        order = self.script_order()
        self.assertIn("flight", order)
        self.assertLess(order.index("flight"), order.index("pool"))
        self.assertNotIn("deck", order)

    def test_the_board_is_in_the_sidebar_under_the_logo(self):
        side = self.html.split('<aside class="room-sidebar">')[1].split("</aside>")[0]
        self.assertIn('id="board"', side)
        self.assertLess(side.index('class="room-brand"'), side.index('id="board"'))
        main = self.html.split('<main class="room-main"')[1]
        self.assertNotIn('id="board"', main)

    def test_the_sidebar_is_wide_enough_for_a_note_and_locked_in_place(self):
        self.assertRegex(self.room_css, r"\.room-sidebar \{[^}]*flex: 0 0 348px;")
        self.assertRegex(self.room_css, r"\.room-sidebar \{[^}]*position: sticky;[^}]*top: 0;[^}]*height: 100vh;[^}]*overflow-y: auto;")

    def test_the_board_shows_last_week_this_week_and_next_week_top_to_bottom(self):
        self.assertRegex(read("static/room/board.css"), r"\.board-slots \{[^}]*flex-direction: column;")
        self.assertRegex(self.board_view, r"function weeksShown\(\) \{\s*return 2;")
        self.assertNotIn("set-weeks-shown", self.html)

    def test_the_nav_is_four_icons_in_a_row_each_still_named(self):
        nav = self.html[self.html.index('<nav class="room-nav"'):self.html.index("</nav>")]
        for word in ("essays", "the shelf", "rainy day", "settings"):
            self.assertIn(f'<span class="visually-hidden">{word}</span>', nav)
            self.assertIn(f'title="{word}', nav)
        self.assertRegex(self.room_css, r"\.room-nav \{ display: flex; justify-content: center;")

    def test_the_three_slots_are_sized_to_fit_the_window(self):
        self.assertRegex(self.room_css, r"\.room-sidebar \.slot-zone \{[^}]*height: clamp\(190px, calc\(\(100vh - ")
        self.assertRegex(self.room_css, r"@media \(max-height: \d+px\) \{ \.room-sidebar \.board-cork \{ zoom: 0\.\d+; \} \}")

    def test_the_live_button_pulses_red_once_the_air_date_has_come(self):
        css = read("static/room/board.css")
        self.assertRegex(css, r"\.slot\.kind-asking \.note-live-button,\s*\.slot\.kind-onair \.note-live-button \{[^}]*animation: live-due")
        self.assertIn("@keyframes live-due", css)
        self.assertNotIn("not marked live yet.'", read("static/room/board.js"))

    def test_the_board_keys_open_in_a_dialog_from_the_sidebar_foot(self):
        side = self.html.split('<aside class="room-sidebar">')[1].split("</aside>")[0]
        self.assertIn('id="keys-open"', side)
        self.assertLess(side.index('id="board"'), side.index('id="keys-open"'))
        dialog = self.html.split('<dialog class="keys-dialog" id="keys-dialog"')[1].split("</dialog>")[0]
        for words in ("lifts the card", "move between open days", "sets it", "puts it back"):
            self.assertIn(words, dialog)
        self.assertIn("showModal()", read("static/room/keys-help.js"))
        self.assertIn("keys-help", self.script_order())

    def test_the_essays_search_and_drop_downs_stay_at_the_top_of_the_essays(self):
        self.assertRegex(self.room_css, r"\.pool-head \{[^}]*position: sticky;[^}]*top: 0;")

    def test_the_quick_selects_are_drop_downs_in_one_row_between_the_title_and_the_search(self):
        head = self.html.split('<div class="pool-head">')[1].split('<div class="pool-columns"')[0]
        title = head.index('id="pool-heading"')
        show = head.index('id="pool-drop-show"')
        totem = head.index('id="pool-totem-drop"')
        topic = head.index('id="pool-topic"')
        sort = head.index('id="pool-sort"')
        search = head.index('id="pool-search"')
        self.assertTrue(title < show < totem < topic < sort < search)
        for pill in ('data-phase="writers-likey"', 'id="pool-star"', 'id="pool-filing"'):
            self.assertGreater(head.index(pill), show)
            self.assertLess(head.index(pill), totem)

    def test_the_shelf_and_rainy_day_keep_their_search_at_the_top(self):
        self.assertRegex(read("static/room/shelf.css"), r"\.shelf-head \{[^}]*position: sticky;")
        self.assertRegex(read("static/room/rainy-day.css"), r"\.rainy-head \{[^}]*position: sticky;")

    def test_a_lifted_card_leaves_the_layout_and_a_note_flies(self):
        self.assertRegex(self.cards_css, r"\.card\.is-up \{ display: none; \}")
        self.assertIn(".flying-note {", self.cards_css)
        self.assertIn("pointer-events: none;", self.cards_css.split(".flying-note {")[1].split("}")[0])

    def test_the_grabber_tucks_the_card_and_sends_the_note_up(self):
        self.assertIn("pool().tuck(", self.placing)
        self.assertIn("Flight.fly(", self.placing)
        self.assertNotIn("setPlacing(String(id))", self.placing)

    def test_putting_a_note_back_flies_it_home_and_reopens_the_card(self):
        cancel = self.placing.split("function cancel(")[1].split("async function set(")[0]
        self.assertIn("pool().untuck(ended.id, { from })", cancel)

    def test_a_drag_hides_the_card_after_it_has_begun_and_a_drop_brings_it_back(self):
        drag = self.board_view.split("function bindDrag()")[1]
        self.assertIn("window.setTimeout(", drag)
        self.assertIn("tuck(id, { collapse: false })", drag)
        self.assertIn("state.dropped", drag)
        schedule = self.board_view.split("async function schedule(")[1].split("async function scheduleNote(")[0]
        self.assertIn("finally", schedule)
        self.assertIn("RoomPool.untuck(id)", schedule)

    def test_the_pool_skips_a_lifted_card_when_arrow_keys_move_between_cards(self):
        self.assertIn("itemSelector: '.card:not(.is-up)'", self.pool)

    def test_a_card_redrawn_while_its_note_is_up_stays_out_of_the_layout(self):
        self.assertIn("for (const id of state.up) cardElement(id)?.classList.add('is-up');", self.pool)
        self.assertIn("next.classList.add('is-up')", self.pool)


class BrandTests(unittest.TestCase):
    def test_the_umbrella_is_the_favicons_orange_everywhere_it_shows(self):
        tokens = read("static/room/tokens.css")
        color = re.search(r"--umbrella:\s*(#[0-9A-Fa-f]{6});", tokens)
        self.assertIsNotNone(color)
        self.assertNotIn("card-umbrella", read("static/room/cards.css"))
        self.assertRegex(read("static/room/editor.css"), r"\.ed-umbrella \{[^}]*color: var\(--umbrella\);")
        self.assertIn("var(--umbrella)", read("static/room/room.css"))

    def test_the_card_has_no_umbrella_and_rainy_day_takes_a_dropped_card(self):
        self.assertNotIn("card-umbrella", read("static/room/cards.js"))
        html = read("room.html")
        self.assertIn('id="nav-rainy-day"', html)
        pool = read("static/room/pool.js")
        self.assertIn("getElementById('nav-rainy-day')", pool)
        self.assertIn("state.parking.add(id);", pool)
        self.assertIn(".room-nav a.is-drop-target", read("static/room/room.css"))

    def test_the_umbrella_orange_is_close_to_the_favicons(self):
        """Sampled from static/brand/airdate-icon-256.png: the field is about
        #D9500A (217, 80, 10) across its edges."""
        tokens = read("static/room/tokens.css")
        hex_value = re.search(r"--umbrella:\s*#([0-9A-Fa-f]{6});", tokens).group(1)
        red, green, blue = (int(hex_value[i:i + 2], 16) for i in (0, 2, 4))
        self.assertLess(abs(red - 217), 12)
        self.assertLess(abs(green - 80), 12)
        self.assertLess(abs(blue - 10), 14)

    def test_open_in_obsidian_wears_the_real_logo_not_a_drawn_gem(self):
        logo = ROOT / "static" / "brand" / "obsidian-logo.png"
        self.assertTrue(logo.is_file())
        self.assertEqual(logo.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        for relative in ("room.html", "static/room/cards.js", "static/room/shelf.js"):
            with self.subTest(file=relative):
                text = read(relative)
                self.assertIn("/static/brand/obsidian-logo.png", text)
                self.assertNotIn('d="M12 2 4 9l3 13h10l3-13z"', text)


if __name__ == "__main__":
    unittest.main()
