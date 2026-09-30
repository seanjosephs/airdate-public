"""Slice 2 server work: the star, the star date, and the red pen.

The post-it on a card is the promotion control. Pressing it moves an essay
from writers room to writers likey, and pressing it again moves it back. The
route is deliberately narrow: those two phases and nothing else, so a star can
never pull an essay off the board or out of the shelf.

The star date is runtime state, like the arrival date. It lives in a sidecar
beside arrivals.json and never in the vault.
"""

import copy
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

OLD_ARRIVAL = "2026-01-01T00:00:00+00:00"


class StarRouteCase(unittest.TestCase):
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

    def note(self, name, status=None, uid=None, extra=""):
        lines = ["---", f'title: "{name[:-3]}"']
        if status:
            lines.append(f"status: {status}")
        if uid:
            lines.append(f"airdate_uid: {uid}")
        if extra:
            lines.append(extra)
        lines += ["---", "The first line of the page.", ""]
        (self.vault / name).write_text("\n".join(lines), encoding="utf-8")
        self.server.refresh_essay_index()
        return self.row(name)["id"]

    def row(self, name):
        essays, _ = self.server.refresh_essay_index()
        return next(r for r in essays if r["relative_path"] == name)

    def backdate(self, essay_id, stamp=OLD_ARRIVAL):
        arrivals = self.server.load_arrivals()
        arrivals[essay_id] = stamp
        self.server.save_arrivals(arrivals)

    def text(self, name):
        return (self.vault / name).read_text(encoding="utf-8")


class StarMovesBetweenRoomAndLikeyTests(StarRouteCase):
    def test_a_star_moves_writers_room_to_writers_likey(self):
        essay_id = self.note("Room.md", "Writers Room", uid="a" * 32)
        result = self.server.star_essay(essay_id, {"starred": True})
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertRegex(self.text("Room.md"), r"status: \"?Writers Likey")
        self.assertEqual(self.row("Room.md")["status"], "Writers Likey")

    def test_taking_the_star_off_moves_likey_back_to_the_room(self):
        essay_id = self.note("Likey.md", "Writers Likey", uid="b" * 32)
        result = self.server.star_essay(essay_id, {"starred": False})
        self.assertTrue(result["ok"])
        self.assertRegex(self.text("Likey.md"), r"status: \"?Writers Room")
        self.assertEqual(self.row("Likey.md")["status"], "Writers Room")

    def test_starring_a_likey_essay_is_a_no_op_success(self):
        essay_id = self.note("Already.md", "Writers Likey", uid="c" * 32)
        before = self.text("Already.md")
        mtime = (self.vault / "Already.md").stat().st_mtime
        result = self.server.star_essay(essay_id, {"starred": True})
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual(self.text("Already.md"), before)
        self.assertEqual((self.vault / "Already.md").stat().st_mtime, mtime)

    def test_unstarring_a_room_essay_is_a_no_op_success(self):
        essay_id = self.note("Plain.md", uid="d" * 32)
        before = self.text("Plain.md")
        result = self.server.star_essay(essay_id, {"starred": False})
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual(self.text("Plain.md"), before)

    def test_starred_must_be_a_real_boolean(self):
        essay_id = self.note("Bool.md", uid="e" * 32)
        for bad in ("true", 1, None):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                self.server.star_essay(essay_id, {"starred": bad})
        with self.assertRaises(ValueError):
            self.server.star_essay(essay_id, {})

    def test_an_unknown_essay_is_not_found(self):
        with self.assertRaises(FileNotFoundError):
            self.server.star_essay("f" * 32, {"starred": True})


class StarRefusesOtherPhasesTests(StarRouteCase):
    def assert_refused(self, name, status, starred, extra=""):
        essay_id = self.note(name, status, uid=None, extra=extra)
        before = self.text(name)
        with self.assertRaises(self.server.RefusedError) as caught:
            self.server.star_essay(essay_id, {"starred": starred})
        payload = caught.exception.payload
        self.assertIs(payload["ok"], False)
        self.assertTrue(payload["refused"])
        sentence = payload["error"]
        self.assertTrue(sentence)
        self.assertEqual(sentence, sentence.lower(), "the interface shows this verbatim, lowercase")
        self.assertTrue(sentence.endswith("."))
        self.assertEqual(self.text(name), before, "a refusal writes nothing")
        return payload

    def test_ready_for_air_is_refused_both_ways(self):
        extra = "scheduled_at: 2026-10-05"
        self.assert_refused("Aired1.md", "Ready for Air", True, extra)
        payload = self.assert_refused("Aired2.md", "Ready for Air", False, extra)
        self.assertEqual(payload["refused"], "ready-for-air")

    def test_live_is_refused(self):
        payload = self.assert_refused(
            "Out.md", "Live", False, "substack_url: https://example.substack.com/p/out")
        self.assertEqual(payload["refused"], "live")

    def test_archived_is_refused(self):
        payload = self.assert_refused("Parked.md", "Archived", True)
        self.assertEqual(payload["refused"], "archived")

    def test_a_refusal_is_not_a_scheduling_refusal(self):
        # The client tells refusals apart by the marker; the board's gate keeps
        # its own marker and its own type.
        self.assertTrue(issubclass(self.server.SchedulingRefusedError, self.server.RefusedError))
        self.assertEqual(
            self.server.SchedulingRefusedError("x.").payload["refused"], "writers-room")


class StarDateSidecarTests(StarRouteCase):
    def test_a_star_records_its_date_in_the_runtime_dir(self):
        essay_id = self.note("Dated.md", uid="1a" * 16)
        before = datetime.now(timezone.utc)
        self.server.star_essay(essay_id, {"starred": True})
        stamp = json.loads((self.runtime / "starred.json").read_text(encoding="utf-8"))[essay_id]
        self.assertGreaterEqual(datetime.fromisoformat(stamp), before.replace(microsecond=0))
        self.assertEqual(list(self.vault.rglob("starred.json")), [], "never in the vault")

    def test_the_index_row_carries_starred_at(self):
        essay_id = self.note("Row.md", uid="2a" * 16)
        self.assertEqual(self.row("Row.md")["starred_at"], "")
        self.server.star_essay(essay_id, {"starred": True})
        self.assertTrue(self.row("Row.md")["starred_at"])

    def test_unstarring_clears_the_date(self):
        essay_id = self.note("Undo.md", uid="3a" * 16)
        self.server.star_essay(essay_id, {"starred": True})
        self.server.star_essay(essay_id, {"starred": False})
        self.assertEqual(self.row("Undo.md")["starred_at"], "")
        self.assertNotIn(essay_id, self.server.load_starred())

    def test_the_star_date_survives_a_uid_mint(self):
        essay_id = self.note("Mint.md")  # no uid: the first write mints one
        result = self.server.star_essay(essay_id, {"starred": True})
        self.assertNotEqual(result["new_id"], essay_id)
        row = self.row("Mint.md")
        self.assertEqual(row["id"], result["new_id"])
        self.assertTrue(row["starred_at"])
        self.assertIn(result["new_id"], self.server.load_starred())

    def test_carry_star_moves_the_date_to_the_new_id(self):
        self.server.save_starred({"old": "2026-09-01T00:00:00+00:00"})
        self.server.carry_star("old", "new")
        self.assertEqual(self.server.load_starred(), {"new": "2026-09-01T00:00:00+00:00"})

    def test_a_failed_write_leaves_no_star_date(self):
        essay_id = self.note("Busy.md", uid="4a" * 16)
        with self.assertRaises(self.server.EssayConflictError):
            self.server.star_essay(essay_id, {"starred": True, "expected_content_hash": "stale"})
        self.assertNotIn(essay_id, self.server.load_starred())
        self.assertIn("title", self.text("Busy.md"))
        self.assertNotIn("Writers Likey", self.text("Busy.md"))


class StarKeepsThePaperTests(StarRouteCase):
    def test_a_star_on_a_note_with_no_uid_keeps_its_arrival(self):
        essay_id = self.note("Old.md")
        self.backdate(essay_id)
        result = self.server.star_essay(essay_id, {"starred": True})
        self.assertNotEqual(result["new_id"], essay_id, "the star minted a uid")
        self.assertEqual(self.row("Old.md")["arrived_at"], OLD_ARRIVAL)
        self.assertEqual(result["essay"]["arrived_at"], OLD_ARRIVAL)

    def test_fresh_on_promotion_off_keeps_the_arrival(self):
        essay_id = self.note("Keep.md", uid="5a" * 16)
        self.backdate(essay_id)
        self.assertFalse(self.server.PAPER_FRESH_ON_PROMOTION)
        self.server.star_essay(essay_id, {"starred": True})
        self.assertEqual(self.row("Keep.md")["arrived_at"], OLD_ARRIVAL)

    def test_fresh_on_promotion_on_resets_the_arrival_to_now(self):
        essay_id = self.note("Fresh.md")
        self.backdate(essay_id)
        before = datetime.now(timezone.utc).replace(microsecond=0)
        with mock.patch.object(self.server, "PAPER_FRESH_ON_PROMOTION", True):
            result = self.server.star_essay(essay_id, {"starred": True})
        arrived = self.row("Fresh.md")["arrived_at"]
        self.assertNotEqual(arrived, OLD_ARRIVAL)
        self.assertGreaterEqual(datetime.fromisoformat(arrived), before)
        self.assertEqual(result["essay"]["arrived_at"], arrived)

    def test_fresh_on_promotion_does_not_freshen_an_unstar(self):
        essay_id = self.note("Down.md", "Writers Likey", uid="6a" * 16)
        self.backdate(essay_id)
        with mock.patch.object(self.server, "PAPER_FRESH_ON_PROMOTION", True):
            self.server.star_essay(essay_id, {"starred": False})
        self.assertEqual(self.row("Down.md")["arrived_at"], OLD_ARRIVAL)

    def test_the_setting_is_read_from_config(self):
        config = copy.deepcopy(self.server.airdate_config.DEFAULT_CONFIG)
        config["vault"]["path"] = str(self.vault.parent)
        config["paper"]["fresh_on_promotion"] = True
        self.server.apply_config(config, True)
        self.assertTrue(self.server.PAPER_FRESH_ON_PROMOTION)
        config["paper"]["fresh_on_promotion"] = False
        self.server.apply_config(config, True)
        self.assertFalse(self.server.PAPER_FRESH_ON_PROMOTION)


class StarResponseTests(StarRouteCase):
    def test_the_response_carries_the_fresh_index_row(self):
        essay_id = self.note("Fresh Row.md")
        result = self.server.star_essay(essay_id, {"starred": True})
        row = result["essay"]
        self.assertEqual(result["old_id"], essay_id)
        self.assertEqual(row["id"], result["new_id"])
        self.assertEqual(row["status"], "Writers Likey")
        for key in ("arrived_at", "excerpt", "starred_at", "status", "word_count",
                    "note_pad", "note_color", "totem_raw", "publish_readiness", "search_blob"):
            with self.subTest(key=key):
                self.assertIn(key, row)
        self.assertTrue(row["starred_at"])
        self.assertTrue(result["starred"])

    def test_a_no_op_still_returns_the_row(self):
        essay_id = self.note("Noop.md", "Writers Likey", uid="7a" * 16)
        result = self.server.star_essay(essay_id, {"starred": True})
        self.assertEqual(result["essay"]["id"], essay_id)
        self.assertEqual(result["old_id"], essay_id)
        self.assertEqual(result["new_id"], essay_id)


class StarHttpTests(StarRouteCase):
    """The route itself: status codes and the refusal shape on the wire."""

    def setUp(self):
        super().setUp()
        class QuietHandler(self.server.Handler):
            def log_message(self, *args):  # keep the test output readable
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

    def test_a_star_answers_200_with_the_row(self):
        essay_id = self.note("Wire.md", uid="8a" * 16)
        status, body = self.post(f"/api/essays/{essay_id}/star", {"starred": True})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["essay"]["status"], "Writers Likey")

    def test_a_refusal_answers_409_with_the_marker(self):
        essay_id = self.note("Wired.md", "Ready for Air", uid="9a" * 16, extra="scheduled_at: 2026-10-05")
        status, body = self.post(f"/api/essays/{essay_id}/star", {"starred": False})
        self.assertEqual(status, 409)
        self.assertEqual(body["refused"], "ready-for-air")
        self.assertNotIn("reason", body, "a refusal is not a file-changed conflict")

    def test_a_bad_body_answers_400(self):
        essay_id = self.note("Bad.md", uid="0a" * 16)
        status, _ = self.post(f"/api/essays/{essay_id}/star", {"starred": "yes"})
        self.assertEqual(status, 400)


class RedPenConfigTests(unittest.TestCase):
    """The twenty jabs ship in the server; the writer's own lines follow them."""

    @classmethod
    def setUpClass(cls):
        import server  # noqa: PLC0415
        cls.server = server

    def tearDown(self):
        self.server.load_and_apply_config()

    def configure(self, **red_pen):
        config = copy.deepcopy(self.server.airdate_config.DEFAULT_CONFIG)
        config["red_pen"].update(red_pen)
        self.server.apply_config(config, True)
        return self.server.ui_config_payload()["red_pen"]

    def test_twenty_jabs_ship(self):
        self.assertEqual(len(self.server.RED_PEN_JABS), 20)

    def test_the_jabs_are_decoded_and_lowercase(self):
        for line in self.server.RED_PEN_JABS:
            with self.subTest(line=line):
                self.assertNotIn("&#", line)
                self.assertNotIn("&amp;", line)
                self.assertEqual(line, line.lower())
        self.assertTrue(any("'" in line for line in self.server.RED_PEN_JABS))

    def test_on_by_default_with_the_twenty(self):
        payload = self.configure()
        self.assertIs(payload["enabled"], True)
        self.assertEqual(payload["lines"], list(self.server.RED_PEN_JABS))

    def test_the_writers_own_lines_come_after_the_twenty(self):
        payload = self.configure(lines=["  mine, all mine.  ", "", 7, "second of mine."])
        self.assertEqual(payload["lines"][:20], list(self.server.RED_PEN_JABS))
        self.assertEqual(payload["lines"][20:], ["mine, all mine.", "second of mine."])

    def test_it_can_be_turned_off(self):
        payload = self.configure(enabled=False)
        self.assertIs(payload["enabled"], False)


if __name__ == "__main__":
    unittest.main()
