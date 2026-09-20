"""A frontmatter key that appears twice must not survive an edit to that key.

Sean unscheduled an essay and had to do it twice. A note carrying
`scheduled_at` twice reads as its last copy; the surgical editor only knew the
last copy of a repeated key, so one unschedule deleted that one and left the
earlier copy, and the essay stayed scheduled for a different week.

airdate does not write duplicates itself. They arrive from outside (a hand
edit, an AI filling frontmatter), so the editor has to cope with them: editing
a key leaves exactly one copy, clearing it leaves none, and keys that are not
being changed are left exactly as found.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

TWICE = (
    '---\n'
    'title: "Probe"\n'
    'scheduled_at: "2026-09-21"\n'
    'status: "Ready for Air"\n'
    'scheduled_at: "2026-09-28"\n'
    '---\n'
    '\n'
    'body\n'
)


def scheduled(text: str):
    return server.split_frontmatter(text)[0].get("scheduled_at")


class EditingARepeatedKeyTests(unittest.TestCase):
    def test_one_unschedule_clears_every_copy(self):
        after = server.apply_frontmatter_edits(TWICE, {"scheduled_at": {"before": "2026-09-28", "after": ""}})
        self.assertIsNone(scheduled(after), "one unschedule left the essay scheduled")
        self.assertNotIn("scheduled_at", after)

    def test_rescheduling_leaves_exactly_one_copy(self):
        after = server.apply_frontmatter_edits(TWICE, {"scheduled_at": {"before": "2026-09-28", "after": "2026-10-05"}})
        self.assertEqual(after.count("scheduled_at:"), 1)
        self.assertEqual(str(scheduled(after)), "2026-10-05")

    def test_untouched_lines_and_body_are_verbatim(self):
        after = server.apply_frontmatter_edits(TWICE, {"scheduled_at": {"before": "2026-09-28", "after": ""}})
        self.assertEqual(after, '---\ntitle: "Probe"\nstatus: "Ready for Air"\n---\n\nbody\n')

    def test_a_repeated_key_that_is_not_being_changed_is_left_alone(self):
        after = server.apply_frontmatter_edits(TWICE, {"status": {"before": "Ready for Air", "after": "Writers Likey"}})
        self.assertEqual(after.count("scheduled_at:"), 2, "airdate rewrote a key it was not asked to change")
        self.assertIn('scheduled_at: "2026-09-21"\n', after)
        self.assertIn('scheduled_at: "2026-09-28"\n', after)

    def test_a_repeated_block_list_is_collapsed_whole(self):
        text = '---\ntags:\n  - one\n  - two\ntitle: "P"\ntags:\n  - three\n---\n\nb\n'
        after = server.apply_frontmatter_edits(text, {"tags": {"before": ["three"], "after": ["four"]}})
        self.assertEqual(after.count("tags:"), 1)
        self.assertNotIn("- one", after)
        self.assertNotIn("- three", after)
        self.assertIn('- "four"', after)


class ReportingRepeatedKeysTests(unittest.TestCase):
    def test_names_each_repeated_key_once(self):
        self.assertEqual(server.duplicated_frontmatter_keys(TWICE), ["scheduled_at"])

    def test_a_clean_note_reports_nothing(self):
        self.assertEqual(server.duplicated_frontmatter_keys('---\ntitle: "P"\ntags:\n  - a\n  - b\n---\n\nb\n'), [])

    def test_a_note_without_frontmatter_reports_nothing(self):
        self.assertEqual(server.duplicated_frontmatter_keys("just a body\n"), [])

    def test_a_key_shaped_line_in_the_body_is_not_counted(self):
        self.assertEqual(server.duplicated_frontmatter_keys('---\ntitle: "P"\n---\n\ntitle: not frontmatter\n'), [])


if __name__ == "__main__":
    unittest.main()
