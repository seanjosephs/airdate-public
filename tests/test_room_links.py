"""AD-038, the writer's own links in the sidebar.

config.json can hold "links": [{"label", "url"}]. The server has always read,
checked and sent them; the room drew them nowhere. links.js draws them in the
sidebar, under the room's own nav: http and https only, each in a new tab, and
nothing at all when there are none. (Not pinned to the bottom: the sidebar
runs the whole height of the page, so a pinned link would sit far below the
fold on a long essays view.)

links.js holds the rules and runs through node. Its start() runs against a
small fake DOM that answers /api/app/status with the links a test gives it.
"""

import copy
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_room_hardening import RoomCase  # noqa: E402

LINKS = ROOT / "static" / "room" / "links.js"
NODE = shutil.which("node")

if NODE is None:  # pragma: no cover - depends on the machine
    raise RuntimeError("node is required for tests/test_room_links.py and was not found on PATH")


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def js(value) -> str:
    return json.dumps(value)


def pure(expression: str):
    script = f"""
const Links = require({js(str(LINKS))});
const result = (() => {{ return {expression}; }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
"""
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def started(links_value, fail: str = ""):
    """What links.js did to #room-links when the page loaded."""
    script = f"""
const nav = {{ hidden: true, innerHTML: '' }};
const calls = [];
globalThis.window = globalThis;
globalThis.document = {{
  readyState: 'complete',
  getElementById: (id) => (id === 'room-links' ? nav : null),
  addEventListener() {{}},
}};
globalThis.RoomApi = {{
  async getJson(url) {{
    calls.push(url);
    if ({js(fail)}) {{ const error = new Error('no'); error.kind = {js(fail)}; throw error; }}
    return {{ config: {{ links: {js(links_value)} }} }};
  }},
}};
require({js(str(LINKS))});
(async () => {{
  await new Promise((resolve) => setTimeout(resolve, 20));
  process.stdout.write(JSON.stringify({{ hidden: nav.hidden, html: nav.innerHTML, calls }}));
}})();
"""
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, capture_output=True, text=True)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


# ---- which links are drawn ---------------------------------------------------------


class UsableLinksTests(unittest.TestCase):
    def usable(self, value):
        return pure(f"Links.usable({js(value)})")

    def test_http_and_https_links_with_a_label_are_kept(self):
        self.assertEqual(
            self.usable([
                {"label": "my dashboard", "url": "http://127.0.0.1:9000/"},
                {"label": "  the blog ", "url": " https://example.com/blog "},
            ]),
            [{"label": "my dashboard", "url": "http://127.0.0.1:9000/"},
             {"label": "the blog", "url": "https://example.com/blog"}],
        )

    def test_nothing_else_is_ever_a_link(self):
        bad_urls = ["javascript:alert(1)", "JAVASCRIPT:alert(1)", "data:text/html,x", "ftp://example.com/",
                    "//example.com/", "/relative", "example.com", "http://", "https://", "http://a b", "", None, 7]
        for url in bad_urls:
            with self.subTest(url=url):
                self.assertEqual(self.usable([{"label": "x", "url": url}]), [])

    def test_a_link_needs_a_label(self):
        for label in ("", "   ", None, 3):
            with self.subTest(label=label):
                self.assertEqual(self.usable([{"label": label, "url": "https://example.com/"}]), [])

    def test_entries_that_are_not_links_are_skipped(self):
        good = {"label": "ok", "url": "https://example.com/"}
        self.assertEqual(self.usable([None, "https://example.com/", 4, [], {}, good]), [good])

    def test_not_a_list_is_no_links(self):
        for value in (None, {}, "https://example.com/", 5):
            with self.subTest(value=value):
                self.assertEqual(self.usable(value), [])


class LinkMarkupTests(unittest.TestCase):
    def markup(self, value):
        return pure(f"Links.markup({js(value)})")

    def test_a_link_opens_in_a_new_tab_and_says_so(self):
        html = self.markup([{"label": "my dashboard", "url": "http://127.0.0.1:9000/"}])
        self.assertIn('href="http://127.0.0.1:9000/"', html)
        self.assertIn('target="_blank"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertIn("my dashboard", html)
        self.assertIn('<span class="visually-hidden"> (opens in a new tab)</span>', html)

    def test_the_links_keep_their_order(self):
        html = self.markup([
            {"label": "second", "url": "https://b.test/"},
            {"label": "first", "url": "https://a.test/"},
        ])
        self.assertLess(html.index("second"), html.index("first"))

    def test_the_writers_words_are_escaped(self):
        html = self.markup([{"label": '<b>x</b> & "y"', "url": "https://x.test/?a=1&b=2"}])
        self.assertNotIn("<b>", html)
        self.assertIn("&lt;b&gt;x&lt;/b&gt; &amp; &quot;y&quot;", html)
        self.assertIn('href="https://x.test/?a=1&amp;b=2"', html)

    def test_no_usable_link_is_no_markup(self):
        self.assertEqual(self.markup([]), "")
        self.assertEqual(self.markup([{"label": "x", "url": "javascript:alert(1)"}]), "")


# ---- the page --------------------------------------------------------------------------


class StartTests(unittest.TestCase):
    LINKS = [{"label": "my dashboard", "url": "http://127.0.0.1:9000/"},
             {"label": "the blog", "url": "https://example.com/blog"}]

    def test_the_links_are_drawn_and_the_nav_shown(self):
        out = started(self.LINKS)
        self.assertFalse(out["hidden"])
        self.assertEqual(out["html"].count("<a "), 2)
        self.assertEqual(out["calls"], ["/api/app/status"])

    def test_no_links_leaves_the_nav_hidden(self):
        out = started([])
        self.assertTrue(out["hidden"])
        self.assertEqual(out["html"], "")

    def test_only_unusable_links_leave_it_hidden(self):
        self.assertTrue(started([{"label": "x", "url": "javascript:alert(1)"}])["hidden"])

    def test_a_failed_status_leaves_it_hidden_and_does_not_throw(self):
        for kind in ("network", "setup", "http"):
            with self.subTest(kind=kind):
                out = started(self.LINKS, fail=kind)
                self.assertTrue(out["hidden"])
                self.assertEqual(out["html"], "")


class SidebarHtmlTests(unittest.TestCase):
    def setUp(self):
        self.html = read("room.html")

    def test_the_nav_is_in_the_sidebar_after_the_main_nav_and_starts_hidden(self):
        nav = re.search(r'<nav class="room-links" id="room-links" aria-label="your links" hidden></nav>', self.html)
        self.assertIsNotNone(nav, "no #room-links nav")
        sidebar = self.html[self.html.index('<aside class="room-sidebar">'):self.html.index("</aside>")]
        self.assertIn('id="room-links"', sidebar)
        self.assertLess(sidebar.index('class="room-nav"'), sidebar.index('id="room-links"'))

    def test_the_script_is_loaded_after_the_api_helper_and_versioned(self):
        match = re.search(r'<script src="/static/room/links\.js\?v=\d+" defer></script>', self.html)
        self.assertIsNotNone(match, "links.js is not loaded")
        self.assertLess(self.html.index("/static/room/api.js"), match.start())

    def test_the_links_are_styled(self):
        css = read("static/room/room.css")
        for selector in (".room-links {", ".room-links a {", ".room-links svg {"):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)

    def test_the_stylesheet_that_changed_has_a_new_version(self):
        self.assertNotIn("/static/room/room.css?v=4", self.html)
        self.assertRegex(self.html, r"/static/room/room\.css\?v=\d+")


class WordsTests(unittest.TestCase):
    def test_setup_says_where_the_links_appear(self):
        doc = read("SETUP.md")
        start = doc.index("- `links`")
        bullet = doc[start:doc.index("\n- ", start + 1)]
        self.assertIn("sidebar", bullet)
        self.assertNotIn("does not draw", bullet)
        self.assertNotIn("yet", bullet)


class ServerStillSendsThemTests(RoomCase):
    """The room draws what /api/app/status carries. If the server stopped
    sending them the sidebar would go quiet with nothing to say why."""

    def test_the_status_carries_the_complete_links(self):
        import airdate_config  # noqa: PLC0415
        config = copy.deepcopy(airdate_config.DEFAULT_CONFIG)
        config["vault"]["path"] = str(self.root / "vault")
        config["vault"]["essays_folder"] = "Essays"
        config["links"] = [
            {"label": "my dashboard", "url": "http://127.0.0.1:9000/"},
            {"label": "no address"},
            {"url": "https://example.com/"},
        ]
        self.server.apply_config(config, True)
        self.assertEqual(self.server.ui_config_payload()["links"],
                         [{"label": "my dashboard", "url": "http://127.0.0.1:9000/"}])


if __name__ == "__main__":
    unittest.main()
