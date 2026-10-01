"""The board's pure functions and the room's one clock, run through node.

Every call runs in a fixed time zone (TZ below). The clock tests also run the
same instant in a zone west of UTC, because the old page read "today" in UTC
and lit ON AIR on Sunday evening in the Americas: the room must not.
"""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOM = ROOT / "static" / "room"
TZ = "America/Los_Angeles"
NODE = shutil.which("node")

if NODE is None:  # pragma: no cover - depends on the machine
    raise RuntimeError("node is required for tests/test_room_board_js.py and was not found on PATH")


def run(expression: str, tz: str = TZ):
    script = f"""
const dates = require({json.dumps(str(ROOM / "dates.js"))});
const board = require({json.dumps(str(ROOM / "board.js"))});
const cards = require({json.dumps(str(ROOM / "cards.js"))});
const result = (() => {{ return {expression}; }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
"""
    env = dict(os.environ, TZ=tz)
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True, env=env)
    return json.loads(completed.stdout)


def js(value) -> str:
    return json.dumps(value)


def essay(**fields):
    base = {
        "id": "e1",
        "title": "A Title",
        "status": "Ready for Air",
        "scheduled_at": "2026-10-06",
        "published_date": "",
        "note_pad": "sticky",
        "note_color": "pink",
        "substack_draft_url": "",
    }
    base.update(fields)
    return base


# Wednesday 30 september 2026.
TODAY = "2026-09-30"


class ClockTests(unittest.TestCase):
    def test_today_is_the_local_calendar_date(self):
        # 8pm sunday in los angeles is already monday in UTC.
        instant = "2026-10-05T03:00:00Z"
        self.assertEqual(run(f"dates.today(new Date({js(instant)}))"), "2026-10-04")
        self.assertEqual(run(f"dates.today(new Date({js(instant)}))", tz="UTC"), "2026-10-05")

    def test_an_air_date_is_read_as_its_day_with_no_zone_arithmetic(self):
        self.assertEqual(run('dates.calendarDay("2026-10-05")'), "2026-10-05")
        self.assertEqual(run('dates.calendarDay("2026-10-05T23:30:00-07:00")'), "2026-10-05")
        self.assertEqual(run('dates.calendarDay("2026-10-05T00:00:00Z")', tz="Pacific/Honolulu"), "2026-10-05")

    def test_not_a_day_is_nothing(self):
        for value in ("", "soon", "2026-02-30", "2026-13-01", None):
            with self.subTest(value=value):
                self.assertEqual(run(f"dates.calendarDay({js(value)})"), "")

    def test_the_monday_of_a_week(self):
        cases = [
            ("2026-09-28", "2026-09-28"),  # a monday is its own
            ("2026-09-29", "2026-09-28"),  # tuesday
            ("2026-10-04", "2026-09-28"),  # sunday closes the week
            ("2026-10-05", "2026-10-05"),
            ("2026-03-09", "2026-03-09"),  # the monday after the US clocks change
            ("2026-11-01", "2026-10-26"),  # the sunday they change back
        ]
        for day, monday in cases:
            with self.subTest(day=day):
                self.assertEqual(run(f"dates.mondayOf({js(day)})"), monday)

    def test_weekday_numbers_match_the_config_names(self):
        self.assertEqual(run('dates.weekdayNumber("sunday")'), 0)
        self.assertEqual(run('dates.weekdayNumber("monday")'), 1)
        self.assertEqual(run('dates.weekdayNumber("Tuesday")'), 2)
        self.assertEqual(run('dates.weekdayNumber("saturday")'), 6)
        self.assertEqual(run('dates.weekdayNumber("whenever")'), -1)
        self.assertEqual(run('dates.weekdayNumber("")'), -1)

    def test_the_anchor_of_a_week_follows_the_publish_day(self):
        # A wednesday (2026-09-30): the tuesday anchor is the day before, the
        # thursday anchor is the next day, and an out-of-range anchor falls
        # back to monday, same as calling weekAnchorOf with none at all.
        self.assertEqual(run('dates.weekAnchorOf("2026-09-30", 2)'), "2026-09-29")
        self.assertEqual(run('dates.weekAnchorOf("2026-09-30", 4)'), "2026-09-24")
        self.assertEqual(run('dates.weekAnchorOf("2026-09-30", 0)'), "2026-09-27")
        self.assertEqual(run('dates.weekAnchorOf("2026-09-30")'), run('dates.weekAnchorOf("2026-09-30", 1)'))
        self.assertEqual(run('dates.weekAnchorOf("2026-09-30", 1)'), run('dates.mondayOf("2026-09-30")'))

    def test_day_arithmetic_crosses_a_clock_change(self):
        self.assertEqual(run('dates.addDays("2026-10-26", 7)'), "2026-11-02")
        self.assertEqual(run('dates.daysBetween("2026-10-30", "2026-11-02")'), 3)

    def test_formats(self):
        self.assertEqual(run('dates.formatDay("2026-10-05")'), "mon oct 5")
        self.assertEqual(run('dates.formatShort("2026-10-05")'), "oct 5")


class WeekTests(unittest.TestCase):
    def test_a_tuesday_air_date_sits_in_its_mondays_week(self):
        rows = [essay(scheduled_at="2026-09-29")]
        self.assertEqual(run(f"[...board.weekIndex({js(rows)}).keys()]"), ["2026-09-28"])

    def test_only_scheduled_and_live_essays_are_on_the_board(self):
        rows = [
            essay(id="a", status="Writers Likey", scheduled_at=""),
            essay(id="b", status="Writers Room", scheduled_at="2026-10-05"),
            essay(id="c", status="Archived", scheduled_at="2026-10-05"),
            essay(id="d", status="Ready for Air", scheduled_at="2026-10-05"),
            essay(id="e", status="Live", scheduled_at="", published_date="2026-09-14"),
        ]
        index = run(f"Object.fromEntries([...board.weekIndex({js(rows)})].map(([k, v]) => [k, v.map((x) => x.essay.id)]))")
        self.assertEqual(index, {"2026-10-05": ["d"], "2026-09-14": ["e"]})

    def test_a_week_holding_two_keeps_both_ready_first(self):
        rows = [essay(id="live", status="Live", scheduled_at="2026-10-05"), essay(id="ready", scheduled_at="2026-10-07")]
        self.assertEqual(run(f"board.weekIndex({js(rows)}).get('2026-10-05').map((x) => x.essay.id)"), ["ready", "live"])

    def test_the_window_is_last_week_plus_weeks_shown(self):
        mondays = run(f"board.windowMondays({js(TODAY)}, 3, 0, new Map())")
        self.assertEqual(mondays, ["2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12"])

    def test_the_pager_moves_the_window_a_week(self):
        self.assertEqual(run(f"board.windowMondays({js(TODAY)}, 3, 1, new Map())")[0], "2026-09-28")
        self.assertEqual(run(f"board.windowMondays({js(TODAY)}, 3, -1, new Map())")[0], "2026-09-14")

    def test_an_older_week_still_asking_stays_on_the_board(self):
        rows = [essay(scheduled_at="2026-08-25"), essay(id="gone", status="Live", scheduled_at="2026-08-11")]
        mondays = run(f"board.windowMondays({js(TODAY)}, 3, 0, board.weekIndex({js(rows)}))")
        self.assertEqual(mondays, ["2026-08-24", "2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12"])
        # Paged away, it does not follow.
        self.assertNotIn("2026-08-24", run(f"board.windowMondays({js(TODAY)}, 3, 1, board.weekIndex({js(rows)}))"))

    def test_the_offset_that_shows_a_monday(self):
        self.assertEqual(run(f'board.offsetShowing("2026-10-19", {js(TODAY)}, 3, 0)'), 1)
        self.assertEqual(run(f'board.offsetShowing("2026-10-05", {js(TODAY)}, 3, 0)'), 0)
        self.assertEqual(run(f'board.offsetShowing("2026-09-21", {js(TODAY)}, 3, 2)'), 0)

    def test_the_range_label(self):
        self.assertEqual(run('board.rangeLabel(["2026-09-21", "2026-10-12"])'), "sep 21 to oct 12")

    def test_a_non_monday_publish_day_moves_the_whole_board(self):
        # thursday = weekday 4. An essay airing friday 2026-10-02 belongs to
        # the thursday-anchored week starting 2026-10-01, and the window,
        # firstOpen and offsetShowing all follow the same anchor.
        rows = [essay(scheduled_at="2026-10-02")]
        self.assertEqual(run(f"[...board.weekIndex({js(rows)}, 4).keys()]"), ["2026-10-01"])
        mondays = run(f"board.windowMondays({js(TODAY)}, 3, 0, new Map(), 4)")
        self.assertEqual(mondays, ["2026-09-17", "2026-09-24", "2026-10-01", "2026-10-08"])
        self.assertEqual(run(f"board.firstOpen({js(TODAY)}, new Map(), 4)"), "2026-10-01")
        self.assertEqual(run(f'board.offsetShowing("2026-10-01", {js(TODAY)}, 3, 0, 4)'), 0)
        # Omitting the anchor keeps monday, so every existing caller is unchanged.
        self.assertEqual(run(f"board.windowMondays({js(TODAY)}, 3, 0, new Map())"),
                         run(f"board.windowMondays({js(TODAY)}, 3, 0, new Map(), 1)"))


class OpenMondayTests(unittest.TestCase):
    ROWS = [essay(id="a", scheduled_at="2026-10-06"), essay(id="b", scheduled_at="2026-10-19")]

    def call(self, expression):
        return run(f"(() => {{ const index = board.weekIndex({js(self.ROWS)}); return {expression}; }})()")

    def test_this_weeks_monday_has_passed_on_a_wednesday(self):
        self.assertTrue(self.call(f'board.isPast("2026-09-28", {js(TODAY)})'))
        self.assertFalse(self.call(f'board.isOpen("2026-09-28", {js(TODAY)}, index)'))

    def test_a_monday_is_open_on_the_day_itself(self):
        self.assertTrue(self.call('board.isOpen("2026-10-12", "2026-10-12", index)'))

    def test_the_first_open_monday_skips_taken_weeks(self):
        self.assertEqual(self.call(f"board.firstOpen({js(TODAY)}, index)"), "2026-10-12")

    def test_stepping_skips_taken_and_past_and_never_wraps(self):
        self.assertEqual(self.call(f'board.stepOpen("2026-10-12", 1, {js(TODAY)}, index)'), "2026-10-26")
        self.assertEqual(self.call(f'board.stepOpen("2026-10-26", -1, {js(TODAY)}, index)'), "2026-10-12")
        self.assertEqual(self.call(f'board.stepOpen("2026-10-12", -1, {js(TODAY)}, index)'), "")


class SlotTests(unittest.TestCase):
    def slot(self, monday, rows, today=TODAY, **ctx):
        extra = ", ".join(f"{k}: {js(v)}" for k, v in ctx.items())
        return run(
            f"(() => {{ const s = board.slotModel({js(monday)}, {{ today: {js(today)}, index: board.weekIndex({js(rows)}), shown: new Map(), settling: new Set(){', ' + extra if extra else ''} }});"
            " return { kind: s.kind, plaque: s.plaque, open: s.open, dim: s.dim, takesDrop: s.takesDrop, candidate: s.candidate }; })()"
        )

    def test_an_open_monday(self):
        self.assertEqual(self.slot("2026-10-12", []), {
            "kind": "open", "plaque": "open", "open": True, "dim": False, "takesDrop": True, "candidate": False,
        })

    def test_a_countdown(self):
        self.assertEqual(self.slot("2026-10-05", [essay(scheduled_at="2026-10-05")])["plaque"], "airs in 5 days")
        self.assertEqual(self.slot("2026-09-28", [essay(scheduled_at="2026-10-01")])["plaque"], "airs tomorrow")

    def test_on_air_day_uses_the_local_day(self):
        slot = self.slot("2026-09-28", [essay(scheduled_at="2026-09-30")])
        self.assertEqual(slot["kind"], "onair")
        self.assertEqual(slot["plaque"], "ON AIR")

    def test_the_day_after_it_asks(self):
        slot = self.slot("2026-09-28", [essay(scheduled_at="2026-09-29")])
        self.assertEqual(slot["kind"], "asking")
        self.assertEqual(slot["plaque"], "aired. not marked live yet.")

    def test_live_is_on_the_shelf_dimmed_and_takes_no_drop(self):
        slot = self.slot("2026-09-14", [essay(status="Live", scheduled_at="2026-09-14")])
        self.assertEqual(slot["plaque"], "aired mon sep 14 · on the shelf")
        self.assertTrue(slot["dim"])
        self.assertFalse(slot["takesDrop"])

    def test_live_stays_full_strength_while_its_plaque_is_green(self):
        rows = [essay(status="Live", scheduled_at="2026-09-14")]
        dim = run(f"board.slotModel('2026-09-14', {{ today: {js(TODAY)}, index: board.weekIndex({js(rows)}), shown: new Map(), settling: new Set(['2026-09-14']) }}).dim")
        self.assertFalse(dim)

    def test_a_past_empty_monday_takes_no_drop(self):
        slot = self.slot("2026-09-21", [])
        self.assertEqual((slot["kind"], slot["takesDrop"], slot["open"]), ("past", False, False))

    def test_placing_dims_taken_and_past_and_names_the_candidate(self):
        rows = [essay(scheduled_at="2026-10-06")]
        placing = {"candidate": "2026-10-12"}
        self.assertEqual(self.slot("2026-10-05", rows, placing=placing)["plaque"], "taken")
        self.assertTrue(self.slot("2026-10-05", rows, placing=placing)["dim"])
        self.assertEqual(self.slot("2026-09-21", rows, placing=placing)["plaque"], "past")
        candidate = self.slot("2026-10-12", rows, placing=placing)
        self.assertEqual(candidate["plaque"], "placing · airs mon oct 12 if you set it")
        self.assertTrue(candidate["candidate"])
        self.assertFalse(candidate["dim"])


class SentenceTests(unittest.TestCase):
    """The house voice: one lowercase sentence, what happened then what is true."""

    def test_the_measured_sentences(self):
        self.assertEqual(run('board.say.scheduled("2026-10-05")'), "on the board. airs mon oct 5.")
        self.assertEqual(run('board.say.taken("2026-09-28", "40s Life Lessons")'), "mon sep 28 is taken. unschedule 40s life lessons first.")
        self.assertEqual(run('board.say.unscheduled("2026-10-05")'), "back in the pool. mon oct 5 is open again.")
        self.assertEqual(run('board.say.live("Direction, Not Distance")'), "live. direction, not distance moved to the shelf.")
        self.assertEqual(run("board.say.notAPost()"), "that is not a substack post link.")
        self.assertEqual(run('board.say.didNotAir("2026-09-14")'), "back in the pool. mon sep 14 stays empty.")
        self.assertEqual(run("board.say.putBack()"), "put back. nothing changed.")
        self.assertEqual(run('board.say.stillThere("2026-09-28", "Two Notes")'), "back in the pool. two notes is still on mon sep 28.")

    def test_the_placing_announcements(self):
        self.assertEqual(
            run('board.say.lifting("Game Plan", "2026-10-05")'),
            "placing game plan. mon oct 5 is open. left and right move, enter sets, escape puts it back.",
        )
        self.assertEqual(run('board.say.moved("2026-10-12")'), "mon oct 12 is open.")

    def test_a_failure_says_why_and_what_to_do(self):
        self.assertEqual(run('board.say.failed("unschedule", { kind: "http" })'),
                         "could not unschedule. obsidian did not save the note. try again.")
        self.assertEqual(run('board.say.failed("schedule", { kind: "network" })'),
                         "could not put it on the board. airdate is not answering. try again.")

    def test_the_past_and_open_sentences_name_the_configured_weekday(self):
        self.assertEqual(run('board.say.past("2026-10-01", "thursday")'), "thu oct 1 has passed. pick a thursday from today on.")
        self.assertEqual(run('board.say.noOpen("thursday")'), "there is no open thursday to put it on. unschedule one first.")
        self.assertEqual(run('board.say.noEarlier("2026-10-01", "thursday")'), "no open thursday before thu oct 1. it stays there.")
        # Omitting it keeps monday, so the existing sentences are unchanged.
        self.assertEqual(run('board.say.past("2026-10-05")'), "mon oct 5 has passed. pick a monday from today on.")
        self.assertEqual(run('board.say.noOpen()'), "there is no open monday to put it on. unschedule one first.")

    def test_every_sentence_is_lowercase_and_ends_with_a_stop(self):
        sentences = run("""[
          board.say.scheduled('2026-10-05'), board.say.taken('2026-10-05', 'X'), board.say.past('2026-10-05'),
          board.say.unscheduled('2026-10-05'), board.say.live('X'), board.say.didNotAir('2026-10-05'),
          board.say.putBack(), board.say.noOpen(), board.say.stillThere('2026-10-05', 'X'), board.say.noEarlier('2026-10-05'),
          board.say.cannotLift('Writers Room'), board.say.cannotLift('Ready for Air'),
          board.say.undoneSchedule('2026-10-05'), board.say.undoneUnschedule('2026-10-05'),
          board.say.undoTaken('2026-10-05'), board.say.undoPast('2026-10-05'), board.say.undoStale(),
          ...['schedule', 'unschedule', 'live', 'didNotAir', 'undo', 'load'].flatMap((a) =>
            ['file-changed', 'network', 'not-found', 'setup', 'http'].map((k) => board.say.failed(a, { kind: k }))),
        ]""")
        for sentence in sentences:
            with self.subTest(sentence=sentence):
                self.assertEqual(sentence, sentence.lower())
                self.assertTrue(sentence.endswith("."))


class LiveLinkTests(unittest.TestCase):
    """The same shape rule the server holds, checked before the round trip."""

    def refusal(self, url, publication=""):
        return run(f"board.liveLinkRefusal({js(url)}, {js(publication)})")

    def test_post_links(self):
        for url, publication in (
            ("https://substack.com/p/a-post", ""),
            ("https://writer.substack.com/p/a-post", ""),
            ("https://example.com/p/a-post", "https://example.com"),
            ("https://www.example.com/p/a-post", "example.com"),
            ("https://example.com/p/a-post", "https://www.example.com"),
        ):
            with self.subTest(url=url):
                self.assertIsNone(self.refusal(url, publication))

    def test_not_post_links(self):
        for url in (
            "http://writer.substack.com/p/a-post",
            "https://writer.substack.com/about",
            "https://writer.substack.com/p/",
            "https://substack.com.evil.test/p/a-post",
            "https://substack.com@evil.test/p/a-post",
            "https://evilsubstack.com/p/a-post",
            "https://example.com/p/a-post",
            "writer.substack.com/p/a-post",
            "",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.refusal(url), "that is not a substack post link.")


class NoteMarkupTests(unittest.TestCase):
    def slot_html(self, monday, rows, today=TODAY, markup_ctx=None, **ctx):
        extra = ", ".join(f"{k}: {js(v)}" for k, v in ctx.items())
        return run(
            f"board.slotMarkup(board.slotModel({js(monday)}, {{ today: {js(today)}, index: board.weekIndex({js(rows)}), shown: new Map(), settling: new Set(){', ' + extra if extra else ''} }}), {js(markup_ctx or {})})"
        )

    def test_a_scheduled_note_carries_the_only_unschedule(self):
        html = self.slot_html("2026-10-05", [essay(title="Take <Care>", scheduled_at="2026-10-06")])
        self.assertIn('data-action="unschedule"', html)
        self.assertIn('aria-label="unschedule take &lt;care&gt;"', html)
        self.assertIn("Take &lt;Care&gt;", html)
        self.assertNotIn("<Care>", html)
        self.assertIn("pad-sticky pad-pink", html)
        self.assertIn("airs in 6 days", html)

    def test_only_the_index_card_has_a_pushpin(self):
        self.assertIn("note-pin", self.slot_html("2026-10-05", [essay(note_pad="index")]))
        self.assertNotIn("note-pin", self.slot_html("2026-10-05", [essay(note_pad="paper")]))
        self.assertNotIn("note-pin", self.slot_html("2026-10-05", [essay(note_pad="sticky")]))

    def test_the_sent_marker_is_a_link_to_the_draft(self):
        html = self.slot_html("2026-10-05", [essay(substack_draft_url="https://writer.substack.com/publish/post/1")])
        self.assertIn('class="note-sent" href="https://writer.substack.com/publish/post/1"', html)

    def test_after_air_day_the_note_asks(self):
        html = self.slot_html("2026-09-28", [essay(id="x1", scheduled_at="2026-09-29")])
        self.assertIn('<label for="live-x1">is it live?</label>', html)
        self.assertIn('id="live-x1" type="url"', html)
        self.assertIn('data-action="did-not-air"', html)
        self.assertIn('data-action="live"', html)
        self.assertIn("aired tue sep 29", html)
        self.assertNotIn('data-action="unschedule"', html)
        self.assertIn("aired. not marked live yet.", html)

    def test_the_field_is_open_from_air_day_itself_under_the_on_air_sign(self):
        html = self.slot_html("2026-09-28", [essay(id="x2", scheduled_at="2026-09-30")])
        self.assertIn('id="live-x2"', html)
        self.assertIn('<span class="slot-onair" role="status">ON AIR</span>', html)

    def test_a_bad_link_error_sits_on_the_note_under_the_field(self):
        rows = [essay(id="x3", scheduled_at="2026-09-29")]
        html = run(
            "board.slotMarkup(board.slotModel('2026-09-28', { today: '2026-09-30', index: board.weekIndex("
            + js(rows) + "), shown: new Map(), settling: new Set() }), { errors: new Map([['x3', 'that is not a substack post link.']]) })"
        )
        self.assertIn('aria-invalid="true" aria-describedby="live-error-x3"', html)
        self.assertIn('id="live-error-x3" role="alert"', html)
        self.assertIn("that is not a substack post link.", html)

    def test_an_open_monday_names_the_date_a_drop_would_get(self):
        html = self.slot_html("2026-10-12", [])
        self.assertIn("drag an essay here", html)
        self.assertIn("drop to air mon oct 12", html)

    def test_the_candidate_shows_the_note_and_the_set_sentence(self):
        lifted = essay(id="up", title="Up Next", status="Writers Likey", scheduled_at="")
        html = self.slot_html("2026-10-12", [], markup_ctx={"lifted": lifted, "placing": True},
                              placing={"candidate": "2026-10-12"})
        self.assertIn("enter sets · mon oct 12", html)
        self.assertIn("is-candidate", html)
        self.assertIn('aria-label="mon oct 12, open. enter sets it here."', html)

    def test_a_week_with_two_shows_the_one_on_air_today_first(self):
        rows = [essay(id="first", scheduled_at="2026-09-29"), essay(id="today", scheduled_at="2026-09-30")]
        html = self.slot_html("2026-09-28", rows)
        self.assertIn('data-essay-id="today"', html)
        self.assertIn("2 of 2 this week", html)
        self.assertIn('data-pick="1"', html)

    def test_two_in_a_week_says_so(self):
        rows = [essay(id="a", scheduled_at="2026-10-05"), essay(id="b", scheduled_at="2026-10-07")]
        html = self.slot_html("2026-10-05", rows)
        self.assertIn("1 of 2 this week · show the other", html)

    def test_every_zone_is_a_named_focus_target(self):
        html = self.slot_html("2026-10-12", [])
        self.assertIn('class="slot-zone" tabindex="-1" role="group"', html)
        self.assertIn('aria-label="mon oct 12, open"', html)


class FeedbackRoleTests(unittest.TestCase):
    def test_green_is_polite_and_amber_and_red_interrupt(self):
        roles = run(f"(() => {{ const fb = require({js(str(ROOM / 'feedback.js'))}); return ['green', 'amber', 'red'].map(fb.roleFor); }})()")
        self.assertEqual(roles, ["status", "alert", "alert"])

    def test_green_fades_after_four_seconds(self):
        self.assertEqual(run(f"require({js(str(ROOM / 'feedback.js'))}).FADE_AFTER_MS"), 4000)


class CardHandleTests(unittest.TestCase):
    def markup(self, ctx=None, **fields):
        row = {
            "id": "c1", "title": "T", "status": "Writers Likey", "arrived_at": "2026-09-20T17:00:00+00:00",
            "starred_at": "", "scheduled_at": "", "excerpt": "", "word_count": 900, "note_pad": "sticky",
            "note_color": "pink", "totem_raw": "", "tags": [], "obsidian_url": "", "publish_readiness": {},
        }
        row.update(fields)
        return run(f"cards.cardMarkup({js(row)}, {js(ctx or {'now': '2026-09-30T12:00:00-07:00'})})")

    def test_a_likey_card_has_the_handle(self):
        html = self.markup()
        self.assertIn('class="card-handle" data-action="place" draggable="true" aria-pressed="false" aria-label="place on the board"', html)

    def test_room_and_scheduled_cards_have_none(self):
        self.assertNotIn("card-handle", self.markup(status="Writers Room"))
        self.assertNotIn("card-handle", self.markup(status="Ready for Air", scheduled_at="2026-10-05"))

    def test_placing_presses_the_handle_and_leaves_a_dashed_spot(self):
        html = self.markup({"now": "2026-09-30T12:00:00-07:00", "placing": True})
        self.assertIn('aria-pressed="true" aria-label="place on the board"', html)
        self.assertIn("card-postit-spot", html)
        self.assertNotIn("card-postit note", html)
        self.assertIn("is-placing", html)

    def test_each_card_has_a_place_for_its_plaque(self):
        self.assertIn('<div class="card-feedback"></div>', self.markup())


if __name__ == "__main__":
    unittest.main()
