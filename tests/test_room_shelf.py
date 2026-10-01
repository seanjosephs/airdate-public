"""Slice 6 server work: the shelf, rainy day, and the umbrella.

"The archive" (the shelf's month view) and "archived" (rainy day, on disk) are
two different words for two different things, and this file is careful to
keep them apart: a essay's STATUS is Archived; the SHELF groups live essays by
month and calls last month's group "the archive".

The umbrella parks an essay: Archived, with previous_status and archived_at
stamped into frontmatter (unlike arrived_at/starred_at, which never touch the
vault, because "back to the room" has to survive even if the runtime sidecars
were lost). Parking a Ready for Air or Live essay is refused at the server,
not only hidden in the client. Back to the room restores the stamped phase and
clears both fields.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_room_foundation import point_server_at  # noqa: E402


class ParkRouteCase(unittest.TestCase):
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

    # -- helpers ---------------------------------------------------------

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

    def row(self, essay_id):
        essays, _ = self.server.refresh_essay_index()
        return next(r for r in essays if r["id"] == essay_id)

    def frontmatter(self, relative):
        text = (self.vault / relative).read_text(encoding="utf-8")
        return self.server.split_frontmatter(text)[0]

    def path_of(self, essay_id):
        _, id_map = self.server.refresh_essay_index()
        return id_map[essay_id]


class ParkingMovesToArchiveTests(ParkRouteCase):
    def test_parking_a_writers_room_essay_stamps_both_fields(self):
        essay_id = self.note("Room.md")
        before = datetime.now(timezone.utc)
        result = self.server.park_essay(essay_id, {})
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "Archived")
        new_id = result["new_id"]
        fm = self.frontmatter(f"Archive/{self.path_of(new_id).name}")
        self.assertEqual(fm["previous_status"], "Writers Room")
        self.assertGreaterEqual(datetime.fromisoformat(fm["archived_at"]), before.replace(microsecond=0))
        self.assertTrue(str(self.path_of(new_id)).replace("\\", "/").split("/")[-2] == "Archive")

    def test_parking_a_writers_likey_essay_remembers_likey(self):
        essay_id = self.note("Likey.md", "Writers Likey")
        result = self.server.park_essay(essay_id, {})
        new_id = result["new_id"]
        fm = self.frontmatter(f"Archive/{self.path_of(new_id).name}")
        self.assertEqual(fm["previous_status"], "Writers Likey")

    def test_parking_an_already_parked_essay_is_a_no_op(self):
        essay_id = self.note("Twice.md")
        first = self.server.park_essay(essay_id, {})
        new_id = first["new_id"]
        before = self.frontmatter(f"Archive/{self.path_of(new_id).name}")
        result = self.server.park_essay(new_id, {})
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        after = self.frontmatter(f"Archive/{self.path_of(new_id).name}")
        self.assertEqual(before["previous_status"], after["previous_status"])
        self.assertEqual(before["archived_at"], after["archived_at"])

    def test_parked_essays_leave_the_active_collection(self):
        essay_id = self.note("Gone.md", "Writers Likey")
        result = self.server.park_essay(essay_id, {})
        row = self.row(result["new_id"])
        self.assertEqual(row["collection"], "archived")

    def test_an_unknown_essay_is_not_found(self):
        with self.assertRaises(FileNotFoundError):
            self.server.park_essay("f" * 32, {})


class ParkingRefusesScheduledOrLiveTests(ParkRouteCase):
    def assert_refused(self, name, status, extra=""):
        essay_id = self.note(name, status, extra=extra)
        before = (self.vault / name).read_text(encoding="utf-8")
        with self.assertRaises(self.server.RefusedError) as caught:
            self.server.park_essay(essay_id, {})
        payload = caught.exception.payload
        self.assertIs(payload["ok"], False)
        self.assertTrue(payload["refused"])
        sentence = payload["error"]
        self.assertEqual(sentence, sentence.lower(), "the interface shows this verbatim, lowercase")
        self.assertTrue(sentence.endswith("."))
        self.assertEqual((self.vault / name).read_text(encoding="utf-8"), before, "a refusal writes nothing")
        return payload

    def test_ready_for_air_is_refused(self):
        payload = self.assert_refused("Aired.md", "Ready for Air", extra="scheduled_at: 2026-10-05")
        self.assertEqual(payload["refused"], "ready-for-air")

    def test_live_is_refused(self):
        payload = self.assert_refused(
            "Out.md", "Live", extra="substack_url: https://example.substack.com/p/out")
        self.assertEqual(payload["refused"], "live")


class BackToTheRoomTests(ParkRouteCase):
    def test_it_restores_the_stamped_phase(self):
        essay_id = self.note("Was Likey.md", "Writers Likey")
        parked = self.server.park_essay(essay_id, {})
        result = self.server.back_to_room(parked["new_id"], {})
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "Writers Likey")
        new_id = result["new_id"]
        fm = self.frontmatter(self.path_of(new_id).relative_to(self.vault).as_posix())
        self.assertNotIn("previous_status", fm)
        self.assertNotIn("archived_at", fm)
        self.assertEqual(self.row(new_id)["status"], "Writers Likey")

    def test_it_falls_back_to_writers_room_with_no_previous_status(self):
        essay_id = self.note("Blank.md")
        parked = self.server.park_essay(essay_id, {})
        path = self.path_of(parked["new_id"])
        text = path.read_text(encoding="utf-8")
        fm, body = self.server.split_frontmatter(text)
        fm.pop("previous_status", None)
        path.write_text(self.server.compose_markdown(fm, body), encoding="utf-8")
        self.server.refresh_essay_index()
        result = self.server.back_to_room(parked["new_id"], {})
        self.assertEqual(result["status"], "Writers Room")

    def test_it_pulls_the_file_out_of_archive(self):
        essay_id = self.note("Pulled.md", "Writers Room")
        parked = self.server.park_essay(essay_id, {})
        self.assertIn("Archive", str(self.path_of(parked["new_id"])))
        result = self.server.back_to_room(parked["new_id"], {})
        self.assertTrue(result["moved"])
        self.assertNotIn("Archive", result["relative_path"])

    def test_it_refuses_an_essay_that_is_not_parked(self):
        essay_id = self.note("Not Parked.md", "Writers Likey")
        with self.assertRaises(self.server.RefusedError) as caught:
            self.server.back_to_room(essay_id, {})
        self.assertEqual(caught.exception.payload["refused"], "not-parked")

    def test_a_restore_leaves_the_essay_schedulable_again(self):
        essay_id = self.note("Roundtrip.md", "Writers Likey")
        parked = self.server.park_essay(essay_id, {})
        back = self.server.back_to_room(parked["new_id"], {})
        # No refusal: a restored writers-likey essay can go back on the board.
        result = self.server.schedule_essay(back["new_id"], {"scheduled_at": "2026-10-05"})
        self.assertEqual(result["status"], "Ready for Air")


class ParkedCountsAndScopesTests(ParkRouteCase):
    def test_scope_archived_lists_only_parked_essays(self):
        self.note("Active.md", "Writers Likey")
        parked_id = self.note("Parked.md")
        self.server.park_essay(parked_id, {})
        essays, _ = self.server.refresh_essay_index()
        archived = [e for e in essays if e["collection"] == "archived"]
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["relative_path"].split("/")[-1], "Parked.md")

    def test_a_parked_essay_cannot_be_starred(self):
        essay_id = self.note("Star.md")
        parked = self.server.park_essay(essay_id, {})
        with self.assertRaises(self.server.RefusedError) as caught:
            self.server.star_essay(parked["new_id"], {"starred": True})
        self.assertEqual(caught.exception.payload["refused"], "archived")


class ParkHttpTests(ParkRouteCase):
    """The routes themselves: status codes and the refusal shape on the wire."""

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
            return exc.code, json.loads(exc.read())

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as response:
            return response.status, json.loads(response.read())

    def test_archive_route_parks_and_answers_200(self):
        essay_id = self.note("Wire.md", "Writers Likey")
        status, body = self.post(f"/api/essays/{essay_id}/archive", {})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["row"]["status"], "Archived")
        self.assertEqual(body["row"]["previous_status"], "Writers Likey")

    def test_archive_route_refuses_ready_for_air(self):
        essay_id = self.note("Wired.md", "Ready for Air", extra="scheduled_at: 2026-10-05")
        status, body = self.post(f"/api/essays/{essay_id}/archive", {})
        self.assertEqual(status, 409)
        self.assertEqual(body["refused"], "ready-for-air")

    def test_back_to_room_route_restores_and_answers_200(self):
        essay_id = self.note("Back.md", "Writers Likey")
        _, parked = self.post(f"/api/essays/{essay_id}/archive", {})
        new_id = parked["new_id"]
        status, body = self.post(f"/api/essays/{new_id}/back-to-room", {})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["row"]["status"], "Writers Likey")

    def test_back_to_room_on_an_unparked_essay_is_409(self):
        essay_id = self.note("Nope.md", "Writers Room")
        status, body = self.post(f"/api/essays/{essay_id}/back-to-room", {})
        self.assertEqual(status, 409)
        self.assertEqual(body["refused"], "not-parked")

    def test_scope_archived_over_the_wire(self):
        essay_id = self.note("Rain.md", "Writers Room")
        self.post(f"/api/essays/{essay_id}/archive", {})
        status, body = self.get("/api/essays?scope=archived")
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["essays"][0]["relative_path"], "Archive/Rain.md")


if __name__ == "__main__":
    unittest.main()


class UndoUsesTheIdParkingReturnsTests(ParkRouteCase):
    """Most real notes have no uid. Parking one mints a uid on the first write
    AND moves the file into Archive/, so the id the client held before parking
    no longer resolves. The undo on the slip must use the id the park response
    returns. Found at review: the card's umbrella kept the old id, and every
    undo of a uid-less note answered 404."""

    def test_parking_a_note_without_a_uid_returns_a_new_id(self):
        old_id = self.note("No Uid Yet.md", status="Writers Likey")
        result = self.server.park_essay(old_id, {})
        self.assertNotEqual(result["new_id"], old_id)
        self.assertEqual(result["row"]["id"], result["new_id"])

    def test_back_to_the_room_with_the_returned_id_restores_the_phase(self):
        old_id = self.note("Comes Back.md", status="Writers Likey")
        parked = self.server.park_essay(old_id, {})
        back = self.server.back_to_room(parked["new_id"], {})
        self.assertEqual(back["row"]["status"], "Writers Likey")

    def test_the_pre_park_id_no_longer_resolves(self):
        # Why the client must not keep the old id: the file moved and was
        # given a uid, so the path-hash id points at nothing.
        old_id = self.note("Gone From Here.md", status="Writers Room")
        self.server.park_essay(old_id, {})
        with self.assertRaises(Exception):
            self.server.back_to_room(old_id, {})
