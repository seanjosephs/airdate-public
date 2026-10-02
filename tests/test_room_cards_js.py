"""The script card's pure functions, run through node like the filters helper.

Every call runs in a fixed time zone (TZ below), because paper age and the
stamp dates are local calendar days and must not depend on where the test
happens to run.
"""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "static" / "room" / "cards.js"
TZ = "America/Los_Angeles"
NODE = shutil.which("node")

# A skip here would hide an untested card. Say so loudly instead.
if NODE is None:  # pragma: no cover - depends on the machine
    raise RuntimeError("node is required for tests/test_room_cards_js.py and was not found on PATH")


def run(expression: str):
    script = f"""
const cards = require({json.dumps(str(HELPER))});
const result = (() => {{ return {expression}; }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
"""
    env = dict(os.environ, TZ=TZ)
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True, env=env)
    return json.loads(completed.stdout)


def js(value) -> str:
    return json.dumps(value)


def essay(**fields):
    base = {
        "id": "a1b2c3",
        "title": "A Title",
        "status": "Writers Room",
        "arrived_at": "2026-09-20T17:00:00+00:00",
        "starred_at": "",
        "scheduled_at": "",
        "published_date": "",
        "excerpt": "The first line of the page.",
        "word_count": 1200,
        "note_pad": "sticky",
        "note_color": "pink",
        "totem_raw": "",
        "tags": [],
        "obsidian_url": "obsidian://open?vault=v&file=Essays%2FA",
        "publish_readiness": {"status": "metadata"},
    }
    base.update(fields)
    return base


NOW = "2026-09-30T12:00:00-07:00"  # noon, sep 30, local


class AgeTierTests(unittest.TestCase):
    def tier(self, arrived, now=NOW):
        return run(f"cards.ageTier({js(arrived)}, {js(now)})")

    def test_the_five_boundaries(self):
        cases = [
            ("2026-09-30T08:00:00-07:00", "under-two-weeks"),   # 0 days
            ("2026-09-17T08:00:00-07:00", "under-two-weeks"),   # 13
            ("2026-09-16T08:00:00-07:00", "under-a-month"),     # 14
            ("2026-09-01T08:00:00-07:00", "under-a-month"),     # 29
            ("2026-08-31T08:00:00-07:00", "under-three-months"),  # 30
            ("2026-07-02T08:00:00-07:00", "under-three-months"),  # 90
            ("2026-07-01T08:00:00-07:00", "under-six-months"),    # 91
            ("2026-04-02T08:00:00-07:00", "under-six-months"),    # 181
            ("2026-04-01T08:00:00-07:00", "past-six-months"),     # 182
        ]
        for arrived, expected in cases:
            with self.subTest(arrived=arrived):
                self.assertEqual(self.tier(arrived), expected)

    def test_exactly_182_days_is_past_six_months(self):
        self.assertEqual(run(f"cards.ageDays('2026-04-01T08:00:00-07:00', {js(NOW)})"), 182)
        self.assertEqual(self.tier("2026-04-01T08:00:00-07:00"), "past-six-months")
        self.assertEqual(run(f"cards.ageDays('2026-04-02T08:00:00-07:00', {js(NOW)})"), 181)
        self.assertEqual(self.tier("2026-04-02T08:00:00-07:00"), "under-six-months")

    def test_days_are_local_calendar_days_not_utc(self):
        # 02:00 UTC on sep 17 is still sep 16 in the writer's evening: 14 local
        # days before sep 30, so a month-old pen, although only 13.4 days of
        # wall time have passed.
        self.assertEqual(run(f"cards.ageDays('2026-09-17T02:00:00+00:00', {js(NOW)})"), 14)
        self.assertEqual(self.tier("2026-09-17T02:00:00+00:00"), "under-a-month")

    def test_a_missing_arrival_is_fresh_paper(self):
        self.assertEqual(self.tier(""), "under-two-weeks")
        self.assertEqual(self.tier("not a date"), "under-two-weeks")


class ToolTests(unittest.TestCase):
    def test_each_age_has_its_tool_and_a_sentence(self):
        expected = {
            "under-two-weeks": ("fountain", "silver fountain pen, under two weeks on the desk"),
            "under-a-month": ("rollerball", "black rollerball, under a month on the desk"),
            "under-three-months": ("ballpoint", "cheap ballpoint, under three months on the desk"),
            "under-six-months": ("pencil", "pencil, under six months on the desk"),
            "past-six-months": ("crayon", "chewed crayon, past six months on the desk"),
        }
        for tier, (key, label) in expected.items():
            with self.subTest(tier=tier):
                tool = run(f"cards.toolFor({js(tier)})")
                self.assertEqual(tool["key"], key)
                self.assertEqual(tool["label"], label)


class SignalBarTests(unittest.TestCase):
    def test_the_thresholds(self):
        for words, bars in [(0, 1), (499, 1), (500, 2), (1199, 2), (1200, 3), (2499, 3), (2500, 4), (4999, 4),
                            (5000, 5), (17400, 5)]:
            with self.subTest(words=words):
                self.assertEqual(run(f"cards.barCount({words})"), bars)

    def test_every_artboard_example_fits(self):
        # 251 words one bar, 954 two, 1.3k and 1.6k three, 13.3k and 17.4k five.
        self.assertEqual(run("[251, 954, 1300, 1600, 13300, 17400].map(cards.barCount)"), [1, 2, 3, 3, 5, 5])

    def test_there_are_five_bars_and_the_card_shows_no_number(self):
        html = run(f"cards.cardMarkup({js(essay(word_count=1300))}, {js({'now': '2026-09-30T12:00:00-07:00'})})")
        length = html.split('class="card-length"')[1].split("</span></span>")[0]
        self.assertEqual(length.count('class="bar'), 6)  # the .bars wrapper and five bars
        self.assertEqual(length.count("is-filled"), 3)
        self.assertIn('title="1,300 words', html)
        self.assertIn('<span class="visually-hidden">1,300 words</span>', html)
        self.assertNotIn("1.3k", html)

    def test_word_counts_read_short(self):
        self.assertEqual(run("[251, 954, 1300, 1000, 17400].map(cards.formatWords)"),
                         ["251", "954", "1.3k", "1k", "17.4k"])


class SizeTierTests(unittest.TestCase):
    def size(self, **fields):
        return run(f"cards.sizeTier({js(essay(**fields))})")

    def test_the_four_sizes(self):
        self.assertEqual(self.size(status="Writers Room"), {"key": "room", "cover": 290, "title": 16, "halo": False})
        self.assertEqual(self.size(status="Writers Likey")["cover"], 340)
        self.assertEqual(self.size(status="Writers Likey")["title"], 18)
        complete = self.size(status="Writers Likey", publish_readiness={"status": "ready"})
        self.assertEqual((complete["cover"], complete["title"], complete["halo"]), (390, 21, True))
        ready = self.size(status="Ready for Air", scheduled_at="2026-10-05")
        self.assertEqual((ready["cover"], ready["title"], ready["halo"]), (340, 18, False))

    def test_complete_only_counts_for_likey(self):
        # A complete writers room essay is still a room card: it needs a star.
        self.assertEqual(self.size(status="Writers Room", publish_readiness={"status": "ready"})["key"], "room")


class StampTests(unittest.TestCase):
    def stamp(self, **fields):
        return run(f"cards.stampFor({js(essay(**fields))})")

    def test_room_stamps_the_arrival_on_the_local_calendar(self):
        # 03:00 UTC on sep 21 is the evening of sep 20 in the writer's zone.
        stamp = self.stamp(arrived_at="2026-09-21T03:00:00+00:00")
        self.assertEqual(stamp["kind"], "room")
        self.assertEqual(stamp["date"], "sep 20 2026")
        self.assertEqual(stamp["label"], "writers room, sep 20 2026")

    def test_likey_stamps_the_star_date(self):
        stamp = self.stamp(status="Writers Likey", starred_at="2026-09-29T18:00:00+00:00")
        self.assertEqual((stamp["kind"], stamp["date"]), ("likey", "sep 29 2026"))

    def test_likey_without_a_star_date_carries_no_date(self):
        stamp = self.stamp(status="Writers Likey", starred_at="")
        self.assertEqual((stamp["date"], stamp["label"]), ("", "writers likey"))

    def test_ready_stamps_the_air_date_as_written(self):
        # A calendar date never moves across a time zone.
        stamp = self.stamp(status="Ready for Air", scheduled_at="2026-10-05")
        self.assertEqual((stamp["kind"], stamp["date"]), ("ready", "oct 5 2026"))
        self.assertEqual(self.stamp(status="Ready for Air", scheduled_at="2026-10-05T00:00:00Z")["date"], "oct 5 2026")

    def test_live_stamps_the_post_date(self):
        stamp = self.stamp(status="Live", published_date="2026-09-21", scheduled_at="2026-09-14")
        self.assertEqual((stamp["kind"], stamp["date"], stamp["label"]), ("live", "sep 21 2026", "live, sep 21 2026"))

    def test_live_without_a_post_date_falls_back_to_the_air_date(self):
        self.assertEqual(self.stamp(status="Live", scheduled_at="2026-09-14")["date"], "sep 14 2026")

    def test_the_air_day_reads_like_the_board(self):
        self.assertEqual(run("cards.formatAirDay('2026-09-28')"), "mon sep 28")


LINES = [f"line {i}." for i in range(18)] + ["the coffee ring is load bearing now.", "the crayon is all that's left."]


class JabTests(unittest.TestCase):
    def jab(self, essay_id, tier, lines=LINES):
        return run(f"cards.jabFor({js(essay_id)}, {js(tier)}, {js(lines)})")

    def test_no_jab_before_three_months(self):
        for tier in ("under-two-weeks", "under-a-month", "under-three-months"):
            with self.subTest(tier=tier):
                self.assertEqual(self.jab("abc", tier), "")

    def test_the_same_essay_always_gets_the_same_jab(self):
        first = self.jab("0f3c9a1e", "under-six-months")
        self.assertTrue(first)
        for _ in range(3):
            self.assertEqual(self.jab("0f3c9a1e", "under-six-months"), first)

    def test_different_essays_spread_across_the_lines(self):
        picks = run(f"Array.from({{length: 200}}, (_, i) => cards.jabFor('essay-' + i, 'past-six-months', {js(LINES)}))")
        self.assertGreater(len(set(picks)), 12)

    def test_coffee_ring_and_crayon_only_on_the_oldest_paper(self):
        younger = run(f"Array.from({{length: 400}}, (_, i) => cards.jabFor('e' + i, 'under-six-months', {js(LINES)}))")
        self.assertFalse(any("coffee ring" in p or "crayon" in p for p in younger))
        oldest = run(f"Array.from({{length: 400}}, (_, i) => cards.jabFor('e' + i, 'past-six-months', {js(LINES)}))")
        self.assertTrue(any("coffee ring" in p for p in oldest))
        self.assertTrue(any("crayon" in p for p in oldest))

    def test_no_lines_means_no_jab(self):
        self.assertEqual(self.jab("abc", "past-six-months", []), "")


class PageLineTests(unittest.TestCase):
    def line(self, tier, red_pen, **fields):
        return run(f"cards.pageLine({js(essay(**fields))}, {js(tier)}, {js(red_pen)})")

    def test_a_young_page_shows_the_excerpt(self):
        self.assertEqual(self.line("under-a-month", {"enabled": True, "lines": LINES}),
                         {"kind": "excerpt", "text": "The first line of the page."})

    def test_an_old_page_shows_a_jab_instead(self):
        self.assertEqual(self.line("under-six-months", {"enabled": True, "lines": LINES})["kind"], "jab")

    def test_red_pen_off_shows_the_excerpt(self):
        self.assertEqual(self.line("past-six-months", {"enabled": False, "lines": LINES})["kind"], "excerpt")

    def test_red_pen_off_and_no_excerpt_shows_nothing(self):
        self.assertEqual(self.line("past-six-months", {"enabled": False, "lines": LINES}, excerpt="  "),
                         {"kind": "none", "text": ""})


class SortTests(unittest.TestCase):
    def test_closest_to_air(self):
        rows = [
            essay(id="room-new", status="Writers Room", arrived_at="2026-09-20T00:00:00Z"),
            essay(id="likey", status="Writers Likey", arrived_at="2026-09-01T00:00:00Z"),
            essay(id="ready-late", status="Ready for Air", scheduled_at="2026-10-12"),
            essay(id="room-old", status="Writers Room", arrived_at="2026-01-01T00:00:00Z"),
            essay(id="complete", status="Writers Likey", publish_readiness={"status": "ready"}),
            essay(id="ready-soon", status="Ready for Air", scheduled_at="2026-10-05"),
            essay(id="likey-old", status="Writers Likey", arrived_at="2026-02-01T00:00:00Z"),
        ]
        order = run(f"cards.closestToAirSort({js(rows)}).map((e) => e.id)")
        self.assertEqual(order, ["ready-soon", "ready-late", "complete", "likey-old", "likey", "room-old", "room-new"])

    def test_sorting_does_not_mutate_the_input(self):
        rows = [essay(id="b", status="Writers Room"), essay(id="a", status="Ready for Air", scheduled_at="2026-10-05")]
        self.assertEqual(run(f"(() => {{ const rows = {js(rows)}; cards.closestToAirSort(rows); return rows.map((e) => e.id); }})()"),
                         ["b", "a"])


class RainyDaySortTests(unittest.TestCase):
    def test_default_is_longest_in_the_rain(self):
        rows = [
            essay(id="new", status="Archived", archived_at="2026-09-01T12:00:00Z"),
            essay(id="old", status="Archived", archived_at="2026-02-01T12:00:00Z"),
            essay(id="mid", status="Archived", archived_at="2026-06-01T12:00:00Z"),
        ]
        order = run(f"cards.rainyDaySort({js(rows)}).map((e) => e.id)")
        self.assertEqual(order, ["old", "mid", "new"])

    def test_newest_parked(self):
        rows = [
            essay(id="new", status="Archived", archived_at="2026-09-01T12:00:00Z"),
            essay(id="old", status="Archived", archived_at="2026-02-01T12:00:00Z"),
        ]
        order = run(f"cards.rainyDaySort({js(rows)}, 'newest-parked').map((e) => e.id)")
        self.assertEqual(order, ["new", "old"])

    def test_title(self):
        rows = [essay(id="b", title="Bravo", status="Archived"), essay(id="a", title="Alpha", status="Archived")]
        order = run(f"cards.rainyDaySort({js(rows)}, 'title').map((e) => e.id)")
        self.assertEqual(order, ["a", "b"])

    def test_sorting_does_not_mutate_the_input(self):
        rows = [essay(id="b", status="Archived", archived_at="2026-09-01T12:00:00Z"),
                essay(id="a", status="Archived", archived_at="2026-02-01T12:00:00Z")]
        self.assertEqual(
            run(f"(() => {{ const rows = {js(rows)}; cards.rainyDaySort(rows); return rows.map((e) => e.id); }})()"),
            ["b", "a"])


class DisplayStatusTests(unittest.TestCase):
    def test_a_non_parked_essay_keeps_its_own_status(self):
        self.assertEqual(run(f"cards.displayStatusOf({js(essay(status='Writers Likey'))})"), "Writers Likey")

    def test_a_parked_essay_shows_its_previous_status(self):
        self.assertEqual(
            run(f"cards.displayStatusOf({js(essay(status='Archived', previous_status='Writers Likey'))})"),
            "Writers Likey")

    def test_a_parked_essay_with_no_previous_status_falls_back_to_the_room(self):
        self.assertEqual(run(f"cards.displayStatusOf({js(essay(status='Archived'))})"), "Writers Room")

    def test_an_invalid_previous_status_also_falls_back(self):
        self.assertEqual(
            run(f"cards.displayStatusOf({js(essay(status='Archived', previous_status='Ready for Air'))})"),
            "Writers Room")


class DistributeTests(unittest.TestCase):
    def test_shortest_column_first_leftmost_on_a_tie(self):
        columns = run("cards.distribute([100, 100, 100, 50, 10], 3, (h) => h)")
        self.assertEqual(columns, [[100, 50], [100, 10], [100]])

    def test_one_column_keeps_the_order(self):
        self.assertEqual(run("cards.distribute([1, 2, 3], 1, (h) => h)"), [[1, 2, 3]])


class CardMarkupTests(unittest.TestCase):
    CTX = {
        "now": NOW,
        "totems": {"fox": {"label": "Fox", "image": "/static/totems/fox-512.webp"}},
        "redPen": {"enabled": True, "lines": LINES},
        "presets": [{"name": "Craft", "color": "#FF3B78", "tags": ["writing"]}],
    }

    def markup(self, ctx=None, **fields):
        return run(f"cards.cardMarkup({js(essay(**fields))}, {js(ctx or self.CTX)})")

    def test_the_title_is_a_link_to_the_editor(self):
        html = self.markup(id="abc123", title="Take <Care>")
        self.assertIn('class="card-link" id="card-title-abc123" href="/airdate?essay=abc123"', html)
        self.assertIn("Take &lt;Care&gt;", html)
        self.assertNotIn("<Care>", html)

    def test_the_postit_is_a_toggle_with_one_stable_name(self):
        room = self.markup(status="Writers Room")
        likey = self.markup(status="Writers Likey")
        self.assertIn('aria-pressed="false" aria-label="star for writers likey"', room)
        self.assertIn('aria-pressed="true" aria-label="star for writers likey"', likey)
        self.assertIn("is-hint", room)
        self.assertIn("is-drawn", likey)

    def test_the_postit_is_the_essays_own_pad(self):
        self.assertIn("pad-index pad-green", self.markup(note_pad="index", note_color="green"))
        # Unknown values fall back rather than inventing a class.
        self.assertIn("pad-sticky pad-canary", self.markup(note_pad="napkin", note_color="plaid"))

    def test_the_postit_leaves_once_scheduled(self):
        html = self.markup(status="Ready for Air", scheduled_at="2026-10-05")
        self.assertNotIn("card-postit", html)
        self.assertIn("airs mon oct 5", html)

    def test_a_room_card_has_the_grabber_for_rainy_day_only(self):
        """A writers room essay cannot go on the board, but its grabber drags it
        to rainy day in the sidebar. There is no umbrella on any card."""
        html = self.markup()
        self.assertIn('class="card-handle"', html)
        self.assertIn('aria-label="move to rainy day"', html)
        self.assertNotIn("card-umbrella", html)
        self.assertNotIn('data-action="park"', html)

    def test_the_grabber_is_a_six_dot_grip_on_room_and_likey_cards_only(self):
        for status in ("Writers Room", "Writers Likey"):
            html = self.markup(status=status)
            self.assertEqual(html.split('class="card-handle"')[1].split("</button>")[0].count("<circle"), 6, status)
        self.assertNotIn("card-handle", self.markup(status="Ready for Air", scheduled_at="2026-10-05"))
        self.assertNotIn("card-handle", self.markup(status="Live", published_date="2026-09-21"))

    def test_one_line_above_the_title_holds_everything_in_order(self):
        html = self.markup(status="Writers Likey", needs_intake=True, category="Craft",
                           obsidian_url="obsidian://open?vault=v&file=A")
        meta = html.split('<div class="card-meta">')[1].split("</div>")[0]
        order = [meta.index(part) for part in ("card-topic", 'data-action="intake-suggest"', "card-length",
                                               "card-obsidian", "card-handle")]
        self.assertEqual(order, sorted(order))
        self.assertLess(html.index("card-meta"), html.index('class="card-title"'))
        self.assertNotIn("card-actions", html)

    def test_the_air_chip_is_brief_and_sits_between_the_topic_and_the_bars(self):
        html = self.markup(status="Ready for Air", scheduled_at="2026-10-05")
        meta = html.split('<div class="card-meta">')[1].split("</div>")[0]
        self.assertIn("airs 10/5/26", meta)
        self.assertLess(meta.index("card-meta-start"), meta.index("card-air"))
        self.assertLess(meta.index("card-air"), meta.index("card-length"))

    def test_only_a_parked_card_has_a_row_under_the_title(self):
        self.assertNotIn("card-actions", self.markup(status="Ready for Air", scheduled_at="2026-10-05"))
        self.assertIn("card-actions", self.markup(status="Archived", previous_status="Writers Room"))

    def test_once_a_topic_is_suggested_the_picker_moves_under_the_title(self):
        html = self.markup({**self.CTX, 'intake': {'abc': {'category': 'x', 'categories': ['x', 'y']}}}, id='abc', needs_intake=True)
        self.assertIn("data-action=\"intake-apply\"", html)
        self.assertNotIn("data-action=\"intake-suggest\"", html)

    def test_a_parked_card_shows_its_previous_phase(self):
        html = self.markup(status="Archived", previous_status="Writers Likey", archived_at="2026-05-12T18:00:00Z",
                            starred_at="2026-02-02T18:00:00Z")
        self.assertIn("phase-likey", html)
        self.assertIn('role="img" aria-label="writers likey, feb 2 2026"', html)
        self.assertIn("is-parked", html)
        self.assertIn("in the rain since may 12", html)
        self.assertIn('data-action="unpark"', html)
        self.assertNotIn('data-action="park"', html)
        self.assertNotIn('data-action="place"', html)

    def test_a_card_parked_before_archived_at_existed_says_in_the_rain_without_a_date(self):
        # Essays parked by an older airdate carry no archived_at; the line must
        # not trail off into "in the rain since" with nothing after it.
        html = self.markup(status="Archived")
        self.assertIn(">in the rain</span>", html)
        self.assertNotIn("in the rain since", html)

    def test_a_parked_card_with_no_previous_status_falls_back_to_the_room(self):
        html = self.markup(status="Archived", archived_at="2026-05-12T18:00:00Z")
        self.assertIn("phase-room", html)
        self.assertIn("size-room", html)

    def test_a_parked_cards_star_still_shows(self):
        html = self.markup(status="Archived", previous_status="Writers Likey", archived_at="2026-05-12T18:00:00Z")
        self.assertIn("is-drawn", html)

    def test_the_obsidian_link(self):
        html = self.markup()
        self.assertIn('class="card-obsidian" href="obsidian://open?vault=v&amp;file=Essays%2FA" aria-label="open in obsidian"', html)

    def test_totem_art_only_from_the_notes_own_totem(self):
        self.assertIn('src="/static/totems/fox-512.webp" alt="fox totem"', self.markup(totem_raw="Fox"))
        # An inferred totem is never drawn: no totem_raw, no art.
        self.assertNotIn("card-totem", self.markup(totem_raw="", totem="fox"))
        self.assertNotIn("card-totem", self.markup(totem_raw="dragon"))

    def test_the_writing_tool_matches_the_age(self):
        html = self.markup(arrived_at="2026-05-01T12:00:00-07:00")
        self.assertIn('class="tool tool-pencil" role="img" aria-label="pencil, under six months on the desk"', html)
        self.assertIn("age-under-six-months", html)

    def test_a_jab_on_old_paper(self):
        html = self.markup(arrived_at="2026-01-01T12:00:00-08:00")
        self.assertIn('class="card-jab"', html)
        self.assertNotIn("The first line of the page.", html)

    def test_size_and_phase_classes(self):
        self.assertIn("size-likey-complete", self.markup(status="Writers Likey", publish_readiness={"status": "ready"}))
        self.assertIn("phase-room", self.markup())

    def test_stacked_sheets_follow_the_length(self):
        self.assertEqual(self.markup(word_count=400).count("card-sheet-"), 0)
        self.assertEqual(self.markup(word_count=1300).count("card-sheet-"), 2)
        self.assertEqual(self.markup(word_count=5000).count("card-sheet-"), 3)

    def test_the_stamp_is_one_image_with_its_date(self):
        html = self.markup(status="Live", published_date="2026-09-21")
        self.assertIn('role="img" aria-label="live, sep 21 2026"', html)
        self.assertIn(">SEP 21<", html)

    def test_the_topic_comes_from_the_tag_presets(self):
        html = self.markup(tags=["writing"])
        self.assertIn("--topic:#FF3B78", html)
        self.assertIn('class="card-topic">craft<', html)

    def test_a_bad_preset_colour_never_reaches_a_style(self):
        ctx = dict(self.CTX, presets=[{"name": "x", "color": "red;background:url(x)", "tags": ["writing"]}])
        self.assertNotIn("style=", self.markup(ctx=ctx, tags=["writing"]))

    def test_an_error_is_an_alert_on_the_card(self):
        ctx = dict(self.CTX, error="could not star it.")
        self.assertIn('<p class="card-error" role="alert">could not star it.</p>', self.markup(ctx=ctx))

    def test_no_uppercase_interface_text_outside_the_page(self):
        # Titles are the writer's own; everything airdate writes is lowercase.
        html = self.markup(status="Ready for Air", scheduled_at="2026-10-05")
        for phrase in ("ready for air", "airs mon oct 5", "open in obsidian"):
            self.assertIn(phrase, html)


def run_module(module: str, expression: str):
    path = ROOT / "static" / "room" / module
    script = f"""
const mod = require({json.dumps(str(path))});
const result = (() => {{ return {expression}; }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
"""
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True,
                               env=dict(os.environ, TZ=TZ))
    return json.loads(completed.stdout)


class ApiClassifyTests(unittest.TestCase):
    """A refusal and a file-changed conflict are both 409s; the room must
    never mix them up, because one is a sentence to show and the other is a
    reload."""

    def test_a_refusal_is_its_own_kind(self):
        self.assertEqual(run_module("api.js", "mod.classify(409, {refused: 'ready-for-air', error: 'x.'})"), "refused")

    def test_a_file_change_is_not_a_refusal(self):
        self.assertEqual(run_module("api.js", "mod.classify(409, {reason: 'file_changed'})"), "file-changed")

    def test_setup_and_missing(self):
        self.assertEqual(run_module("api.js", "mod.classify(409, {error_kind: 'setup_required'})"), "setup")
        self.assertEqual(run_module("api.js", "mod.classify(404, {})"), "not-found")
        self.assertEqual(run_module("api.js", "mod.classify(500, null)"), "http")

    def test_the_error_carries_the_marker(self):
        self.assertEqual(
            run_module("api.js", "new mod.RoomApiError('refused', 'x.', 409, {refused: 'live'}).refused"), "live")


class RovingNeighbourTests(unittest.TestCase):
    """Arrow keys across three flex columns of uneven height."""

    COLUMNS = "[['a', 'b', 'c'], ['d', 'e'], ['f']]"
    TOPS = "{a: 0, b: 300, c: 700, d: 0, e: 420, f: 0}"

    def step(self, current, key):
        return run_module("keys.js", f"mod.neighbour({self.COLUMNS}, {json.dumps(current)}, {json.dumps(key)}, (id) => ({self.TOPS})[id])")

    def test_up_and_down_stay_in_the_column(self):
        self.assertEqual(self.step("a", "ArrowDown"), "b")
        self.assertEqual(self.step("b", "ArrowUp"), "a")
        self.assertIsNone(self.step("c", "ArrowDown"))
        self.assertIsNone(self.step("a", "ArrowUp"))

    def test_left_and_right_land_on_the_nearest_card(self):
        self.assertEqual(self.step("b", "ArrowRight"), "e")   # 300 is nearer 420 than 0
        self.assertEqual(self.step("c", "ArrowRight"), "e")
        self.assertEqual(self.step("e", "ArrowLeft"), "b")
        self.assertEqual(self.step("e", "ArrowRight"), "f")
        self.assertIsNone(self.step("f", "ArrowRight"))

    def test_home_and_end(self):
        self.assertEqual(self.step("e", "Home"), "a")
        self.assertEqual(self.step("a", "End"), "f")

    def test_letters_are_not_arrows(self):
        self.assertIsNone(self.step("a", "a"))

    def test_typing_is_detected(self):
        fake = "(tag, type) => ({tagName: tag, type, isContentEditable: false, closest(sel) { return sel.split(/,\\s*/).includes(tag.toLowerCase()) ? this : null; }})"
        self.assertTrue(run_module("keys.js", f"mod.isTyping(({fake})('INPUT', 'search'))"))
        self.assertFalse(run_module("keys.js", f"mod.isTyping(({fake})('INPUT', 'checkbox'))"))
        self.assertFalse(run_module("keys.js", f"mod.isTyping(({fake})('A', ''))"))


if __name__ == "__main__":
    unittest.main()
