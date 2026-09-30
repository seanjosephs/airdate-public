"""Slice 3 server work: the board, going live, and "it did not air".

The board schedules through /ready-for-air, takes a note down through
/unschedule or /did-not-air, and marks an essay live through /publish. The
rules the server owns:

- a live link is checked by its shape and never fetched: https, a host that is
  substack.com, a subdomain of it, or the writer's own custom domain, and a
  path with a post under /p/;
- going live stamps the air date, not today;
- a live or rainy-day essay cannot be scheduled, and says why;
- a date that is not a calendar date is a bad request, not a refusal;
- the board's settings reach the browser.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_room_foundation import point_server_at  # noqa: E402

NOT_A_POST = "that is not a substack post link."


class BoardCase(unittest.TestCase):
    """A fresh vault and fresh sidecars for every test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "vault").mkdir()
        (root / "runtime").mkdir()
        os.environ["OBSIDIAN_ESSAYS_DIR"] = str(root / "vault")
        os.environ["AIR_DATE_DATA_DIR"] = str(root / "runtime")
        import server  # noqa: PLC0415
        self.server = server
        self.runtime = root / "runtime"
        self.vault = point_server_at(server, root)
        self._patches = [
            mock.patch.object(server, "ARRIVALS_LOG", self.runtime / "arrivals.json"),
            mock.patch.object(server, "STARRED_LOG", self.runtime / "starred.json"),
            mock.patch.object(server, "ESSAY_INDEX_FILE", self.runtime / "essay_index.json"),
            mock.patch.object(server, "SUBSTACK_PUBLICATION", ""),
        ]
        for patch in self._patches:
            patch.start()
        server._ARRIVALS_CACHE = None
        server._STARRED_CACHE = None
        server._essay_cache = None

    def tearDown(self):
        for patch in self._patches:
            patch.stop()
        self.server._ARRIVALS_CACHE = None
        self.server._STARRED_CACHE = None
        self.server._essay_cache = None
        self.server.load_and_apply_config()
        self.tmp.cleanup()

    def note(self, name, status=None, extra="", folder=""):
        lines = ["---", f'title: "{Path(name).stem}"']
        if status:
            lines.append(f'status: "{status}"')
        if extra:
            lines.append(extra)
        lines += ["---", "The first line of the page.", ""]
        target = self.vault / folder if folder else self.vault
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_text("\n".join(lines), encoding="utf-8")
        relative = f"{folder}/{name}" if folder else name
        essays, _ = self.server.refresh_essay_index()
        return next(r for r in essays if r["relative_path"] == relative)["id"]

    def frontmatter(self, relative):
        text = (self.vault / relative).read_text(encoding="utf-8")
        return self.server.split_frontmatter(text)[0]


class LiveLinkShapeTests(BoardCase):
    """Shape only. airdate never fetches the post, so it never claims live
    about something it cannot see."""

    def refusal(self, url, publication=""):
        with mock.patch.object(self.server, "SUBSTACK_PUBLICATION", publication):
            return self.server.live_link_refusal(url)

    def test_a_post_on_substack_com_is_a_post_link(self):
        self.assertIsNone(self.refusal("https://substack.com/p/a-post"))

    def test_a_post_on_a_substack_subdomain_is_a_post_link(self):
        self.assertIsNone(self.refusal("https://writer.substack.com/p/a-post"))

    def test_the_host_is_read_without_regard_to_case(self):
        self.assertIsNone(self.refusal("HTTPS://Writer.Substack.com/p/a-post"))

    def test_surrounding_space_is_ignored(self):
        self.assertIsNone(self.refusal("  https://writer.substack.com/p/a-post  "))

    def test_the_custom_domain_from_settings_counts(self):
        self.assertIsNone(self.refusal("https://example.com/p/a-post", "https://example.com"))

    def test_the_custom_domain_counts_with_www(self):
        self.assertIsNone(self.refusal("https://www.example.com/p/a-post", "https://example.com"))

    def test_the_custom_domain_counts_without_www_when_settings_have_it(self):
        self.assertIsNone(self.refusal("https://example.com/p/a-post", "https://www.example.com"))

    def test_the_custom_domain_counts_when_settings_have_no_scheme(self):
        self.assertIsNone(self.refusal("https://example.com/p/a-post", "example.com"))

    def test_a_query_or_fragment_does_not_matter(self):
        self.assertIsNone(self.refusal("https://writer.substack.com/p/a-post?utm_source=x#top"))

    def test_a_path_without_a_post_is_refused(self):
        self.assertEqual(self.refusal("https://writer.substack.com/about"), NOT_A_POST)

    def test_a_bare_p_with_no_post_is_refused(self):
        self.assertEqual(self.refusal("https://writer.substack.com/p/"), NOT_A_POST)

    def test_plain_http_is_refused(self):
        self.assertEqual(self.refusal("http://writer.substack.com/p/a-post"), NOT_A_POST)

    def test_a_lookalike_host_is_refused(self):
        self.assertEqual(self.refusal("https://substack.com.evil.test/p/a-post"), NOT_A_POST)

    def test_a_subdomain_lookalike_is_refused(self):
        self.assertEqual(self.refusal("https://writer.substack.com.evil.test/p/a-post"), NOT_A_POST)

    def test_a_host_that_only_ends_in_the_letters_is_refused(self):
        self.assertEqual(self.refusal("https://evilsubstack.com/p/a-post"), NOT_A_POST)

    def test_a_login_prefix_cannot_smuggle_the_host(self):
        self.assertEqual(self.refusal("https://substack.com@evil.test/p/a-post"), NOT_A_POST)

    def test_another_domain_is_refused_when_none_is_set(self):
        self.assertEqual(self.refusal("https://example.com/p/a-post"), NOT_A_POST)

    def test_a_subdomain_of_the_custom_domain_is_not_the_custom_domain(self):
        self.assertEqual(self.refusal("https://blog.example.com/p/a-post", "https://example.com"), NOT_A_POST)

    def test_a_draft_edit_link_is_not_a_post(self):
        self.assertEqual(self.refusal("https://writer.substack.com/publish/post/216553077"), NOT_A_POST)

    def test_nothing_and_not_a_url_are_refused(self):
        for value in ("", "   ", "writer.substack.com/p/a-post", "not a link", None):
            with self.subTest(value=value):
                self.assertEqual(self.refusal(value), NOT_A_POST)


class GoingLiveTests(BoardCase):
    GOOD = "https://writer.substack.com/p/a-post"

    def test_live_stamps_the_air_date_not_today(self):
        essay_id = self.note("Aired.md", "Ready for Air", 'scheduled_at: "2026-09-14"')
        self.server.publish_essay(essay_id, {"substack_url": self.GOOD})
        fm = self.frontmatter("Published/Aired.md")
        self.assertEqual(str(fm["published_date"]), "2026-09-14")

    def test_live_keeps_the_air_date(self):
        essay_id = self.note("Kept.md", "Ready for Air", 'scheduled_at: "2026-09-14"')
        self.server.publish_essay(essay_id, {"substack_url": self.GOOD})
        fm = self.frontmatter("Published/Kept.md")
        self.assertEqual(str(fm["scheduled_at"]), "2026-09-14")
        self.assertEqual(fm["substack_url"], self.GOOD)
        self.assertEqual(fm["status"], "Live")

    def test_an_air_date_with_a_time_stamps_its_calendar_day(self):
        essay_id = self.note("Timed.md", "Ready for Air", 'scheduled_at: "2026-09-14T09:00"')
        self.server.publish_essay(essay_id, {"substack_url": self.GOOD})
        self.assertEqual(str(self.frontmatter("Published/Timed.md")["published_date"]), "2026-09-14")

    def test_the_air_date_wins_over_a_date_in_the_request(self):
        essay_id = self.note("Asked.md", "Ready for Air", 'scheduled_at: "2026-09-14"')
        self.server.publish_essay(essay_id, {"substack_url": self.GOOD, "published_date": "2026-09-30"})
        self.assertEqual(str(self.frontmatter("Published/Asked.md")["published_date"]), "2026-09-14")

    def test_with_no_air_date_it_falls_back_to_today(self):
        essay_id = self.note("Unscheduled.md", "Writers Likey")
        self.server.publish_essay(essay_id, {"substack_url": self.GOOD})
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        self.assertEqual(str(self.frontmatter("Published/Unscheduled.md")["published_date"]), today)

    def test_a_link_that_is_not_a_post_is_refused_with_the_sentence(self):
        essay_id = self.note("Wrong.md", "Ready for Air", 'scheduled_at: "2026-09-14"')
        with self.assertRaises(ValueError) as caught:
            self.server.publish_essay(essay_id, {"substack_url": "https://writer.substack.com/about"})
        self.assertEqual(str(caught.exception), NOT_A_POST)
        self.assertEqual(self.frontmatter("Wrong.md")["status"], "Ready for Air")
        self.assertNotIn("substack_url", self.frontmatter("Wrong.md"))

    def test_the_custom_domain_is_read_from_settings(self):
        essay_id = self.note("Custom.md", "Ready for Air", 'scheduled_at: "2026-09-14"')
        with mock.patch.object(self.server, "SUBSTACK_PUBLICATION", "https://example.com"):
            self.server.publish_essay(essay_id, {"substack_url": "https://www.example.com/p/a-post"})
        self.assertEqual(self.frontmatter("Published/Custom.md")["status"], "Live")

    def test_the_result_carries_the_index_row(self):
        essay_id = self.note("Row.md", "Ready for Air", 'scheduled_at: "2026-09-14"')
        result = self.server.publish_essay(essay_id, {"substack_url": self.GOOD})
        self.assertEqual(result["row"]["status"], "Live")
        self.assertEqual(result["row"]["published_date"], "2026-09-14")


class SchedulingRefusalTests(BoardCase):
    def test_a_live_essay_cannot_be_scheduled(self):
        essay_id = self.note("On Shelf.md", "Live", 'substack_url: "https://writer.substack.com/p/x"')
        with self.assertRaises(self.server.RefusedError) as caught:
            self.server.schedule_essay(essay_id, {"scheduled_at": "2026-10-05"})
        self.assertEqual(caught.exception.payload["refused"], "live")
        sentence = caught.exception.payload["error"]
        self.assertEqual(sentence, sentence.lower())
        self.assertTrue(sentence.endswith("."))
        self.assertNotIn("scheduled_at", self.frontmatter("On Shelf.md"))

    def test_a_live_essay_in_the_published_folder_cannot_be_scheduled(self):
        essay_id = self.note("Old.md", folder="Published")
        with self.assertRaises(self.server.RefusedError) as caught:
            self.server.schedule_essay(essay_id, {"scheduled_at": "2026-10-05"})
        self.assertEqual(caught.exception.payload["refused"], "live")

    def test_a_rainy_day_essay_cannot_be_scheduled(self):
        essay_id = self.note("Parked.md", "Archived", folder="Archive")
        with self.assertRaises(self.server.RefusedError) as caught:
            self.server.schedule_essay(essay_id, {"scheduled_at": "2026-10-05"})
        self.assertEqual(caught.exception.payload["refused"], "archived")
        self.assertNotIn("scheduled_at", self.frontmatter("Archive/Parked.md"))

    def test_writers_room_is_still_refused_at_the_write(self):
        essay_id = self.note("Room.md", "Writers Room")
        with self.assertRaises(self.server.SchedulingRefusedError):
            self.server.schedule_essay(essay_id, {"scheduled_at": "2026-10-05"})

    def test_writers_likey_is_scheduled_and_the_row_comes_back(self):
        essay_id = self.note("Likey.md", "Writers Likey")
        result = self.server.schedule_essay(essay_id, {"scheduled_at": "2026-10-05"})
        self.assertEqual(result["status"], "Ready for Air")
        self.assertEqual(result["row"]["scheduled_at"], "2026-10-05")
        self.assertTrue(result["content_hash"])

    def test_a_scheduled_essay_may_move_to_another_week(self):
        essay_id = self.note("Moving.md", "Ready for Air", 'scheduled_at: "2026-10-05"')
        self.server.schedule_essay(essay_id, {"scheduled_at": "2026-10-12"})
        self.assertEqual(str(self.frontmatter("Moving.md")["scheduled_at"]), "2026-10-12")

    def test_a_date_and_time_is_accepted(self):
        essay_id = self.note("Timed.md", "Writers Likey")
        self.server.schedule_essay(essay_id, {"scheduled_at": "2026-10-05T09:30"})
        self.assertEqual(str(self.frontmatter("Timed.md")["scheduled_at"]), "2026-10-05T09:30")

    def test_a_malformed_date_is_a_bad_request(self):
        for value in ("next monday", "2026-13-01", "2026-02-30", "10/05/2026", "2026-10-5", "2026-10-05 then"):
            with self.subTest(value=value):
                essay_id = self.note("Bad.md", "Writers Likey")
                with self.assertRaises(ValueError):
                    self.server.schedule_essay(essay_id, {"scheduled_at": value})
                self.assertNotIn("scheduled_at", self.frontmatter("Bad.md"))

    def test_a_missing_date_is_a_bad_request(self):
        essay_id = self.note("Missing.md", "Writers Likey")
        with self.assertRaises(ValueError):
            self.server.schedule_essay(essay_id, {})

    def test_a_stale_hash_is_a_file_changed_conflict(self):
        essay_id = self.note("Stale.md", "Writers Likey")
        with self.assertRaises(self.server.EssayConflictError):
            self.server.schedule_essay(essay_id, {"scheduled_at": "2026-10-05", "expected_content_hash": "0" * 64})


class DidNotAirTests(BoardCase):
    def test_it_clears_the_air_date_and_goes_back_to_writers_likey(self):
        essay_id = self.note("Missed.md", "Ready for Air", 'scheduled_at: "2026-09-14"')
        result = self.server.did_not_air(essay_id, {})
        fm = self.frontmatter("Missed.md")
        self.assertEqual(fm["status"], "Writers Likey")
        self.assertNotIn("scheduled_at", fm)
        self.assertEqual(result["row"]["status"], "Writers Likey")
        self.assertEqual(result["row"]["scheduled_at"], "")

    def test_it_keeps_the_sent_marker(self):
        essay_id = self.note(
            "Sent.md", "Ready for Air",
            'scheduled_at: "2026-09-14"\nsubstack_draft_url: "https://writer.substack.com/publish/post/1"',
        )
        self.server.did_not_air(essay_id, {})
        self.assertEqual(self.frontmatter("Sent.md")["substack_draft_url"], "https://writer.substack.com/publish/post/1")

    def test_an_essay_that_is_not_on_the_board_is_refused(self):
        for name, status, folder in (("Likey.md", "Writers Likey", ""), ("Room.md", "Writers Room", ""),
                                     ("Shelf.md", None, "Published")):
            with self.subTest(status=status or "Live"):
                essay_id = self.note(name, status, folder=folder)
                with self.assertRaises(self.server.RefusedError) as caught:
                    self.server.did_not_air(essay_id, {})
                self.assertEqual(caught.exception.payload["refused"], "not-on-the-board")


class UnscheduleUndoTests(BoardCase):
    """Undo of a schedule is an unschedule that names the file it expects, so a
    stale or doubled replay is a conflict instead of an overwrite."""

    def test_an_unschedule_with_the_schedule_hash_goes_through(self):
        essay_id = self.note("Undo.md", "Writers Likey")
        scheduled = self.server.schedule_essay(essay_id, {"scheduled_at": "2026-10-05"})
        self.server.set_essay_status(scheduled["new_id"], "Writers Likey", {"scheduled_at": ""},
                                     None, scheduled["content_hash"])
        self.assertNotIn("scheduled_at", self.frontmatter("Undo.md"))

    def test_a_doubled_undo_is_a_conflict(self):
        essay_id = self.note("Twice.md", "Writers Likey")
        scheduled = self.server.schedule_essay(essay_id, {"scheduled_at": "2026-10-05"})
        self.server.set_essay_status(scheduled["new_id"], "Writers Likey", {"scheduled_at": ""},
                                     None, scheduled["content_hash"])
        with self.assertRaises(self.server.EssayConflictError):
            self.server.set_essay_status(scheduled["new_id"], "Writers Likey", {"scheduled_at": ""},
                                         None, scheduled["content_hash"])


class BoardConfigTests(BoardCase):
    def test_the_board_reaches_the_browser(self):
        board = self.server.ui_config_payload()["board"]
        self.assertEqual(board, {"weeks_shown": 3, "default_pad": "sticky", "default_color": "canary"})

    def test_the_board_follows_settings(self):
        with mock.patch.object(self.server, "BOARD_WEEKS_SHOWN", 6), \
                mock.patch.object(self.server, "BOARD_DEFAULT_PAD", "index"), \
                mock.patch.object(self.server, "BOARD_DEFAULT_COLOR", "green"):
            board = self.server.ui_config_payload()["board"]
        self.assertEqual(board, {"weeks_shown": 6, "default_pad": "index", "default_color": "green"})


class BoardHttpTests(BoardCase):
    """The routes on the wire: status codes and the refusal shape."""

    def setUp(self):
        super().setUp()

        class QuietHandler(self.server.Handler):
            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def post(self, path, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read())

    def test_scheduling_a_live_essay_answers_409_refused(self):
        essay_id = self.note("Wire Live.md", "Live", 'substack_url: "https://writer.substack.com/p/x"')
        status, body = self.post(f"/api/essays/{essay_id}/ready-for-air", {"scheduled_at": "2026-10-05"})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["refused"], "live")
        self.assertNotIn("reason", body, "a refusal is not a file-changed conflict")

    def test_a_malformed_date_answers_400(self):
        essay_id = self.note("Wire Bad.md", "Writers Likey")
        status, body = self.post(f"/api/essays/{essay_id}/ready-for-air", {"scheduled_at": "2026-02-30"})
        self.assertEqual(status, 400, body)
        self.assertNotIn("refused", body)

    def test_a_stale_hash_answers_409_file_changed_without_a_refusal(self):
        essay_id = self.note("Wire Stale.md", "Writers Likey")
        status, body = self.post(f"/api/essays/{essay_id}/ready-for-air",
                                 {"scheduled_at": "2026-10-05", "expected_content_hash": "0" * 64})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["reason"], "file_changed")
        self.assertNotIn("refused", body)

    def test_scheduling_answers_200_with_the_row(self):
        essay_id = self.note("Wire Ok.md", "Writers Likey")
        status, body = self.post(f"/api/essays/{essay_id}/ready-for-air", {"scheduled_at": "2026-10-05"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["row"]["status"], "Ready for Air")

    def test_unschedule_answers_with_the_row(self):
        essay_id = self.note("Wire Down.md", "Ready for Air", 'scheduled_at: "2026-10-05"')
        status, body = self.post(f"/api/essays/{essay_id}/unschedule", {})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["row"]["status"], "Writers Likey")

    def test_did_not_air_answers_200_and_refuses_off_the_board(self):
        essay_id = self.note("Wire Missed.md", "Ready for Air", 'scheduled_at: "2026-09-14"')
        status, body = self.post(f"/api/essays/{essay_id}/did-not-air", {})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["row"]["status"], "Writers Likey")
        status, body = self.post(f"/api/essays/{essay_id}/did-not-air", {})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["refused"], "not-on-the-board")

    def test_a_bad_live_link_answers_400_with_the_sentence(self):
        essay_id = self.note("Wire Link.md", "Ready for Air", 'scheduled_at: "2026-09-14"')
        status, body = self.post(f"/api/essays/{essay_id}/publish", {"substack_url": "https://substack.com.evil.test/p/x"})
        self.assertEqual(status, 400, body)
        self.assertEqual(body["error"], NOT_A_POST)

    def test_a_good_live_link_answers_200_with_the_row(self):
        essay_id = self.note("Wire Live Ok.md", "Ready for Air", 'scheduled_at: "2026-09-14"')
        status, body = self.post(f"/api/essays/{essay_id}/publish", {"substack_url": "https://writer.substack.com/p/x"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["row"]["status"], "Live")
        self.assertEqual(body["row"]["published_date"], "2026-09-14")


if __name__ == "__main__":
    unittest.main()
