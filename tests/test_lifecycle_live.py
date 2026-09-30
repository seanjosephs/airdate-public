"""The writers room lifecycle: Published is gone and Live means published.

Four phases: Writers Room -> Writers Likey -> Ready for Air -> Live.
"Published" survives only as a folder name on disk and as an alias when
reading old notes, never as a status the app writes or shows.

Old notes are read, never rewritten in bulk. Two rules do that work:
the Published/ folder still wins outright, and an old frontmatter "Live"
(which used to mean "draft sent") is re-read from the evidence in the note.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LifecycleTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["OBSIDIAN_ESSAYS_DIR"] = cls.tmp.name
        os.environ["AIR_DATE_DATA_DIR"] = str(Path(cls.tmp.name) / "runtime")
        sys.path.insert(0, str(ROOT))
        import server  # noqa: PLC0415
        cls.server = server

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class StatusVocabularyTests(LifecycleTestCase):
    def test_published_is_not_a_status(self):
        self.assertNotIn("Published", self.server.STATUS_SET)

    def test_the_four_phases_plus_archived_are(self):
        self.assertEqual(
            self.server.STATUS_SET,
            {"Writers Room", "Writers Likey", "Ready for Air", "Live", "Archived"},
        )

    def test_published_reads_as_live(self):
        self.assertEqual(self.server.normalize_status("Published"), "Live")

    def test_published_alias_is_case_insensitive(self):
        self.assertEqual(self.server.normalize_status("published"), "Live")

    def test_released_reads_as_live(self):
        self.assertEqual(self.server.normalize_status("released"), "Live")

    def test_live_is_still_live(self):
        self.assertEqual(self.server.normalize_status("Live"), "Live")

    def test_the_other_phases_are_untouched(self):
        for raw, expected in [
            ("", "Writers Room"),
            ("Writers Room", "Writers Room"),
            ("writers likey", "Writers Likey"),
            ("air date", "Ready for Air"),
            ("archived", "Archived"),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(self.server.normalize_status(raw), expected)


class EvidencePassTests(LifecycleTestCase):
    """Old "Live" meant "draft sent". It is re-read from what the note proves."""

    def effective(self, frontmatter, relative_path="Politics & AI/Note.md"):
        return self.server.effective_status(relative_path, frontmatter)

    def test_a_post_link_proves_live(self):
        self.assertEqual(
            self.effective({"status": "Live", "substack_url": "https://example.substack.com/p/x"}),
            "Live",
        )

    def test_a_draft_id_alone_is_not_live(self):
        self.assertEqual(
            self.effective({"status": "Live", "substack_draft_id": "216553077"}),
            "Ready for Air",
        )

    def test_no_evidence_at_all_is_not_live(self):
        # Nothing proves it went out, so airdate does not claim it did.
        self.assertEqual(self.effective({"status": "Live"}), "Ready for Air")

    def test_a_blank_url_is_not_evidence(self):
        self.assertEqual(
            self.effective({"status": "Live", "substack_url": "   "}),
            "Ready for Air",
        )

    def test_a_non_http_url_is_not_evidence(self):
        self.assertEqual(
            self.effective({"status": "Live", "substack_url": "draft-216553077"}),
            "Ready for Air",
        )

    def test_the_published_folder_still_wins_outright(self):
        # No frontmatter evidence needed: the folder is the proof.
        self.assertEqual(self.effective({}, "Published/Old Essay.md"), "Live")

    def test_the_published_folder_beats_a_working_status(self):
        self.assertEqual(
            self.effective({"status": "Writers Room"}, "Published/Old Essay.md"),
            "Live",
        )

    def test_the_archive_folder_still_wins(self):
        self.assertEqual(self.effective({}, "Archive/Old Essay.md"), "Archived")

    def test_the_evidence_pass_only_applies_to_live(self):
        # A Ready for Air essay with no link is still Ready for Air, not downgraded.
        self.assertEqual(self.effective({"status": "Ready for Air"}), "Ready for Air")


class ShelfTests(LifecycleTestCase):
    def classify(self, frontmatter, relative_path="Politics & AI/Note.md"):
        return self.server.classify_essay(relative_path, "Note", frontmatter)

    def test_live_with_a_link_is_on_the_shelf(self):
        self.assertEqual(
            self.classify({"status": "Live", "substack_url": "https://example.substack.com/p/x"}),
            "shelf",
        )

    def test_live_without_a_link_is_still_in_the_flow(self):
        self.assertEqual(self.classify({"status": "Live", "substack_draft_id": "1"}), "active")

    def test_an_old_published_note_stays_on_the_shelf(self):
        self.assertEqual(self.classify({"status": "Published"}), "shelf")

    def test_a_note_in_the_published_folder_is_on_the_shelf(self):
        self.assertEqual(self.classify({}, "Published/Old Essay.md"), "shelf")


class FolderTests(LifecycleTestCase):
    """The app's words change; the folders do not."""

    def folder(self, status, relative_path, frontmatter=None):
        return self.server.folder_for_status(status, frontmatter or {}, relative_path)

    def test_live_moves_into_the_published_folder(self):
        dest = self.folder("Live", "Politics & AI/Note.md")
        self.assertIsNotNone(dest)
        self.assertEqual(Path(dest).name, "Published")

    def test_a_live_note_already_there_does_not_move(self):
        self.assertIsNone(self.folder("Live", "Published/Note.md"))

    def test_archived_still_moves_into_archive(self):
        dest = self.folder("Archived", "Politics & AI/Note.md")
        self.assertIsNotNone(dest)
        self.assertEqual(Path(dest).name, "Archive")

    def test_a_working_status_pulls_a_note_back_out(self):
        dest = self.folder(
            "Writers Likey", "Published/Note.md", {"category": "Politics & AI"}
        )
        self.assertIsNotNone(dest)
        self.assertEqual(Path(dest).name, "Politics & AI")


class SchedulingGateTests(LifecycleTestCase):
    """A writers room essay cannot be scheduled. The server is the rule."""

    def test_the_room_refuses_with_a_showable_sentence(self):
        refusal = self.server.scheduling_refusal("Writers Room")
        self.assertIsInstance(refusal, str)
        self.assertTrue(refusal.strip())
        # House voice: one lowercase sentence that says what to do next.
        self.assertEqual(refusal, refusal.lower())
        self.assertIn("writers likey", refusal)

    def test_writers_likey_may_be_scheduled(self):
        self.assertIsNone(self.server.scheduling_refusal("Writers Likey"))

    def test_a_scheduled_essay_may_be_rescheduled(self):
        self.assertIsNone(self.server.scheduling_refusal("Ready for Air"))

    def test_an_absent_status_is_the_room_and_is_refused(self):
        self.assertIsNotNone(self.server.scheduling_refusal(""))


if __name__ == "__main__":
    unittest.main()
