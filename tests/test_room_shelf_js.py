"""The shelf's pure functions, run through node like the card and board
helpers.

The two words that must not collide: "the shelf" is this whole screen (every
live essay); "on the shelf" is the current month; "the archive" is every
earlier month, grouped, newest first - a view, never the Archive/ folder
(that is rainy day, a different status entirely, covered in
test_room_shelf.py and test_room_cards_js.py). Every call runs in a fixed
time zone because "the current month" is a local calendar fact.
"""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "static" / "room" / "cards.js"
SHELF = ROOT / "static" / "room" / "shelf.js"
TZ = "America/Los_Angeles"
NODE = shutil.which("node")

if NODE is None:  # pragma: no cover - depends on the machine
    raise RuntimeError("node is required for tests/test_room_shelf_js.py and was not found on PATH")


def run(expression: str, now: str = "2026-09-30T12:00:00-07:00"):
    script = f"""
const cards = require({json.dumps(str(CARDS))});
globalThis.AirdateCards = cards;
const shelf = require({json.dumps(str(SHELF))});
const NOW = new Date({json.dumps(now)});
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
        "subtitle": "",
        "status": "Live",
        "published_date": "",
        "scheduled_at": "",
        "substack_url": "",
        "word_count": 900,
        "totem_raw": "",
        "tags": [],
        "excerpt": "",
        "search_blob": "",
        "obsidian_url": "obsidian://open?vault=v&file=Essays%2FA",
    }
    base.update(fields)
    return base


class AirDateOfTests(unittest.TestCase):
    def test_published_date_wins(self):
        e = essay(published_date="2026-09-21", scheduled_at="2026-09-14")
        self.assertEqual(run(f"shelf.airDateOf({js(e)})"), "2026-09-21")

    def test_falls_back_to_scheduled_at(self):
        e = essay(published_date="", scheduled_at="2026-09-14")
        self.assertEqual(run(f"shelf.airDateOf({js(e)})"), "2026-09-14")

    def test_blank_with_neither(self):
        self.assertEqual(run(f"shelf.airDateOf({js(essay())})"), "")


class MonthGroupingTests(unittest.TestCase):
    def test_month_key_and_label(self):
        self.assertEqual(run('shelf.monthKey("2026-07-13")'), "2026-07")
        self.assertEqual(run('shelf.monthLabel("2026-07")'), "july 2026")
        self.assertEqual(run('shelf.monthKey("not a date")'), "")

    def test_split_separates_current_month_from_the_archive(self):
        essays = [
            essay(id="this-month", published_date="2026-09-21"),
            essay(id="july", published_date="2026-07-13"),
            essay(id="june", published_date="2026-06-30"),
            essay(id="july-2", published_date="2026-07-07"),
        ]
        result = run(f"shelf.splitByMonth({js(essays)}, NOW)")
        self.assertEqual([e["id"] for e in result["onShelf"]], ["this-month"])
        self.assertEqual([g["key"] for g in result["archive"]], ["2026-07", "2026-06"])
        self.assertEqual(result["archive"][0]["label"], "july 2026")
        # Newest within the month first.
        self.assertEqual([e["id"] for e in result["archive"][0]["essays"]], ["july", "july-2"])

    def test_an_essay_with_no_date_stays_on_the_shelf(self):
        result = run(f"shelf.splitByMonth({js([essay(id='undated')])}, NOW)")
        self.assertEqual([e["id"] for e in result["onShelf"]], ["undated"])
        self.assertEqual(result["archive"], [])

    def test_archive_is_newest_month_first(self):
        essays = [essay(id="feb", published_date="2026-02-26"),
                  essay(id="may", published_date="2026-05-04")]
        result = run(f"shelf.splitByMonth({js(essays)}, NOW)")
        self.assertEqual([g["key"] for g in result["archive"]], ["2026-05", "2026-02"])


class YearTests(unittest.TestCase):
    def test_year_of(self):
        self.assertEqual(run(f"shelf.yearOf({js(essay(published_date='2026-07-13'))})"), "2026")
        self.assertEqual(run(f"shelf.yearOf({js(essay())})"), "")

    def test_distinct_years_sorted_newest_first(self):
        essays = [essay(published_date="2025-01-01"), essay(published_date="2026-07-13"),
                  essay(published_date="2025-12-31")]
        self.assertEqual(run(f"shelf.distinctYears({js(essays)})"), ["2026", "2025"])


class FilterTests(unittest.TestCase):
    PRESETS = [{"name": "Craft", "color": "#FF3B78", "tags": ["writing"]}]

    def test_search_covers_search_blob_and_excerpt(self):
        e = essay(search_blob="a title about ducks")
        self.assertTrue(run(f"shelf.matchesFilters({js(e)}, {js({'query': 'ducks'})}, [])"))
        self.assertFalse(run(f"shelf.matchesFilters({js(e)}, {js({'query': 'geese'})}, [])"))

    def test_totem_filter(self):
        e = essay(totem_raw="Fox")
        filters_set = {"totems": None}
        self.assertTrue(run(
            f"shelf.matchesFilters({js(e)}, {{...{js(filters_set)}, totems: new Set(['fox'])}}, [])"))
        self.assertFalse(run(
            f"shelf.matchesFilters({js(e)}, {{...{js(filters_set)}, totems: new Set(['bison'])}}, [])"))

    def test_topic_filter_from_tag_presets(self):
        e = essay(tags=["writing"])
        self.assertTrue(run(f"shelf.matchesFilters({js(e)}, {js({'topic': 'craft'})}, {js(self.PRESETS)})"))
        self.assertFalse(run(f"shelf.matchesFilters({js(e)}, {js({'topic': 'politics'})}, {js(self.PRESETS)})"))

    def test_year_filter(self):
        e = essay(published_date="2026-07-13")
        self.assertTrue(run(f"shelf.matchesFilters({js(e)}, {js({'year': '2026'})}, [])"))
        self.assertFalse(run(f"shelf.matchesFilters({js(e)}, {js({'year': '2025'})}, [])"))

    def test_distinct_topics_from_tag_presets(self):
        essays = [essay(tags=["writing"]), essay(tags=[])]
        self.assertEqual(run(f"shelf.distinctTopics({js(essays)}, {js(self.PRESETS)})"), ["craft"])


class RowMarkupTests(unittest.TestCase):
    def test_the_title_links_to_the_editor(self):
        html = run(f"shelf.rowMarkup({js(essay(id='xyz', title='Take <Care>'))}, {{}})")
        self.assertIn('href="/airdate?essay=xyz"', html)
        self.assertIn("Take &lt;Care&gt;", html)

    def test_a_subtitle_shows_under_the_title(self):
        html = run(f"shelf.rowMarkup({js(essay(subtitle='one line under'))}, {{}})")
        self.assertIn('class="shelf-subtitle">one line under<', html)

    def test_read_on_substack_only_for_a_real_post_link(self):
        live = run(f"shelf.rowMarkup({js(essay(substack_url='https://x.substack.com/p/y'))}, {{}})")
        self.assertIn("read on substack", live)
        none = run(f"shelf.rowMarkup({js(essay(substack_url=''))}, {{}})")
        self.assertNotIn("read on substack", none)

    def test_the_obsidian_link(self):
        html = run(f"shelf.rowMarkup({js(essay())}, {{}})")
        self.assertIn('aria-label="open A Title in obsidian"', html)

    def test_no_live_post_link_is_not_a_crash_and_shows_a_dash(self):
        html = run(f"shelf.rowMarkup({js(essay(substack_url='not a url'))}, {{}})")
        self.assertIn('class="shelf-muted">&mdash;</span>', html)


if __name__ == "__main__":
    unittest.main()


class TableHeaderTests(unittest.TestCase):
    """A data table needs column headers, or a screen reader reads each cell
    with no idea which column it is in (WCAG 1.3.1). The shelf is an ARIA
    table; it had rows and cells and no headers. The design draws no visible
    header row, so the headers are visually hidden."""

    MARKUP = "shelf.tableMarkup('on the shelf', [], {})"

    def test_the_first_row_is_seven_column_headers(self):
        heads = run(f"(() => {{ const m = {self.MARKUP}; return (m.match(/role=\"columnheader\"/g) || []).length; }})()")
        self.assertEqual(heads, 7)

    def test_the_header_row_comes_before_any_data_row(self):
        first = run(f"(() => {{ const m = {self.MARKUP}; return m.indexOf('role=\"columnheader\"') < m.indexOf('role=\"cell\"') || m.indexOf('role=\"cell\"') === -1; }})()")
        self.assertTrue(first)

    def test_the_header_row_is_visually_hidden_not_removed(self):
        hidden = run(f"(() => {{ const m = {self.MARKUP}; return /class=\"[^\"]*visually-hidden[^\"]*\"[^>]*>\\s*<div role=\"columnheader\"/.test(m); }})()")
        self.assertTrue(hidden)

    def test_the_headers_name_every_column(self):
        names = run(f"(() => {{ const m = {self.MARKUP}; return [...m.matchAll(/role=\"columnheader\"[^>]*>([^<]*)</g)].map(x => x[1]); }})()")
        self.assertEqual(names, ["totem", "title", "topic", "length", "went live", "post", "obsidian"])
