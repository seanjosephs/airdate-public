"""The room reads /api/app/status once and every view shares the answer.

Every read of /api/app/status asks the obsidian connector how it is, and a slow
connector takes up to five seconds to say. The pool, the board, the links, the
first-run check, the shelf, rainy day, the editor and settings all want the
same answer, so api.js reads it once (RoomApi.appStatus) and hands every view
the same promise. A write that changes the answer (save settings, connect,
reload the essays) reads again with { fresh: true }, and so does opening
settings, which shows the connection as it is now. A failed read is not kept.

api.js runs through node against a fake fetch that counts the reads.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOM = ROOT / "static" / "room"
API = ROOM / "api.js"
NODE = shutil.which("node")

if NODE is None:  # pragma: no cover - depends on the machine
    raise RuntimeError("node is required for tests/test_room_status.py and was not found on PATH")


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def run(body: str):
    """Run `body` (an async function body) with api.js loaded as RoomApi and a
    fake fetch. `answer(n)` is what the nth read returns; `fail(n)` makes the
    nth read fail; `hold()` keeps reads open until `release()`."""
    script = f"""
const reads = [];
let failing = new Set();
let held = null;
globalThis.fetch = async (url) => {{
  reads.push(url);
  const n = reads.length;
  if (held) await held.promise;
  if (failing.has(n)) throw new Error('down');
  return {{ ok: true, status: 200, json: async () => ({{ n, config: {{ read: n }} }}) }};
}};
const fail = (n) => failing.add(n);
const hold = () => {{ let resolve; const promise = new Promise((r) => {{ resolve = r; }}); held = {{ promise, resolve }}; }};
const release = () => {{ const h = held; held = null; h.resolve(); }};
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const Api = require({json.dumps(str(API))});
(async () => {{
  const result = await (async () => {{ {body} }})();
  process.stdout.write(JSON.stringify(result === undefined ? null : result));
}})().catch((error) => {{ process.stderr.write(String(error && error.stack || error)); process.exit(1); }});
"""
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, capture_output=True, text=True)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


class SharedReadTests(unittest.TestCase):
    def test_every_view_at_load_shares_one_read(self):
        out = run("""
hold();
const asks = [Api.appStatus(), Api.appStatus(), Api.appStatus(), Api.appStatus()];
release();
const answers = await Promise.all(asks);
return { reads, same: answers.every((answer) => answer === answers[0]), n: answers[0].n };
""")
        self.assertEqual(out["reads"], ["/api/app/status"])
        self.assertTrue(out["same"])
        self.assertEqual(out["n"], 1)

    def test_a_view_that_opens_later_takes_the_same_answer(self):
        out = run("""
const first = await Api.appStatus();
const later = await Api.appStatus();
return { reads: reads.length, same: first === later };
""")
        self.assertEqual(out, {"reads": 1, "same": True})

    def test_fresh_reads_again_and_later_views_take_the_new_answer(self):
        out = run("""
await Api.appStatus();
const fresh = await Api.appStatus({ fresh: true });
const after = await Api.appStatus();
return { reads: reads.length, fresh: fresh.n, after: after.n };
""")
        self.assertEqual(out, {"reads": 2, "fresh": 2, "after": 2})

    def test_fresh_reads_again_even_while_a_read_is_open(self):
        # A save must not be answered by a read that started before it.
        out = run("""
hold();
const before = Api.appStatus();
const fresh = Api.appStatus({ fresh: true });
release();
return { reads: reads.length, before: (await before).n, fresh: (await fresh).n, after: (await Api.appStatus()).n };
""")
        self.assertEqual(out, {"reads": 2, "before": 1, "fresh": 2, "after": 2})

    def test_a_failed_read_is_not_kept(self):
        out = run("""
fail(1);
let kind = '';
try { await Api.appStatus(); } catch (error) { kind = error.kind; }
const again = await Api.appStatus();
return { kind, reads: reads.length, again: again.n };
""")
        self.assertEqual(out, {"kind": "network", "reads": 2, "again": 2})

    def test_an_old_read_failing_does_not_drop_a_newer_answer(self):
        out = run("""
fail(1);
hold();
const old = Api.appStatus().catch(() => 'failed');
const fresh = Api.appStatus({ fresh: true });
release();
const results = [await old, (await fresh).n];
await settle();
const after = await Api.appStatus();
return { results, reads: reads.length, after: after.n };
""")
        self.assertEqual(out, {"results": ["failed", 2], "reads": 2, "after": 2})

    def test_it_reads_the_status_route(self):
        out = run("await Api.appStatus(); return reads;")
        self.assertEqual(out, ["/api/app/status"])


class EveryViewUsesTheSharedReadTests(unittest.TestCase):
    """Only api.js names the route; every view asks RoomApi.appStatus."""

    VIEWS = ("pool.js", "board-view.js", "links.js", "shelf-view.js", "rainy-day.js", "editor-view.js", "settings.js")

    def test_only_api_js_names_the_status_route(self):
        named = sorted(path.name for path in ROOM.glob("*.js") if "/api/app/status'" in path.read_text(encoding="utf-8"))
        self.assertEqual(named, ["api.js"])
        for path in (ROOT / "static").glob("*.js"):
            with self.subTest(file=path.name):
                self.assertNotIn("/api/app/status", path.read_text(encoding="utf-8"))

    def test_each_view_asks_the_shared_read(self):
        for name in self.VIEWS:
            with self.subTest(view=name):
                self.assertIn(".appStatus(", read(f"static/room/{name}"))

    def test_settings_reads_fresh_when_it_loads_and_shares_for_the_first_run_check(self):
        source = read("static/room/settings.js")
        load = source[source.index("async function load()"):source.index("function bind()")]
        self.assertIn("appStatus({ fresh: true })", load)
        wizard = source[source.index("async function maybeOpenWizard()"):source.index("function start()")]
        self.assertRegex(wizard, r"appStatus\(\)")
        self.assertNotIn("fresh: true", wizard)

    def test_the_editor_keeps_its_own_copy_of_the_config(self):
        # The editor records its own settings (script height, tour done) into
        # its config after saving them, so it must not write into the answer
        # the other views share.
        source = read("static/room/editor-view.js")
        load = source[source.index("function loadConfig()"):source.index("function totemItems()")]
        self.assertIn("Api.appStatus()", load)
        self.assertRegex(load, r"config = \{ \.\.\.\(\(status && status\.config\) \|\| \{\}\) \}")

    def test_api_js_loads_before_every_view(self):
        html = read("room.html")
        api_at = html.index("/static/room/api.js")
        for name in self.VIEWS:
            with self.subTest(view=name):
                match = re.search(rf'<script src="/static/room/{re.escape(name)}\?v=\d+" defer></script>', html)
                self.assertIsNotNone(match, f"{name} is not loaded")
                self.assertLess(api_at, match.start())


if __name__ == "__main__":
    unittest.main()
