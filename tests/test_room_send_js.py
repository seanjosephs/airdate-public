"""Send to substack in the room: the rules that live in the browser.

The server's preflight is the only judge of what Substack would reject; these
are the rules for routing its rows (build spec §15.2): the persisted rows fold
into one, a gap the server names twice shows once, the connection rows shut
gate 1 instead of joining the list, and a source note gets one row of its own.
Then the three gates and the line under the button, and what a failed send
says: who refused, why, whether anything was sent, never an "API key", and
a timeout that never claims nothing was sent.
"""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "static" / "room" / "editor.js"
TZ = "America/Los_Angeles"
NODE = shutil.which("node")

if NODE is None:  # pragma: no cover - depends on the machine
    raise RuntimeError("node is required for tests/test_room_send_js.py and was not found on PATH")


def run(expression: str):
    script = f"""
const E = require({json.dumps(str(HELPER))});
const result = (() => {{ {expression} }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
"""
    env = dict(os.environ, TZ=TZ)
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True, env=env)
    return json.loads(completed.stdout)


def js(value) -> str:
    return json.dumps(value)


# What the editor holds for a note with every detail filled in.
VALUES = {
    "title": "Borrowed Light",
    "subtitle": "a subtitle",
    "summary": "A line about the essay.",
    "publication": "https://example.substack.com",
    "audience": "everyone",
    "comment_permissions": "everyone",
    "email_subject": "Borrowed Light",
    "email_preview_text": "A line about the essay.",
    "hero_image": "_assets/hero.png",
    "thumbnail_alt": "Borrowed Light",
    "seo_title": "Borrowed Light",
    "seo_description": "A line about the essay.",
    "social_title": "Borrowed Light",
    "social_description": "A line about the essay.",
    "thumbnail_prompt": "a lamp",
    "tags": ["craft"],
}
SIMPLIFIED = {
    "mode": "simplified", "totem": True, "on_the_board": True, "advanced": False,
    "email_comments": False, "seo_social": False, "thumbnail": False, "notes": False,
}
COMPLETE = {key: True for key in SIMPLIFIED} | {"mode": "complete"}

CONNECTED = {
    "connected": True,
    "connector": {"paired": True, "available": True, "connected": True},
    "publication": "https://example.substack.com",
    "publication_configured": True,
}


def blocker(key, field="", message="", **extra):
    return {"key": key, "field": field, "message": message or f"{key} is wrong.", **extra}


def persisted(field):
    return blocker(f"persisted_{field}", field, f"Save {field.replace('_', ' ')} to Obsidian before sending.")


def listing(blockers, values=None, visibility=None, body="The script.", **preflight):
    payload = {"blockers": blockers, **preflight}
    return run(
        f"return E.readinessList({js(payload)}, {js(VALUES if values is None else values)}, "
        f"{js(COMPLETE if visibility is None else visibility)}, {js(body)});"
    )


def gates(substack=CONNECTED, status="Ready for Air", readiness=None, connection=None):
    readiness = readiness if readiness is not None else {"state": "done", "count": 0, "source": False}
    return run(
        f"return E.sendGates({{ substack: {js(substack)}, status: {js(status)}, "
        f"readiness: {js(readiness)}, connection: {js(connection or [])} }});"
    )


def failure(result, substack=None, count=0):
    return run(f"return E.sendFailure({js(result)}, {js(substack)}, {js(count)});")


class ReadinessListTests(unittest.TestCase):
    def test_a_clean_preflight_is_an_empty_list(self):
        out = listing([])
        self.assertEqual(out["rows"], [])
        self.assertFalse(out["source"])

    def test_details_the_editor_holds_fold_into_one_save_row(self):
        out = listing([persisted("email_subject"), persisted("seo_title"), persisted("social_title")])
        self.assertEqual(len(out["rows"]), 1)
        row = out["rows"][0]
        self.assertEqual(row["text"], "press save: 3 details are not in the note yet")
        self.assertEqual(row["target"], "ed-save")
        self.assertEqual(listing([persisted("seo_title")])["rows"][0]["text"],
                         "press save: 1 detail is not in the note yet")

    def test_a_detail_the_editor_does_not_hold_sends_the_writer_to_it(self):
        values = dict(VALUES, summary="", tags=[])
        out = listing([persisted("summary"), persisted("tags"), persisted("seo_title")], values=values)
        targets = [row["target"] for row in out["rows"]]
        self.assertEqual(targets, ["ed-save", "ed-f-summary", "ed-tag-input"])
        summary = next(row for row in out["rows"] if row["target"] == "ed-f-summary")
        self.assertEqual(summary["text"], "the summary is empty. substack uses it as the preview")

    def test_a_gap_the_server_names_twice_shows_once(self):
        values = dict(VALUES, title="", subtitle="", hero_image="")
        out = listing([
            blocker("title", "title"), persisted("title"),
            blocker("subtitle", "subtitle"), persisted("subtitle"),
            blocker("hero", "hero_image"), persisted("hero_image"),
        ], values=values)
        self.assertEqual([row["key"] for row in out["rows"]], ["title", "subtitle", "hero"])

    def test_connection_rows_belong_to_gate_one(self):
        out = listing([
            blocker("publication", "publication"),
            blocker("substack_command"),
            blocker("substack_session"),
        ])
        self.assertEqual(out["rows"], [])
        self.assertEqual(out["connection"], ["publication", "substack_command", "substack_session"])

    def test_a_source_note_gets_one_row_instead_of_the_list(self):
        out = listing([blocker("source_role", "source_role"), persisted("summary"), blocker("hero", "hero_image")],
                      source_role="source")
        self.assertTrue(out["source"])
        self.assertEqual(len(out["rows"]), 1)
        self.assertEqual(out["rows"][0]["kind"], "source")
        self.assertIn("linked draft", out["rows"][0]["text"])

    def test_a_body_row_names_its_line_and_goes_to_the_script(self):
        out = listing([
            blocker("body_embed_14", "", "line 14: Obsidian embed ![[x]] cannot be resolved", line=14),
            blocker("body_table_5", "", "line 5: Table (3 rows) has no Substack equivalent", line=5),
        ])
        self.assertEqual([row["line"] for row in out["rows"]], [5, 14])
        self.assertEqual(out["rows"][0]["text"], "line 5: a table. substack has no tables")
        self.assertEqual({row["target"] for row in out["rows"]}, {"ed-f-body"})

    def test_a_body_kind_the_room_does_not_know_uses_the_server_words(self):
        out = listing([blocker("body_new_kind_3", "", "line 3: Something New happened here", line=3)])
        self.assertEqual(out["rows"][0]["text"], "line 3: something New happened here")

    def test_the_overflow_row_counts(self):
        out = listing([blocker("body_blockers_more", "", "...and 4 more", count=4)])
        self.assertEqual(out["rows"][0]["text"], "and 4 more problems in the script")

    def test_rows_are_in_page_order(self):
        values = dict(VALUES, summary="")
        out = listing([
            blocker("hero", "hero_image"),
            blocker("body_table_9", "", "line 9: t", line=9),
            persisted("summary"),
            persisted("seo_title"),
            blocker("subtitle", "subtitle"),
        ], values=values)
        self.assertEqual([row["target"] for row in out["rows"]],
                         ["ed-save", "ed-f-subtitle", "ed-f-summary", "ed-f-body", "ed-hero-file"])

    def test_a_field_in_a_switched_off_section_is_not_a_dead_link(self):
        values = dict(VALUES, email_subject="")
        out = listing([persisted("email_subject")], values=values, visibility=SIMPLIFIED)
        self.assertEqual(out["rows"][0]["target"], "")
        self.assertIn("email and comments, which is switched off", out["rows"][0]["text"])
        on = listing([persisted("email_subject")], values=values)
        self.assertEqual(on["rows"][0]["target"], "ed-f-email_subject")

    def test_an_unknown_row_keeps_the_servers_words_lowercased(self):
        out = listing([blocker("something_new", "", "Substack Wants A Thing.")])
        self.assertEqual(out["rows"][0]["text"], "substack Wants A Thing.")

    def test_every_row_is_lowercase_at_the_start(self):
        values = dict(VALUES, summary="", tags=[], title="")
        out = listing([persisted("summary"), persisted("tags"), blocker("title", "title"), persisted("thumbnail_alt")],
                      values=values)
        for row in out["rows"]:
            with self.subTest(row=row["text"]):
                self.assertEqual(row["text"][0], row["text"][0].lower())

    def test_the_heading(self):
        self.assertEqual(run("return E.readinessHeading(3, new Date(2026, 8, 30, 14, 14));"), "3 things to fix. checked 2:14pm")
        self.assertEqual(run("return E.readinessHeading(1, new Date(2026, 8, 30, 9, 5));"), "1 thing to fix. checked 9:05am")
        self.assertEqual(run("return E.readinessHeading(0, new Date(2026, 8, 30, 0, 0));"), "all clear. checked 12:00am")

    def test_line_range_counts_from_the_first_written_line(self):
        body = "\nfirst\n\nthird line\n"
        out = run(f"return E.lineRange({js(body)}, 3);")
        self.assertEqual(body[out["start"]:out["end"]], "third line")
        out = run(f"return E.lineRange({js('no leading blank')}, 1);")
        self.assertEqual(out, {"start": 0, "end": 16, "index": 0})
        # past the end stays inside the script
        two_lines = "a\nb"
        out = run(f"return E.lineRange({js(two_lines)}, 40);")
        self.assertEqual(out["index"], 1)


class GateTests(unittest.TestCase):
    CLEAR = {"state": "done", "count": 0, "source": False}
    ONE = {"state": "done", "count": 1, "source": False}

    def test_all_open(self):
        self.assertEqual(gates(), {"open": True, "shut": [], "line": ""})

    def test_board_then_readiness_reads_as_drawn(self):
        out = gates(status="Writers Likey", readiness=self.ONE)
        self.assertFalse(out["open"])
        self.assertEqual(out["line"], "not on the board yet. schedule it, then fix 1 thing below.")

    def test_a_writers_room_essay_is_told_to_star_it_first(self):
        out = gates(status="Writers Room", readiness=self.CLEAR)
        self.assertEqual(out["line"], "not on the board yet. star it for writers likey, then schedule it to send.")

    def test_one_then_per_line(self):
        out = gates(status="Writers Room", readiness=self.ONE)
        self.assertEqual(out["line"], "not on the board yet. star it for writers likey, then schedule it and fix 1 thing below.")

    def test_only_readiness_left(self):
        out = gates(readiness={"state": "done", "count": 2, "source": False})
        self.assertEqual(out["line"], "fix 2 things below to send.")
        self.assertEqual(out["shut"], ["readiness"])

    def test_all_three_shut_names_the_first_and_counts_the_rest(self):
        substack = dict(CONNECTED, connected=False, connector={"paired": True, "available": True, "connected": False})
        out = gates(substack=substack, status="Writers Likey", readiness=self.ONE)
        self.assertEqual(out["shut"], ["connected", "board", "readiness"])
        self.assertEqual(out["line"], "substack is not connected. connect it through obsidian, then schedule it and fix 1 thing below.")

    def test_ready_for_air_and_live_are_on_the_board(self):
        for status in ("Ready for Air", "Live"):
            with self.subTest(status=status):
                self.assertTrue(gates(status=status)["open"])
        for status in ("Writers Room", "Writers Likey", "Archived", ""):
            with self.subTest(status=status):
                self.assertIn("board", gates(status=status)["shut"])

    def test_gate_one_includes_the_publication(self):
        out = gates(substack=dict(CONNECTED, publication_configured=False))
        self.assertEqual(out["line"], "airdate has no substack address yet. add it in settings to send.")
        out = gates(connection=["publication"])
        self.assertEqual(out["shut"], ["connected"])

    def test_gate_one_says_what_is_wrong_with_the_connector(self):
        unpaired = {"connected": False, "connector": {"paired": False, "available": False}, "publication_configured": True}
        self.assertTrue(gates(substack=unpaired)["line"].startswith("the obsidian connector is not paired. pair it in obsidian"))
        closed = {"connected": False, "connector": {"paired": True, "available": False}, "publication_configured": True}
        self.assertTrue(gates(substack=closed)["line"].startswith("obsidian is not answering."))
        self.assertTrue(gates(substack=None)["line"].startswith("airdate could not check substack."))

    def test_a_source_note_points_at_its_row(self):
        out = gates(readiness={"state": "done", "count": 1, "source": True})
        self.assertEqual(out["line"], "make a linked draft below to send.")

    def test_still_checking_keeps_send_shut(self):
        out = run("return E.sendGates({ status: 'Ready for Air', readiness: { state: 'loading' } });")
        self.assertFalse(out["open"])
        self.assertIn("checking", out["line"])
        self.assertFalse(gates(readiness={"state": "error"})["open"])

    def test_no_line_mentions_a_key(self):
        cases = [
            gates(substack=None),
            gates(substack={"connected": False, "connector": {}}),
            gates(substack=dict(CONNECTED, publication_configured=False)),
            gates(connection=["substack_session"]),
        ]
        for out in cases:
            with self.subTest(line=out["line"]):
                self.assertNotIn("key", out["line"])

    def test_the_connection_line_mirrors_a_refused_session(self):
        out = run(f"return E.connectionLine({js(CONNECTED)}, true);")
        self.assertEqual(out["name"], "substack: session refused")
        self.assertEqual(out["tone"], "off")
        out = run(f"return E.connectionLine({js(CONNECTED)}, false);")
        self.assertEqual(out, {"tone": "connected", "name": "substack connected", "sub": "example.substack.com"})


class SendResultTests(unittest.TestCase):
    AUTH = {
        "ok": False, "error_kind": "auth", "transport": "obsidian_connector",
        "message": "Substack draft creation failed: 401 Unauthorized (Substack refused the session.)",
    }
    TIMEOUT = {
        "ok": False, "error_kind": "timeout", "transport": "obsidian_connector",
        "message": "Substack draft command did not finish within 120s. Check Substack for the draft before sending again.",
    }

    def test_only_a_refused_session_retries_and_only_once_after_signing_in(self):
        self.assertTrue(run(f"return E.shouldRetrySend({js(self.AUTH)}, 1, true);"))
        self.assertFalse(run(f"return E.shouldRetrySend({js(self.AUTH)}, 1, false);"))
        self.assertFalse(run(f"return E.shouldRetrySend({js(self.AUTH)}, 2, true);"))
        self.assertFalse(run(f"return E.shouldRetrySend({js(self.TIMEOUT)}, 1, true);"))
        self.assertFalse(run("return E.shouldRetrySend({ ok: false, error_kind: 'transport' }, 1, true);"))
        self.assertFalse(run("return E.shouldRetrySend({ ok: true }, 1, true);"))

    def test_a_refused_session_says_who_why_and_that_no_draft_was_made(self):
        out = failure(self.AUTH)
        self.assertEqual(out["sentence"], "substack refused the session obsidian holds (401). no draft was made.")
        self.assertEqual(out["actions"], ["connect", "retry"])
        self.assertTrue(out["refused"])

    def test_a_timeout_never_claims_nothing_was_sent(self):
        out = failure(self.TIMEOUT)
        self.assertEqual(out["sent"], "maybe")
        self.assertIn("a draft may already exist", out["sentence"])
        self.assertIn("check substack before you send again", out["sentence"])
        for claim in ("nothing was sent", "no draft was made", "not sent"):
            self.assertNotIn(claim, out["sentence"])
        self.assertNotIn("retry", out["actions"])
        self.assertIn("(timed out)", out["sentence"])

    def test_no_failure_mentions_an_api_key(self):
        results = [
            self.AUTH, self.TIMEOUT,
            {"ok": False, "error_kind": "auth", "message": "Saved locally."},
            {"ok": False, "error_kind": "blocked"},
            {"ok": False, "error_kind": "transport", "transport": "obsidian_connector", "message": "boom 502"},
            {"ok": False, "error_kind": "transport", "transport": "obsidian_connector", "stale_draft_id": "123"},
            {"ok": False, "error_kind": "transport"},
            {"error_kind": "network"},
            {"error_kind": "http", "message": "airdate answered 500."},
            {"error_kind": "setup"},
            {"error_kind": "refused", "message": "Not today."},
        ]
        for result in results:
            for substack in (None, CONNECTED, {"connector": {"paired": False}}):
                with self.subTest(result=result, substack=substack):
                    sentence = failure(result, substack, 2)["sentence"]
                    self.assertNotIn("key", sentence.lower())
                    self.assertNotIn("api", sentence.lower())
                    self.assertEqual(sentence, sentence.lower() if result.get("error_kind") != "refused" else sentence)

    def test_unknown_outcomes_say_to_check_substack(self):
        for result in (
            {"error_kind": "network"},
            {"error_kind": "http"},
            {"ok": False, "error_kind": "transport", "transport": "obsidian_connector", "message": "Substack said 502"},
        ):
            with self.subTest(result=result):
                out = failure(result)
                self.assertEqual(out["sent"], "maybe")
                self.assertIn("check substack before you send again", out["sentence"])

    def test_a_transport_failure_before_the_connector_sent_nothing(self):
        out = failure({"ok": False, "error_kind": "transport"}, {"connector": {"paired": False}})
        self.assertEqual(out["sent"], "no")
        self.assertIn("not paired", out["sentence"])

    def test_a_stale_draft_offers_to_forget_the_link(self):
        out = failure({"ok": False, "error_kind": "transport", "transport": "obsidian_connector", "stale_draft_id": "987"})
        self.assertEqual(out["actions"], ["forget"])
        self.assertEqual(out["stale"], "987")
        self.assertIn("draft 987", out["sentence"])

    def test_blocked_counts_the_list(self):
        self.assertEqual(failure({"ok": False, "error_kind": "blocked"}, None, 2)["sentence"],
                         "airdate stopped before substack: fix 2 things below. nothing was sent.")

    def test_where_open_substack_goes(self):
        self.assertEqual(run(f"return E.substackCheckUrl({js(CONNECTED)}, {{}});"), "https://example.substack.com/publish/posts")
        self.assertEqual(
            run(f"return E.substackCheckUrl({js(CONNECTED)}, {{ substack_draft_url: 'https://example.substack.com/p/x' }});"),
            "https://example.substack.com/p/x",
        )
        self.assertEqual(run("return E.substackCheckUrl({ publication: '', publication_configured: false }, {});"), "")

    def test_success_says_where_the_draft_is(self):
        out = run("return E.sendSuccess({ ok: true, edit_url: 'https://example.substack.com/p/d', warnings: ['Section was not applied'] });")
        self.assertEqual(out["url"], "https://example.substack.com/p/d")
        self.assertEqual(out["warnings"], ["section was not applied"])
        self.assertIn("did not publish", out["text"])
        self.assertEqual(run("return E.sendSuccess({ ok: true, edit_url: 'javascript:alert(1)' }).url;"), "")


def essay_state():
    return {
        "id": "e1",
        "title": "Borrowed Light",
        "status": "Ready for Air",
        "body": "The script.",
        "frontmatter": dict(VALUES, status="Ready for Air"),
        "metadata_defaults": {},
        "mtime": 1.0,
        "content_hash": "h1",
    }


class AdoptSendTests(unittest.TestCase):
    PREFIX = f"""
let s = E.fromEssay({js(essay_state())});
s.values.subtitle = 'a new line';
const sent = E.snapshot(s);
const payload = E.savePayload(s);
"""

    def test_a_failed_send_that_saved_adopts_the_save(self):
        out = run(self.PREFIX + """
const next = E.adoptSend(s, sent, payload, { ok: false, error_kind: 'auth',
  saved_state: { old_id: 'e1', new_id: 'u1', mtime: 2.0, content_hash: 'h2' } });
return { id: next.id, file: next.fileState, dirty: E.isDirty(next), sub: next.frontmatter.subtitle };
""")
        self.assertEqual(out, {"id": "u1", "file": {"mtime": 2.0, "content_hash": "h2"}, "dirty": False, "sub": "a new line"})

    def test_a_recorded_draft_takes_the_later_file_state(self):
        out = run(self.PREFIX + """
const next = E.adoptSend(s, sent, payload, { ok: true, draft_recorded: true, draft_id: 'd1',
  edit_url: 'https://example.substack.com/p/d1', mtime: 3.0, content_hash: 'h3',
  saved_state: { old_id: 'e1', new_id: 'e1', mtime: 2.0, content_hash: 'h2' } });
return { file: next.fileState, id: next.frontmatter.substack_draft_id, url: next.frontmatter.substack_draft_url,
  status: next.frontmatter.status, dirty: E.isDirty(next) };
""")
        self.assertEqual(out["file"], {"mtime": 3.0, "content_hash": "h3"})
        self.assertEqual(out["id"], "d1")
        self.assertEqual(out["url"], "https://example.substack.com/p/d1")
        self.assertEqual(out["status"], "Ready for Air")
        self.assertFalse(out["dirty"])

    def test_a_send_that_never_saved_changes_nothing(self):
        out = run(self.PREFIX + """
const next = E.adoptSend(s, sent, payload, { ok: false, error_kind: 'timeout', saved_state: null });
return { file: next.fileState, dirty: E.isDirty(next) };
""")
        self.assertEqual(out, {"file": {"mtime": 1.0, "content_hash": "h1"}, "dirty": True})

    def test_typing_during_the_send_stays_unsaved(self):
        out = run(self.PREFIX + """
s.values.summary = 'typed while it was sending';
const next = E.adoptSend(s, sent, payload, { ok: false, error_kind: 'auth',
  saved_state: { old_id: 'e1', new_id: 'e1', mtime: 2.0, content_hash: 'h2' } });
return E.changedFields(next.values, next.baseline);
""")
        self.assertEqual(out, ["summary"])

    def test_forgetting_the_link_drops_both_keys(self):
        state = essay_state()
        state["frontmatter"].update(substack_draft_id="d1", substack_draft_url="https://example.substack.com/p/d1")
        out = run(f"""
const s = E.fromEssay({js(state)});
const next = E.adoptForget(s, {{ mtime: 4.0, content_hash: 'h4' }});
return {{ fm: next.frontmatter, file: next.fileState }};
""")
        self.assertNotIn("substack_draft_id", out["fm"])
        self.assertNotIn("substack_draft_url", out["fm"])
        self.assertEqual(out["file"], {"mtime": 4.0, "content_hash": "h4"})

    def test_publish_values_never_carry_what_the_editor_never_sends(self):
        state = essay_state()
        state["frontmatter"].update(scheduled_at="2026-10-05", source_note="[[x]]")
        out = run(f"const s = E.fromEssay({js(state)}); s.values.section = ''; return E.publishValues(s);")
        for key in ("status", "scheduled_at", "source_note", "section"):
            self.assertNotIn(key, out)
        self.assertEqual(out["title"], "Borrowed Light")


if __name__ == "__main__":
    unittest.main()


class PressSaveDoesNotBlockSendTests(unittest.TestCase):
    """Sean, 2026-09-30: the 'press save' row does not block send.

    Pressing send saves first, which fills in exactly the details that row is
    about, so blocking on it only forces a save and then a send. The row stays
    in the list - it is still true and worth seeing - it just does not shut the
    button. Every other row still blocks."""

    def test_the_press_save_row_is_not_counted_as_blocking(self):
        rows = [{"key": "persisted", "text": "press save: 3 details are not in the note yet"}]
        self.assertEqual(run(f"return E.blockingCount({js(rows)});"), 0)

    def test_a_real_problem_still_counts(self):
        rows = [
            {"key": "persisted", "text": "press save: 3 details are not in the note yet"},
            {"key": "hero", "text": "no hero image yet"},
        ]
        self.assertEqual(run(f"return E.blockingCount({js(rows)});"), 1)

    def test_a_press_save_only_list_leaves_the_readiness_gate_open(self):
        self.assertIsNone(run("return E.readinessGate({state: 'ready', count: 0});"))

    def test_nothing_at_all_blocks_nothing(self):
        self.assertEqual(run("return E.blockingCount([]);"), 0)
        self.assertEqual(run("return E.blockingCount(null);"), 0)
