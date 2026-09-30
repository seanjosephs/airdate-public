"""Slice 4b server work: send to substack from the room's editor.

What the server owns for send:

- a body-fidelity row in a preflight carries a structured `line`: the line of
  the script as the writer wrote it, counted from its first non-blank line,
  so the room can put the writer on it. airdate's own repairs (dropped tag
  lines, blank lines around headings) do not move it;
- a fresh data directory has no pairing, so no request can leave airdate at
  all, let alone reach the default connector port;
- a send through the connector records the draft's id and url on the note and
  leaves the phase alone, and a refused session and a timeout each come back
  with their own `error_kind`.

Every connector here is the fake from scripts/workflow-smoke.py on a free
port, paired through a pairing file in this test's own temporary folder.
"""

import importlib.util
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_room_foundation import point_server_at  # noqa: E402

# The port the real connector listens on. Nothing in this file may reach it.
REAL_CONNECTOR_PORT = 17777


def load_smoke():
    spec = importlib.util.spec_from_file_location("airdate_workflow_smoke", ROOT / "scripts" / "workflow-smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


READY_NOTE = """---
title: "Borrowed Light"
subtitle: "the first draft"
summary: "A line about the essay."
status: Ready for Air
scheduled_at: 2026-10-05
publication: "https://example.substack.com"
audience: everyone
comment_permissions: everyone
email_subject: "Borrowed Light"
email_preview_text: "A line about the essay."
hero_image: "https://example.com/hero.png"
thumbnail_alt: "Borrowed Light"
seo_title: "Borrowed Light"
seo_description: "A line about the essay."
social_title: "Borrowed Light"
social_description: "A line about the essay."
thumbnail_prompt: "a lamp on a desk"
tags: [craft, light]
---

The first line of the page.

The second paragraph.
"""


# The script as the writer sees it, lines counted from the first non-blank
# one: 1 intro, 3 a tag line airdate drops, 5 a table, 11 a heading airdate
# separates from the prose under it, 14 a pasted image glued to prose.
FIDELITY_BODY = (
    "\n"
    "Intro paragraph.\n"
    "\n"
    "#tag #another\n"
    "\n"
    "| a | b |\n"
    "| - | - |\n"
    "| 1 | 2 |\n"
    "\n"
    "After.\n"
    "\n"
    "## Heading\n"
    "welded prose\n"
    "\n"
    "![[Pasted image 1.png]]\n"
    "glued prose\n"
)


class SendCase(unittest.TestCase):
    """A fresh vault, fresh sidecars and a pairing file that does not exist."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "vault").mkdir()
        (root / "runtime" / "secrets").mkdir(parents=True)
        (root / "drafts").mkdir()
        os.environ["OBSIDIAN_ESSAYS_DIR"] = str(root / "vault")
        os.environ["AIR_DATE_DATA_DIR"] = str(root / "runtime")
        import server  # noqa: PLC0415
        self.server = server
        self.root = root
        self.runtime = root / "runtime"
        self.pairing = self.runtime / "secrets" / "connector.json"
        self.vault = point_server_at(server, root)
        self._patches = [
            mock.patch.object(server, "ARRIVALS_LOG", self.runtime / "arrivals.json"),
            mock.patch.object(server, "STARRED_LOG", self.runtime / "starred.json"),
            mock.patch.object(server, "ESSAY_INDEX_FILE", self.runtime / "essay_index.json"),
            # Never the module's own pairing file: it is resolved at import
            # from whichever data directory the first test happened to set.
            mock.patch.object(server, "CONNECTOR_PAIRING_FILE", self.pairing),
            mock.patch.object(server, "DRAFTS", root / "drafts"),
        ]
        for patch in self._patches:
            patch.start()
        server._ARRIVALS_CACHE = None
        server._STARRED_CACHE = None
        server._essay_cache = None

    def tearDown(self):
        # A read can start a background re-scan. Let it finish while the index
        # file is still patched, or it writes into a folder being deleted.
        with self.server._essay_cache_cond:
            while self.server._essay_scan_inflight:
                self.server._essay_cache_cond.wait(timeout=5)
        for patch in self._patches:
            patch.stop()
        self.server._ARRIVALS_CACHE = None
        self.server._STARRED_CACHE = None
        self.server._essay_cache = None
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.vault / name
        path.write_text(text, encoding="utf-8")
        self.server._essay_cache = None
        essays, _ = self.server.refresh_essay_index()
        relative = path.relative_to(self.vault).as_posix()
        row = next(e for e in essays if e.get("relative_path") == relative)
        return row["id"], path

    def assert_not_the_real_connector(self):
        port = urllib.parse.urlsplit(self.server.connector_url()).port
        self.assertNotEqual(port, REAL_CONNECTOR_PORT, "this test would reach the real connector")


class BodyFidelityLineTests(SendCase):
    def blockers(self, body):
        note = READY_NOTE.split("---\n\n", 1)[0] + "---\n" + body
        essay_id, _ = self.write("Fidelity.md", note)
        preflight = self.server.preflight_essay_for_substack(essay_id)
        return [b for b in preflight["blockers"] if b["key"].startswith("body_")]

    def test_every_body_row_carries_an_integer_line(self):
        rows = self.blockers(FIDELITY_BODY)
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(key=row["key"]):
                self.assertIsInstance(row["line"], int)
                self.assertNotIsInstance(row["line"], bool)
                self.assertEqual(row["field"], "")

    def test_the_line_is_where_the_writer_wrote_it(self):
        rows = {b["key"].split("_")[1]: b for b in self.blockers(FIDELITY_BODY)}
        # airdate drops the tag line and separates the heading before it
        # checks, which moved the table to line 3 and the image to line 13.
        self.assertEqual(rows["table"]["line"], 5)
        self.assertEqual(rows["embed"]["line"], 14)

    def test_the_message_names_the_same_line(self):
        for row in self.blockers(FIDELITY_BODY):
            with self.subTest(key=row["key"]):
                self.assertTrue(row["message"].startswith(f"line {row['line']}:"), row["message"])

    def test_the_key_names_the_same_line(self):
        # The key carries the line so two tables stay two rows.
        for row in self.blockers(FIDELITY_BODY):
            with self.subTest(key=row["key"]):
                self.assertTrue(row["key"].endswith(f"_{row['line']}"), row["key"])

    def test_the_overflow_row_carries_its_count(self):
        tables = "\n\n".join("| a | b |\n| - | - |\n| 1 | 2 |" for _ in range(self.server.BODY_BLOCKER_LIMIT + 3))
        rows = self.blockers(tables + "\n")
        more = next(b for b in rows if b["key"] == "body_blockers_more")
        self.assertEqual(more["count"], 3)

    def test_the_unsaved_script_is_counted_the_same_way(self):
        essay_id, _ = self.write("Plain.md", READY_NOTE)
        preflight = self.server.preflight_essay_for_substack(essay_id, None, FIDELITY_BODY)
        rows = {b["key"].split("_")[1]: b for b in preflight["blockers"] if b["key"].startswith("body_")}
        self.assertEqual(rows["table"]["line"], 5)

    def test_the_line_map_follows_every_repair(self):
        import obsidian_markdown  # noqa: PLC0415
        text = FIDELITY_BODY.strip()
        repaired, findings, line_map = obsidian_markdown.preprocess_with_line_map(text)
        self.assertEqual((repaired, findings), obsidian_markdown.preprocess(text))
        self.assertEqual(len(line_map), len(repaired.split("\n")))
        original = text.split("\n")
        for index, line in enumerate(repaired.split("\n")):
            if line.strip():
                with self.subTest(line=line):
                    self.assertEqual(original[line_map[index] - 1], line)


class NoRealConnectorTests(SendCase):
    """With no pairing file, no send leaves airdate. The port it would have
    used is the real connector's, which is exactly why this has to hold."""

    def setUp(self):
        super().setUp()
        self.assertFalse(self.pairing.exists())

        def refuse(*args, **kwargs):
            raise AssertionError(f"a request left airdate: {args!r}")

        self._guards = [
            mock.patch("urllib.request.urlopen", side_effect=refuse),
            mock.patch.object(socket, "create_connection", side_effect=refuse),
            mock.patch.object(socket.socket, "connect", side_effect=refuse),
        ]
        for guard in self._guards:
            guard.start()

    def tearDown(self):
        for guard in self._guards:
            guard.stop()
        super().tearDown()

    def test_the_unpaired_url_would_be_the_real_port(self):
        # Why the guard below matters: unpaired, airdate falls back to it.
        self.assertEqual(urllib.parse.urlsplit(self.server.connector_url()).port, REAL_CONNECTOR_PORT)

    def test_a_draft_request_is_refused_before_any_socket(self):
        result = self.server.connector_request("/draft", {"file": "anything.md"}, timeout=1)
        self.assertFalse(result["ok"])
        self.assertIs(result["paired"], False)
        self.assertEqual(result["error_kind"], "transport")

    def test_a_send_is_refused_before_any_socket(self):
        essay_id, path = self.write("Ready.md", READY_NOTE)
        before = path.read_text(encoding="utf-8")
        result = self.server.send_essay_to_substack(essay_id, {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "blocked")
        keys = {b["key"] for b in result["preflight"]["blockers"]}
        self.assertIn("substack_command", keys)
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(list((self.root / "drafts").iterdir()), [])

    def test_the_publish_step_itself_is_refused_before_any_socket(self):
        # Below the readiness gate, in case a later change lets a send past it.
        result = self.server.publish_draft({"title": "Borrowed Light", "body": "Words."})
        self.assertFalse(result["ok"])
        self.assertNotIn("transport", result)
        self.assertEqual(result["error_kind"], "transport")

    def test_the_status_route_is_answered_without_a_socket(self):
        status = self.server.substack_status_payload()
        self.assertIs(status["connected"], False)
        self.assertIs(status["connector"]["paired"], False)


class FakeConnectorSendTests(SendCase):
    """The whole send path against the fake connector, paired on a free port."""

    @classmethod
    def setUpClass(cls):
        cls.smoke = load_smoke()
        cls.token = "fake-connector-token"
        cls.port = cls.smoke.free_port()
        assert cls.port != REAL_CONNECTOR_PORT
        cls.connector, cls.captures, cls.mode = cls.smoke.start_fake_connector(cls.port, cls.token)

    @classmethod
    def tearDownClass(cls):
        cls.connector.shutdown()
        cls.connector.server_close()

    def setUp(self):
        super().setUp()
        self.pairing.write_text(json.dumps({"port": self.port, "token": self.token}), encoding="utf-8")
        self.mode["draft"] = "ok"
        self.captures.clear()
        self.assert_not_the_real_connector()
        self.essay_id, self.path = self.write("Ready.md", READY_NOTE)

    def detail(self):
        self.server._essay_cache = None
        return self.server.get_essay_detail(self.essay_id)

    def test_the_pairing_port_wins(self):
        self.assertEqual(self.server.connector_url(), f"http://127.0.0.1:{self.port}")

    def test_a_ready_note_passes_preflight(self):
        preflight = self.server.preflight_essay_for_substack(self.essay_id)
        self.assertEqual(preflight["blockers"], [])

    def test_success_records_the_draft_and_keeps_the_phase(self):
        before = self.detail()
        self.assertEqual(before["status"], "Ready for Air")
        result = self.server.send_essay_to_substack(self.essay_id, {})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["draft_id"], "connector-draft-1")
        self.assertEqual(result["edit_url"], "https://example.substack.com/p/connector-draft-1")
        self.assertIs(result["draft_recorded"], True)
        self.assertEqual(result["transport"], "obsidian_connector")
        self.assertEqual(len(self.captures), 1)
        after = self.detail()
        self.assertEqual(after["frontmatter"]["substack_draft_id"], "connector-draft-1")
        self.assertEqual(after["frontmatter"]["substack_draft_url"], "https://example.substack.com/p/connector-draft-1")
        self.assertEqual(after["status"], "Ready for Air")
        self.assertEqual(after["frontmatter"]["status"], "Ready for Air")
        self.assertEqual(str(after["frontmatter"]["scheduled_at"])[:10], "2026-10-05")
        # The editor adopts this, so its next save is not refused.
        self.assertEqual(result["content_hash"], after["content_hash"])

    def test_success_after_a_save_hands_back_the_saved_state(self):
        before = self.detail()
        result = self.server.send_essay_to_substack(
            self.essay_id, {"subtitle": "a new line"}, None, None, before["mtime"], before["content_hash"],
        )
        self.assertTrue(result["ok"], result)
        self.assertIsNotNone(result["saved_state"])
        self.assertIn("subtitle: a new line", self.path.read_text(encoding="utf-8").replace('"', ""))

    def test_a_refused_session_is_auth_and_records_nothing(self):
        self.mode["draft"] = "auth"
        result = self.server.send_essay_to_substack(self.essay_id, {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "auth")
        self.assertEqual(result["transport"], "obsidian_connector")
        self.assertNotIn("substack_draft_id", self.detail()["frontmatter"])

    def test_a_timeout_is_timeout_and_never_says_nothing_was_sent(self):
        self.mode["draft"] = "timeout"
        result = self.server.send_essay_to_substack(self.essay_id, {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "timeout")
        self.assertIn("check substack", result["message"].lower())
        self.assertNotIn("nothing was sent", result["message"].lower())

    def test_a_failed_send_after_a_save_still_hands_back_the_saved_state(self):
        # Without it the editor's next send carries the pre-save file state
        # and is refused as a change made in Obsidian.
        self.mode["draft"] = "auth"
        before = self.detail()
        result = self.server.send_essay_to_substack(
            self.essay_id, {"subtitle": "a new line"}, None, None, before["mtime"], before["content_hash"],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["saved_state"]["content_hash"], self.detail()["content_hash"])


if __name__ == "__main__":
    unittest.main()
