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
    confirms before calling it.

    Re-pinned to the room in slice 7: the old page asked with window.confirm;
    the room asks in the page, in the send result under the button
    (editor-view.js askForget), and only its "forget it and send" calls the
    route. editor.js offers the row only when there is a dead id."""

    SERVER = (ROOT / "server.py").read_text(encoding="utf-8")
    VIEW = (ROOT / "static" / "room" / "editor-view.js").read_text(encoding="utf-8")
    RULES = (ROOT / "static" / "room" / "editor.js").read_text(encoding="utf-8")

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

    def ask(self):
        return self.VIEW.split("function askForget(stale)", 1)[1].split("\n  }\n", 1)[0]

    def test_the_editor_asks_in_the_page_before_forgetting(self):
        ask = self.ask()
        self.assertIn("forget draft ${esc(stale)} and send this as a new draft?", ask)
        self.assertIn('data-send-action="forget-yes"', ask)
        self.assertIn('data-send-action="forget-no">keep the link', ask)
        # The safe answer takes focus, so a stray enter keeps the link.
        self.assertIn("querySelector('[data-send-action=\"forget-no\"]').focus()", ask)
        self.assertNotIn("window.confirm", self.VIEW)

    def test_the_question_says_what_the_wrong_answer_costs(self):
        self.assertIn("a new draft would copy a post that is already live", self.ask(),
                      "a published-not-deleted draft would be duplicated; ask first")

    def test_only_the_yes_calls_the_route(self):
        forget = self.VIEW.split("async function forgetAndSend()", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("/forget-draft-link", forget)
        self.assertEqual(self.VIEW.count("/forget-draft-link"), 1, "nothing else may clear the link")
        actions = self.VIEW.split("async function onSendAction(event)", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("} else if (action === 'forget') {\n      askForget(", actions)
        self.assertIn("} else if (action === 'forget-yes') {\n      forgetAndSend();", actions)

    def test_the_editor_only_offers_recovery_when_there_is_a_dead_id(self):
        self.assertIn("const stale = String(result.stale_draft_id || '').trim();", self.RULES)
        self.assertIn("if (stale) {", self.RULES)

    def test_server_still_parses(self):
        ast.parse(self.SERVER)


class ParkingSaysWhereItWentTests(unittest.TestCase):
    """The old page's archive button said where the file went ("archived"
    alone reads as "deleted"). The room has no archive button: rainy day
    parks an essay (Archive/ on disk, never named in the interface), and the
    slip says where it went and carries an undo. Re-pinned in slice 7."""

    POOL = (ROOT / "static" / "room" / "pool.js").read_text(encoding="utf-8")
    VIEW = (ROOT / "static" / "room" / "editor-view.js").read_text(encoding="utf-8")

    def test_the_card_says_rainy_day_and_offers_undo(self):
        flow = self.POOL.split("async function parkEssay(id)", 1)[1][:2200]
        self.assertIn("saved for a rainy day.`", flow)
        self.assertIn("undo: () => backToRoom(parkedId, title)", flow)

    def test_the_editor_says_it_too(self):
        self.assertIn("saved for a rainy day.`", self.VIEW)
        self.assertIn("undo: () => bringBack(", self.VIEW)


if __name__ == "__main__":
    unittest.main()
