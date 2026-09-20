"""Recovery when Substack no longer has a draft airdate stored an id for.

A draft deleted in Substack used to be terminal from airdate's side: the id
stayed in frontmatter, every later send failed against it, and the only remedy
was hand-editing YAML. The transport always reported the dead id; nothing
carried it to the browser.

Deleting in Substack cannot be prevented from here, so recovery is the fix.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class StaleDraftIdReachesTheBrowserTests(unittest.TestCase):
    def test_the_transport_reports_the_dead_id(self):
        source = (ROOT / "substack_draft.py").read_text(encoding="utf-8")
        self.assertIn("stale_draft_id=stored_draft_id", source,
                      "the transport must name which draft id died")

    def test_finish_transport_result_lifts_it(self):
        result = server.finish_transport_result({
            "ok": False,
            "stdout": '{"error_kind": "transport", "message": "gone", "stale_draft_id": "209222818"}',
        })
        self.assertEqual(result.get("stale_draft_id"), "209222818",
                         "the dead id was computed and then dropped before the browser saw it")

    def test_a_healthy_send_carries_no_stale_id(self):
        result = server.finish_transport_result({
            "ok": True,
            "stdout": '{"draft_id": "216553077", "edit_url": "https://example.com/publish/post/216553077"}',
        })
        self.assertFalse(result.get("stale_draft_id"))
        self.assertEqual(result.get("draft_id"), "216553077")


class ForgettingTheLinkIsDeliberateTests(unittest.TestCase):
    """The transport refuses to silently create a replacement draft: if the old
    one was published rather than deleted, a new draft would duplicate a live
    post. So recovery is an explicit route the writer chooses, and the UI
    confirms before calling it."""

    SERVER = (ROOT / "server.py").read_text(encoding="utf-8")
    EDITOR = (ROOT / "static" / "airdate.js").read_text(encoding="utf-8")

    def test_the_transport_still_refuses_to_auto_replace(self):
        source = (ROOT / "substack_draft.py").read_text(encoding="utf-8")
        self.assertIn("Never fall back to creating a new draft here", source,
                      "the no-silent-replacement rule must survive this feature")

    def test_the_route_exists_and_clears_both_keys(self):
        self.assertIn('action == "forget-draft-link"', self.SERVER)
        route = self.SERVER.split('action == "forget-draft-link"', 1)[1][:1400]
        self.assertIn('"substack_draft_id": ""', route)
        self.assertIn('"substack_draft_url": ""', route)

    def test_clearing_the_link_does_not_move_the_note(self):
        # set_essay_status enforces a status->folder policy, so passing the
        # wrong status here would relocate the file as a side effect.
        route = self.SERVER.split('action == "forget-draft-link"', 1)[1][:1400]
        self.assertIn("get_essay_detail(essay_id).get(\"status\")", route,
                      "recovery must reuse the essay's own effective status")

    def test_the_editor_confirms_before_forgetting(self):
        self.assertIn("forgetDraftLinkAndResend", self.EDITOR)
        flow = self.EDITOR.split("async function forgetDraftLinkAndResend", 1)[1][:900]
        self.assertIn("window.confirm", flow,
                      "a published-not-deleted draft would be duplicated; ask first")
        self.assertIn("duplicate a post that is already live", flow,
                      "the confirm must say what the wrong answer costs")

    def test_the_editor_only_offers_recovery_when_there_is_a_dead_id(self):
        self.assertIn("const stale = String(result.stale_draft_id || '').trim();", self.EDITOR)
        self.assertIn("if (stale) {", self.EDITOR)

    def test_server_still_parses(self):
        ast.parse(self.SERVER)


class ArchiveSaysWhereItWentTests(unittest.TestCase):
    EDITOR = (ROOT / "static" / "airdate.js").read_text(encoding="utf-8")

    def test_the_success_message_names_the_destination(self):
        flow = self.EDITOR.split("async function archiveEssayFlow", 1)[1][:700]
        self.assertIn("archived to ${where}", flow,
                      '"archived" alone reads as "deleted"; name the file')
        self.assertIn("result?.path", flow, "the server already returns the new path")


if __name__ == "__main__":
    unittest.main()
