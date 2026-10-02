"""v0.6.1: what a QA pass on v0.6.0 found in the server.

- Filing a note (intake-apply) sorts it into a topic folder and keeps the
  phase it had: a starred essay stays starred, a scheduled one stays on the
  board. Only a note with no status at all becomes writers room.
- One write lock: a check-and-write is atomic, so six saves naming the same
  file state give one success and five conflicts, never four silent winners.
- The secondary routes hold the same rules as the room: unschedule needs an
  essay on the board, /set-status is gone, /publish will not take a rainy-day
  essay and clears its parking stamps when it goes live.
- A post link is one post: a /p/<slug> path on a real host.
- Malformed input is a 400 with a sentence, never a 500 or a skipped guard.
- No error message carries an absolute path. HEAD works on pages and static
  files. A file that vanishes mid-scan is skipped, not reported unparseable.
"""

import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_room_foundation import point_server_at  # noqa: E402

NOT_A_POST = "that is not a substack post link."
HASH = "a" * 64


class RoomCase(unittest.TestCase):
    """A fresh vault and fresh sidecars for every test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
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
        # A background rescan still writing the index would race the cleanup.
        deadline = time.monotonic() + 10
        while self.server._essay_scan_inflight and time.monotonic() < deadline:
            time.sleep(0.01)
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

    def row(self, essay_id):
        self.server.refresh_essay_index()
        return self.server.index_row_for(essay_id)


class HttpCase(RoomCase):
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
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read())

    def assert_no_absolute_path(self, body):
        message = json.dumps(body.get("error", ""))
        for root in {str(self.root), str(self.root.resolve()), str(self.vault)}:
            self.assertNotIn(root, message, f"an error message carries a path: {message}")


# ---- 1. filing keeps the phase ---------------------------------------------


class FilingKeepsThePhaseTests(RoomCase):
    def file(self, essay_id, **payload):
        payload.setdefault("category", "Craft")
        return self.server.intake_apply(essay_id, payload)

    def test_a_writers_likey_essay_stays_likey_with_its_star(self):
        essay_id = self.note("Starred.md", "Writers Room")
        starred = self.server.star_essay(essay_id, {"starred": True})
        before = self.row(starred["new_id"])
        self.assertTrue(before.get("starred_at"))
        result = self.file(starred["new_id"])
        row = self.row(result["new_id"])
        self.assertEqual(row["relative_path"], "Craft/Starred.md")
        self.assertEqual(row["status"], "Writers Likey")
        self.assertEqual(row.get("starred_at"), before["starred_at"])
        self.assertEqual(self.frontmatter("Craft/Starred.md")["status"], "Writers Likey")

    def test_a_ready_for_air_essay_keeps_its_air_date_and_stays_on_the_board(self):
        essay_id = self.note("Scheduled.md", "Ready for Air", 'scheduled_at: "2026-10-05"')
        result = self.file(essay_id)
        fm = self.frontmatter("Craft/Scheduled.md")
        self.assertEqual(fm["scheduled_at"], "2026-10-05")
        self.assertEqual(fm["status"], "Ready for Air")
        self.assertEqual(self.row(result["new_id"])["status"], "Ready for Air")

    def test_every_lifecycle_field_survives(self):
        extra = "\n".join([
            'scheduled_at: "2026-10-05"',
            'substack_draft_id: "123"',
            'substack_draft_url: "https://writer.substack.com/publish/post/123"',
        ])
        essay_id = self.note("Sent.md", "Ready for Air", extra)
        self.file(essay_id)
        fm = self.frontmatter("Craft/Sent.md")
        self.assertEqual(fm["substack_draft_id"], "123")
        self.assertEqual(fm["substack_draft_url"], "https://writer.substack.com/publish/post/123")
        self.assertEqual(fm["scheduled_at"], "2026-10-05")

    def test_a_note_with_no_status_becomes_writers_room(self):
        essay_id = self.note("Bare.md")
        self.assertNotIn("status", self.frontmatter("Bare.md"))
        result = self.file(essay_id)
        self.assertEqual(self.frontmatter("Craft/Bare.md")["status"], "Writers Room")
        self.assertEqual(self.row(result["new_id"])["status"], "Writers Room")

    def test_a_note_with_no_frontmatter_at_all_becomes_writers_room(self):
        (self.vault / "Loose.md").write_text("Just words.\n", encoding="utf-8")
        essays, _ = self.server.refresh_essay_index()
        essay_id = next(r for r in essays if r["relative_path"] == "Loose.md")["id"]
        self.file(essay_id)
        fm = self.frontmatter("Craft/Loose.md")
        self.assertEqual(fm["status"], "Writers Room")
        self.assertEqual(fm["category"], "Craft")

    def test_the_category_and_totem_are_written(self):
        essay_id = self.note("Topic.md", "Writers Likey")
        self.file(essay_id, category="Craft")
        self.assertEqual(self.frontmatter("Craft/Topic.md")["category"], "Craft")

    def test_a_supplied_status_does_not_override_the_phase_a_note_has(self):
        essay_id = self.note("Old Client.md", "Writers Likey")
        self.file(essay_id, status="Writers Room")
        self.assertEqual(self.frontmatter("Craft/Old Client.md")["status"], "Writers Likey")

    def test_a_supplied_status_names_the_phase_of_a_note_with_none(self):
        essay_id = self.note("Fresh.md")
        self.file(essay_id, status="Writers Likey")
        self.assertEqual(self.frontmatter("Craft/Fresh.md")["status"], "Writers Likey")

    def test_a_supplied_status_outside_the_room_is_a_bad_request(self):
        for status in ("Ready for Air", "Live", "Archived", "Published", "nonsense"):
            with self.subTest(status=status):
                essay_id = self.note(f"Bad {status}.md", "Writers Room")
                with self.assertRaises(ValueError):
                    self.file(essay_id, status=status)
                self.assertTrue((self.vault / f"Bad {status}.md").exists(), "a refused filing moved the note")

    def test_a_live_essay_is_refused(self):
        essay_id = self.note("Gone Out.md", "Live", 'substack_url: "https://writer.substack.com/p/x"', folder="Published")
        with self.assertRaises(self.server.RefusedError) as caught:
            self.file(essay_id)
        self.assertEqual(caught.exception.payload["refused"], "live")
        self.assertTrue((self.vault / "Published" / "Gone Out.md").exists())

    def test_an_archived_essay_is_refused(self):
        essay_id = self.note("Parked.md", "Archived", folder="Archive")
        with self.assertRaises(self.server.RefusedError) as caught:
            self.file(essay_id)
        self.assertEqual(caught.exception.payload["refused"], "archived")
        self.assertTrue((self.vault / "Archive" / "Parked.md").exists())


class FilingRouteTests(HttpCase):
    def test_a_refusal_answers_409_with_a_sentence(self):
        essay_id = self.note("Parked.md", "Archived", folder="Archive")
        status, body = self.post(f"/api/essays/{essay_id}/intake-apply", {"category": "Craft"})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["refused"], "archived")
        self.assertTrue(body["error"].endswith("."))

    def test_a_bad_status_answers_400(self):
        essay_id = self.note("Bad.md", "Writers Room")
        status, body = self.post(f"/api/essays/{essay_id}/intake-apply", {"category": "Craft", "status": "Live"})
        self.assertEqual(status, 400, body)

    def test_filing_a_likey_through_the_route_keeps_it_likey(self):
        essay_id = self.note("Likey.md", "Writers Likey")
        status, body = self.post(f"/api/essays/{essay_id}/intake-apply", {"category": "Craft", "totem": ""})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["essay"]["status"], "Writers Likey")


class PoolFilingClientTests(unittest.TestCase):
    def test_the_pool_does_not_send_a_status_when_filing(self):
        source = (ROOT / "static" / "room" / "pool.js").read_text(encoding="utf-8")
        start = source.index("/intake-apply")
        call = source[start:source.index("});", start)]
        self.assertNotIn("status", call)


# ---- 2. one write lock -----------------------------------------------------


def slow_writes(server, delay=0.05):
    """Widen the window between the check and the write, so a missing lock
    shows every time rather than now and then."""
    real = server.atomic_write_text

    def slow(path, value):
        time.sleep(delay)
        return real(path, value)

    return mock.patch.object(server, "atomic_write_text", slow)


class WriteLockTests(HttpCase):
    THREADS = 6

    def race(self, path, payloads):
        barrier = threading.Barrier(len(payloads))
        results = [None] * len(payloads)

        def go(index):
            barrier.wait()
            results[index] = self.post(path, payloads[index])

        threads = [threading.Thread(target=go, args=(i,)) for i in range(len(payloads))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        return results

    def test_simultaneous_saves_naming_one_state_give_exactly_one_winner(self):
        essay_id = self.note("Race.md", "Writers Room", 'airdate_uid: "0123456789abcdef0123456789abcdef"')
        detail = self.server.get_essay_detail(essay_id)
        payloads = [
            {"updates": {"summary": f"writer {i}"}, "expected_content_hash": detail["content_hash"]}
            for i in range(self.THREADS)
        ]
        with slow_writes(self.server):
            results = self.race(f"/api/essays/{essay_id}/save", payloads)
        statuses = sorted(status for status, _ in results)
        self.assertEqual(statuses, [200] + [409] * (self.THREADS - 1), results)
        winner = next(body for status, body in results if status == 200)
        self.assertEqual(self.frontmatter("Race.md")["summary"], winner["saved_frontmatter"]["summary"])
        for status, body in results:
            if status == 409:
                self.assertEqual(body["reason"], "file_changed")

    def test_simultaneous_parks_of_an_unstamped_note_all_answer_with_the_minted_id(self):
        essay_id = self.note("No Uid.md", "Writers Room")
        self.assertNotIn("airdate_uid", self.frontmatter("No Uid.md"))
        with slow_writes(self.server):
            results = self.race(f"/api/essays/{essay_id}/archive", [{}] * 4)
        for status, body in results:
            self.assertEqual(status, 200, body)
            self.assert_no_absolute_path(body)
        uid = self.frontmatter("Archive/No Uid.md")["airdate_uid"]
        self.assertEqual({body["new_id"] for _, body in results}, {uid})
        self.assertEqual(sum(1 for _, body in results if body.get("changed") is False), 3)

    def test_a_note_that_vanishes_mid_request_is_a_404_without_its_path(self):
        essay_id = self.note("Vanishing.md", "Writers Room")
        missing = self.vault / "Elsewhere" / "Vanishing.md"
        with mock.patch.object(self.server, "resolve_essay_path", return_value=missing):
            status, body = self.post(f"/api/essays/{essay_id}/save", {"updates": {"summary": "x"}})
        self.assertEqual(status, 404, body)
        self.assert_no_absolute_path(body)

    def test_an_unexpected_failure_does_not_carry_a_path(self):
        essay_id = self.note("Broken.md", "Writers Room")
        target = str(self.vault / "Broken.md")
        with mock.patch.object(self.server, "save_essay_updates",
                               side_effect=PermissionError(13, "Permission denied", target)):
            status, body = self.post(f"/api/essays/{essay_id}/save", {"updates": {"summary": "x"}})
        self.assertEqual(status, 500, body)
        self.assert_no_absolute_path(body)

    def test_the_lock_is_reentrant_and_shared(self):
        lock = self.server.VAULT_WRITE_LOCK
        with lock:
            with lock:
                pass
        for name in ("save_essay_updates", "set_essay_status", "park_essay", "back_to_room",
                     "intake_apply", "publish_essay", "schedule_essay", "unschedule_essay",
                     "did_not_air", "star_essay", "create_linked_draft", "attach_hero_image_to_essay"):
            with self.subTest(name=name):
                self.assertTrue(getattr(getattr(self.server, name), "__vault_write__", False),
                                f"{name} writes the vault without the lock")


# ---- 3. guards on the secondary routes -------------------------------------


class UnscheduleGuardTests(HttpCase):
    def test_only_an_essay_on_the_board_can_be_unscheduled(self):
        cases = (
            ("Room.md", "Writers Room", "", ""),
            ("Likey.md", "Writers Likey", "", ""),
            ("Shelf.md", "Live", 'substack_url: "https://writer.substack.com/p/x"', "Published"),
            ("Rainy.md", "Archived", "", "Archive"),
        )
        for name, status, extra, folder in cases:
            with self.subTest(status=status):
                essay_id = self.note(name, status, extra, folder=folder)
                code, body = self.post(f"/api/essays/{essay_id}/unschedule", {})
                self.assertEqual(code, 409, body)
                self.assertIn("refused", body)
                relative = f"{folder}/{name}" if folder else name
                self.assertTrue((self.vault / relative).exists(), "a refused unschedule moved the note")

    def test_an_essay_on_the_board_comes_off(self):
        essay_id = self.note("Board.md", "Ready for Air", 'scheduled_at: "2026-10-05"')
        code, body = self.post(f"/api/essays/{essay_id}/unschedule", {})
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "Writers Likey")
        self.assertNotIn("scheduled_at", self.frontmatter("Board.md"))

    def test_the_undo_of_a_schedule_still_works(self):
        essay_id = self.note("Undo.md", "Writers Likey")
        code, scheduled = self.post(f"/api/essays/{essay_id}/ready-for-air", {"scheduled_at": "2026-10-05"})
        self.assertEqual(code, 200, scheduled)
        code, body = self.post(f"/api/essays/{scheduled['new_id']}/unschedule",
                               {"expected_content_hash": scheduled["content_hash"]})
        self.assertEqual(code, 200, body)
        self.assertEqual(body["status"], "Writers Likey")


class SetStatusRouteIsGoneTests(HttpCase):
    def test_set_status_answers_404_and_changes_nothing(self):
        essay_id = self.note("Old Route.md", "Writers Room")
        code, _ = self.post(f"/api/essays/{essay_id}/set-status", {"status": "Live"})
        self.assertEqual(code, 404)
        self.assertEqual(self.frontmatter("Old Route.md")["status"], "Writers Room")

    def test_the_contact_route_is_gone(self):
        essay_id = self.note("Contact.md", "Writers Room")
        code, _ = self.post(f"/api/essays/{essay_id}/contact", {"action": "open"})
        self.assertEqual(code, 404)

    def test_the_dead_helpers_are_gone(self):
        for name in ("track_contact", "load_contact_log", "save_contact_log", "CONTACT_LOG",
                     "env_flag", "unique_upload_path", "_tail"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(self.server, name))


class PublishGuardTests(HttpCase):
    LINK = "https://writer.substack.com/p/a-post"

    def test_a_rainy_day_essay_cannot_go_live(self):
        essay_id = self.note("Rainy.md", "Archived", 'previous_status: "Writers Likey"', folder="Archive")
        code, body = self.post(f"/api/essays/{essay_id}/publish", {"substack_url": self.LINK})
        self.assertEqual(code, 409, body)
        self.assertEqual(body["refused"], "archived")
        self.assertTrue((self.vault / "Archive" / "Rainy.md").exists())

    def test_going_live_clears_the_parking_stamps(self):
        extra = 'scheduled_at: "2026-09-14"\nprevious_status: "Writers Likey"\narchived_at: "2026-09-01T00:00:00+00:00"'
        essay_id = self.note("Stamped.md", "Ready for Air", extra)
        code, body = self.post(f"/api/essays/{essay_id}/publish", {"substack_url": self.LINK})
        self.assertEqual(code, 200, body)
        fm = self.frontmatter("Published/Stamped.md")
        self.assertNotIn("previous_status", fm)
        self.assertNotIn("archived_at", fm)
        self.assertEqual(fm["substack_url"], self.LINK)

    def test_every_way_to_live_clears_the_parking_stamps(self):
        extra = 'previous_status: "Writers Likey"\narchived_at: "2026-09-01T00:00:00+00:00"'
        essay_id = self.note("Any Way.md", "Ready for Air", extra)
        self.server.save_essay_updates(essay_id, {"status": "Live", "substack_url": self.LINK})
        fm = self.frontmatter("Any Way.md")
        self.assertEqual(fm["status"], "Live")
        self.assertNotIn("previous_status", fm)
        self.assertNotIn("archived_at", fm)


# ---- 6. the shape of a post link -------------------------------------------


class PostLinkShapeTests(RoomCase):
    def refusal(self, url, publication=""):
        with mock.patch.object(self.server, "SUBSTACK_PUBLICATION", publication):
            return self.server.live_link_refusal(url)

    def test_one_post_is_a_post_link(self):
        for url in (
            "https://writer.substack.com/p/a-post",
            "https://writer.substack.com/p/a-post/",
            "https://writer.substack.com/p/a-post?utm_source=x#top",
            "https://substack.com/p/a-post",
            "https://my-writer.substack.com/p/a-post",
        ):
            with self.subTest(url=url):
                self.assertIsNone(self.refusal(url))

    def test_two_links_glued_together_are_refused(self):
        for url in (
            "https://example.substack.com/p/ahttps://other.substack.com/p/b",
            "https://example.substack.com/p/a https://other.substack.com/p/b",
            "https://example.substack.com/p/a/https://other.substack.com/p/b",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.refusal(url), NOT_A_POST)

    def test_a_host_that_is_not_dns_labels_is_refused(self):
        for url in (
            "https://.substack.com/p/a",
            "https://evil.com%2F.substack.com/p/a",
            "https://a..substack.com/p/a",
            "https://a_b.substack.com/p/a",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.refusal(url), NOT_A_POST)

    def test_a_post_path_must_start_at_p(self):
        self.assertEqual(self.refusal("https://writer.substack.com/x/p/a-post"), NOT_A_POST)
        self.assertEqual(self.refusal("https://writer.substack.com/p/a/comments"), NOT_A_POST)


# ---- 7. input validation ---------------------------------------------------


class InputValidationTests(HttpCase):
    def test_a_time_that_does_not_exist_is_a_bad_request(self):
        essay_id = self.note("Clock.md", "Writers Likey")
        for value in ("2026-10-05T25:00", "2026-10-05T09:60", "2026-10-05T09:00:61", "2026-10-05T09:00+25:00"):
            with self.subTest(value=value):
                code, body = self.post(f"/api/essays/{essay_id}/ready-for-air", {"scheduled_at": value})
                self.assertEqual(code, 400, body)
        code, body = self.post(f"/api/essays/{essay_id}/ready-for-air", {"scheduled_at": "2026-10-05T23:59"})
        self.assertEqual(code, 200, body)

    def test_a_malformed_expected_state_is_a_bad_request_not_a_skipped_guard(self):
        essay_id = self.note("Guard.md", "Writers Room")
        for payload in (
            {"expected_mtime": "yesterday"},
            {"expected_mtime": [1]},
            {"expected_mtime": True},
            {"expected_content_hash": "abc"},
            {"expected_content_hash": 12},
            {"expected_content_hash": "Z" * 64},
        ):
            with self.subTest(payload=payload):
                code, body = self.post(f"/api/essays/{essay_id}/save", {"updates": {"summary": "x"}, **payload})
                self.assertEqual(code, 400, body)
                self.assertNotIn("summary", self.frontmatter("Guard.md"))

    def test_a_well_formed_expected_state_still_works(self):
        essay_id = self.note("Fine.md", "Writers Room")
        detail = self.server.get_essay_detail(essay_id)
        code, body = self.post(f"/api/essays/{essay_id}/save",
                               {"updates": {"summary": "a"}, "expected_mtime": detail["mtime"]})
        self.assertEqual(code, 200, body)
        code, body = self.post(f"/api/essays/{body['new_id']}/save",
                               {"updates": {"summary": "b"}, "expected_content_hash": body["content_hash"]})
        self.assertEqual(code, 200, body)
        code, body = self.post(f"/api/essays/{body['new_id']}/save",
                               {"updates": {"summary": "c"}, "expected_mtime": str(body["mtime"])})
        self.assertEqual(code, 200, body)

    def test_updates_that_are_not_an_object_are_a_bad_request(self):
        essay_id = self.note("Shape.md", "Writers Room")
        for action in ("save", "preview", "send"):
            for updates in (["summary"], "summary", 3):
                with self.subTest(action=action, updates=updates):
                    code, body = self.post(f"/api/essays/{essay_id}/{action}", {"updates": updates})
                    self.assertEqual(code, 400, body)

    def test_a_publish_object_that_is_not_an_object_is_a_bad_request(self):
        essay_id = self.note("Shape.md", "Writers Room")
        for action in ("preflight", "thumbnail-prompt"):
            with self.subTest(action=action):
                code, body = self.post(f"/api/essays/{essay_id}/{action}", {"publish": ["x"]})
                self.assertEqual(code, 400, body)


class SettingsValidationTests(unittest.TestCase):
    """The settings form's shape, checked before it becomes a config."""

    def setUp(self):
        import airdate_config  # noqa: PLC0415
        self.cfg = airdate_config

    def test_a_publication_that_is_not_a_host_is_refused_with_a_sentence(self):
        for value in ("not a host", "https://", "http://exa mple.com", "ftp://example.com", "https://example.com/p/x",
                      "https://user@example.com", "https://.substack.com", "https://evil.com%2F.substack.com",
                      "localhost", 7, ["a.com"]):
            with self.subTest(value=value):
                errors = self.cfg.form_errors({"publication": value})
                self.assertEqual(len(errors), 1, errors)
                self.assertTrue(errors[0].endswith("."))
                self.assertIn("yourname.substack.com", errors[0])

    def test_a_publication_that_is_a_host_is_fine(self):
        for value in ("", None, "yourname.substack.com", "https://writer.substack.com", "https://www.example.com/",
                      "http://example.com", "  writer.substack.com  "):
            with self.subTest(value=value):
                self.assertEqual(self.cfg.form_errors({"publication": value}), [])

    def test_red_pen_lines_that_are_not_a_list_or_text_are_refused(self):
        for value in (3, {"a": 1}, True, [1, 2]):
            with self.subTest(value=value):
                self.assertEqual(len(self.cfg.form_errors({"red_pen_lines": value})), 1)

    def test_red_pen_lines_as_text_or_a_list_are_fine(self):
        for value in ("one\ntwo", ["one", "two"], "", []):
            with self.subTest(value=value):
                self.assertEqual(self.cfg.form_errors({"red_pen_lines": value}), [])


class SettingsRouteValidationTests(HttpCase):
    def setUp(self):
        super().setUp()
        import airdate_config  # noqa: PLC0415
        self.data = self.root / "data"
        self.data.mkdir()
        self.data_patch = mock.patch.object(self.server, "DATA_DIR", self.data)
        self.data_patch.start()
        self.config_path = airdate_config.config_path(self.data)

    def tearDown(self):
        self.data_patch.stop()
        super().tearDown()

    def test_saving_a_publication_that_is_not_a_host_is_a_400_with_a_sentence(self):
        code, body = self.post("/api/settings", {"publication": "not a host"})
        self.assertEqual(code, 400, body)
        self.assertIn("yourname.substack.com", " ".join(body["errors"]))
        self.assertFalse(self.config_path.exists(), "a refused form was saved")

    def test_saving_red_pen_lines_of_the_wrong_shape_is_a_400_not_a_500(self):
        code, body = self.post("/api/settings", {"red_pen_lines": 3})
        self.assertEqual(code, 400, body)
        self.assertFalse(self.config_path.exists())

    def test_the_wizard_check_names_the_bad_publication(self):
        code, body = self.post("/api/settings/check", {"publication": "https://user@example.com"})
        self.assertEqual(code, 200, body)
        self.assertTrue(any("yourname.substack.com" in e for e in body["errors"]), body)


# ---- 10. scan, HEAD --------------------------------------------------------


class VanishingFileScanTests(RoomCase):
    def test_a_file_that_vanishes_mid_scan_is_skipped_not_unparseable(self):
        self.note("Stays.md", "Writers Room")
        self.note("Goes.md", "Writers Room")
        real = Path.read_text

        def flaky(path, *args, **kwargs):
            if path.name == "Goes.md":
                raise FileNotFoundError(2, "No such file or directory", str(path))
            return real(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", flaky):
            essays, _ = self.server.refresh_essay_index()
        self.assertEqual([r["relative_path"] for r in essays], ["Stays.md"])
        self.assertEqual(self.server._LAST_SCAN_FAILURES, [])


class HeadTests(HttpCase):
    def head(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request("HEAD", path)
            response = connection.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), body
        finally:
            connection.close()

    def test_head_on_the_page_answers_headers_without_a_body(self):
        status, headers, body = self.head("/airdate")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/html"))
        self.assertGreater(int(headers["content-length"]), 0)
        self.assertEqual(body, b"")

    def test_head_on_a_static_file_answers_headers_without_a_body(self):
        status, headers, body = self.head("/static/room/api.js")
        self.assertEqual(status, 200)
        self.assertEqual(int(headers["content-length"]), (ROOT / "static" / "room" / "api.js").stat().st_size)
        self.assertEqual(body, b"")

    def test_head_on_a_missing_file_is_a_404(self):
        status, _, body = self.head("/static/room/nope.js")
        self.assertEqual(status, 404)
        self.assertEqual(body, b"")


if __name__ == "__main__":
    unittest.main()
