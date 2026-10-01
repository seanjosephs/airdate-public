"""v0.6.1: words, docs and leftovers a QA pass on v0.6.0 found.

- The wizard describes the writers room, and its board step is called the
  board. The board legend names the configured publish day, and placing mode
  does not leave "<day> is open." behind once the note is set.
- Settings shows when the vault was indexed as a local date and time.
- SETUP.md matches the code it describes: ten wizard steps, three example tag
  presets, shipped totem art, the connected string the settings page shows,
  and every frontmatter key airdate writes.
- Comments describe the room as it is now. Dead code is gone.
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NODE = shutil.which("node")

if NODE is None:  # pragma: no cover - depends on the machine
    raise RuntimeError("node is required for tests/test_room_copy.py and was not found on PATH")


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def dates(expression: str, tz: str = "America/Los_Angeles"):
    script = f"""
const dates = require({json.dumps(str(ROOT / "static" / "room" / "dates.js"))});
process.stdout.write(JSON.stringify((() => {{ return {expression}; }})()));
"""
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True,
                               env=dict(os.environ, TZ=tz))
    return json.loads(completed.stdout)


class WizardCopyTests(unittest.TestCase):
    def test_the_welcome_describes_the_writers_room(self):
        html = read("room.html")
        welcome = html[html.index('data-step="welcome"'):html.index('data-step="vault"')]
        self.assertNotIn("catalog", welcome)
        self.assertIn("writers room", welcome)

    def test_the_board_step_is_called_the_board(self):
        html = read("room.html")
        step = re.search(r'<section class="wizard-step" data-step="calendar" data-label="([^"]+)"', html)
        self.assertIsNotNone(step)
        self.assertEqual(step.group(1), "board")

    def test_the_summary_calls_it_the_board_too(self):
        source = read("static/airdate-wizard.js")
        summary = source[source.index("function renderSummary"):source.index("async function renderConnect")]
        self.assertNotIn("'calendar'", summary)
        self.assertIn("['board',", summary)


class BoardLegendTests(unittest.TestCase):
    def test_the_legend_does_not_hard_code_monday(self):
        html = read("room.html")
        legend = html[html.index('id="board-legend"'):html.index('id="board-legend-say"')]
        self.assertNotIn("open mondays", legend)
        self.assertIn('id="board-legend-move"', legend)

    def test_the_legend_names_the_publish_day(self):
        source = read("static/room/board-view.js")
        chrome = source[source.index("function renderChrome"):source.index("function render()")]
        self.assertIn("move between open ${plural}", chrome)

    def test_setting_a_note_clears_the_last_announcement(self):
        source = read("static/room/placing.js")
        set_fn = source[source.index("async function set()"):source.index("function step(")]
        self.assertIn("view().announce('')", set_fn)


class IndexedAtTests(unittest.TestCase):
    def test_an_instant_reads_as_a_local_date_and_time(self):
        self.assertEqual(dates('dates.formatMoment("2026-10-01T12:35:29.481988+00:00")'), "thu oct 1, 5:35 am")
        self.assertEqual(dates('dates.formatMoment("2026-10-01T12:35:29+00:00")', tz="Europe/London"),
                         "thu oct 1, 1:35 pm")
        self.assertEqual(dates('dates.formatMoment("2026-10-01T07:05:00Z")'), "thu oct 1, 12:05 am")
        self.assertEqual(dates('dates.formatMoment("2026-10-01T19:00:00Z")'), "thu oct 1, 12:00 pm")

    def test_nothing_readable_is_empty(self):
        for value in ("", "yesterday", None):
            with self.subTest(value=value):
                self.assertEqual(dates(f"dates.formatMoment({json.dumps(value)})"), "")

    def test_settings_never_prints_the_raw_timestamp(self):
        source = read("static/room/settings.js")
        self.assertNotIn("${appStatus.scanned_at", source)
        self.assertIn("formatMoment(appStatus.scanned_at)", source)


class SetupDocTests(unittest.TestCase):
    def setUp(self):
        self.doc = read("SETUP.md")
        first_run = self.doc[self.doc.index("## First run"):self.doc.index("## Connect Substack")]
        self.steps = re.findall(r"^(\d+)\. \*\*([^*]+)\*\*", first_run, re.M)

    def test_the_wizard_steps_match_the_page(self):
        html = read("room.html")
        page_steps = re.findall(r'<section class="wizard-step" data-step="([^"]+)"', html)
        self.assertEqual(len(self.steps), len(page_steps))
        self.assertEqual([int(n) for n, _ in self.steps], list(range(1, len(page_steps) + 1)))
        intro = self.doc[self.doc.index("## First run"):self.doc.index("1. **Welcome**")]
        self.assertIn("ten steps", intro)
        names = [name.lower() for _, name in self.steps]
        self.assertTrue(any("metadata" in name for name in names), names)

    def test_tags_start_with_the_three_examples(self):
        import airdate_config  # noqa: PLC0415
        presets = airdate_config.DEFAULT_CONFIG["tag_presets"]
        self.assertEqual(len(presets), 3)
        tags = next(text for text in self.doc.split("\n\n") if "**Your tags**" in text)
        self.assertNotIn("starts empty", tags)
        for preset in presets:
            self.assertIn(preset["name"], tags)

    def test_totems_ship_real_art(self):
        totems = self.doc[self.doc.index("**Your totems**"):self.doc.index("**Your tags**")]
        self.assertNotIn("placeholder", totems)
        for key in ("fox", "octopus", "bison", "elephant", "phoenix"):
            self.assertTrue((ROOT / "static" / "totems" / f"{key}-512.webp").is_file())
            self.assertIn(key, totems.lower())

    def test_the_connected_string_is_the_one_settings_shows(self):
        source = read("static/room/settings.js")
        self.assertIn("substack connected", source)
        self.assertIn('"substack connected"', self.doc)
        self.assertNotIn("say it is connected through\nObsidian", self.doc)

    def test_the_frontmatter_list_is_every_key_airdate_writes(self):
        # Read from server.py's source, so the doc is checked against the code
        # without importing the server (which reads config at import).
        tree = ast.parse(read("server.py"))
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in ("ORDERED_FRONTMATTER_KEYS", "EDITOR_ALLOWED_FIELDS"):
                    values[node.targets[0].id] = ast.literal_eval(node.value)
        start = self.doc.index("- **Frontmatter.**")
        block = self.doc[start:self.doc.index("\n- **", start + 1)]
        listed = re.findall(r"`([a-z_]+)`", block)
        self.assertEqual(sorted(set(listed)), sorted(set(values["ORDERED_FRONTMATTER_KEYS"])),
                         "SETUP.md's frontmatter list and ORDERED_FRONTMATTER_KEYS differ")
        for key in values["EDITOR_ALLOWED_FIELDS"]:
            self.assertIn(key, listed, f"{key} is written through the editor but missing from SETUP.md")
        for key in ("previous_status", "archived_at", "note_pad", "note_color"):
            self.assertIn(key, listed)

    def test_the_bug_report_uses_the_rooms_words(self):
        template = read(".github/ISSUE_TEMPLATE/bug-report.yml")
        self.assertNotIn("catalog", template.lower())


class CurrentCommentsTests(unittest.TestCase):
    FILES = ("server.py", "room.html", "static/room/pool.js", "static/airdate-wizard.js", "static/room/cards.js",
             "static/room/feedback.js", "static/room/settings.js", "static/room/settings.css",
             "static/room/editor-view.js")

    def test_no_comment_describes_the_old_page_as_existing(self):
        for relative in self.FILES:
            with self.subTest(file=relative):
                self.assertNotIn("old page", read(relative).lower())

    def test_dead_code_is_gone(self):
        self.assertNotIn("isSettingsRoute", read("static/room/settings.js"))
        self.assertNotIn("/contact", read("server.py"))

    def test_the_favicon_is_cache_busted(self):
        self.assertRegex(read("room.html"), r'<link rel="icon" href="/static/brand/airdate-icon-256\.png\?v=\d+"')


if __name__ == "__main__":
    unittest.main()
