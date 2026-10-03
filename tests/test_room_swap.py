"""Slice 7, the swap: /airdate serves the writers room and the old page is gone.

What this pins:

- the routes: /airdate is the room, /airdate/room redirects to it with its
  query string (old ?essay= links still open their essay), / still goes to
  /airdate, and the old page's files are no longer served;
- every room link spells /airdate, and the two places that read the path
  back still work: a plain click on a card opens the editor in place (not a
  full page load), and the sidebar's aria-current follows the hash;
- the home tour on the room: ten stops anchored in room.html, styled by the
  room, started once after first-run setup and replayable from settings;
- the send fields the editor now draws (social title, description and image,
  thumbnail alt text, free unlock date) and their save round trip.

The view files (editor-view.js, settings.js) run in node against a small
fake DOM, ROOM_DOM below: element lookups by id return stand-ins that record
attributes and listeners, and fetch, history, location and sessionStorage are
fakes that record what the page asked of them.
"""

import http.client
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

from test_room_editor import EditorCase, HttpCase  # noqa: E402

ROOM_HTML = ROOT / "room.html"
NODE = shutil.which("node")
if NODE is None:  # pragma: no cover - depends on the machine
    raise RuntimeError("node is required for tests/test_room_swap.py and was not found on PATH")


# ---- the fake DOM ------------------------------------------------------------

ROOM_DOM = r"""
const ROOT = __ROOT__;
const els = new Map();
const docListeners = [];
const winListeners = [];
const navLinks = [];
const record = { history: [], replaced: [], slips: [], calls: [], prevented: 0, tour: [], fetched: [] };
function makeEl(id) {
  const attrs = {};
  const listeners = [];
  const q = new Map();
  const el = {
    id, hidden: false, textContent: '', innerHTML: '', value: '', className: '', dataset: {}, style: {},
    tabIndex: 0, disabled: false, isConnected: true, type: '', placeholder: '', files: null, checked: false,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
    removeAttribute(k) { delete attrs[k]; }, hasAttribute(k) { return k in attrs; },
    addEventListener(type, fn) { listeners.push({ type, fn }); }, removeEventListener() {},
    focus() { globalThis.document.activeElement = el; }, blur() {}, select() {}, setSelectionRange() {},
    querySelector(sel) { if (!q.has(sel)) q.set(sel, makeEl(sel)); return q.get(sel); },
    querySelectorAll() { return []; }, closest() { return null; },
    contains() { return false; }, append() {}, appendChild() {}, remove() {}, replaceWith() {},
    getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; },
    getClientRects() { return []; }, scrollIntoView() {}, setPointerCapture() {}, releasePointerCapture() {},
    hasPointerCapture() { return false; }, matches() { return false; },
    fire(type, extra) { for (const l of listeners) if (l.type === type) l.fn({ type, target: el, preventDefault() {}, ...(extra || {}) }); },
    attrs,
  };
  return el;
}
function byId(id) { if (!els.has(id)) els.set(id, makeEl(id)); return els.get(id); }
for (const href of ['/airdate', '/airdate#shelf', '/airdate#rainy-day', '/airdate#settings']) {
  const link = makeEl(`nav ${href}`);
  link.setAttribute('href', href);
  navLinks.push(link);
}
globalThis.window = globalThis;
globalThis.location = {
  search: '', hash: '', pathname: '/airdate', origin: 'http://127.0.0.1:8788',
  get href() { return `http://127.0.0.1:8788${this.pathname}${this.search}${this.hash}`; },
  replace(url) { record.replaced.push(url); },
  reload() { record.replaced.push('reload'); },
};
globalThis.history = {
  state: null,
  pushState(s, t, url) { record.history.push(['push', url]); },
  replaceState(s, t, url) { record.history.push(['replace', url]); },
  back() { record.history.push(['back']); },
};
const store = {};
globalThis.sessionStorage = {
  getItem(k) { return k in store ? store[k] : null; }, setItem(k, v) { store[k] = String(v); }, removeItem(k) { delete store[k]; },
};
globalThis.document = {
  readyState: 'complete', activeElement: null, body: makeEl('body'),
  getElementById: byId,
  querySelector() { return null; },
  querySelectorAll(sel) { return sel === '.room-nav a' ? navLinks : []; },
  addEventListener(type, fn, opts) { docListeners.push({ type, fn, once: Boolean(opts && opts.once) }); },
  removeEventListener() {},
  dispatchEvent(event) { fireDoc(event.type, event); return true; },
  createElement: makeEl,
};
function fireDoc(type, event) {
  for (const l of docListeners.slice()) {
    if (l.type !== type) continue;
    if (l.once) docListeners.splice(docListeners.indexOf(l), 1);
    l.fn(event || { type });
  }
}
function fireWin(type) {
  for (const l of winListeners.slice()) {
    if (l.type !== type) continue;
    if (l.once) winListeners.splice(winListeners.indexOf(l), 1);
    l.fn({ type });
  }
}
globalThis.addEventListener = (type, fn, opts) => winListeners.push({ type, fn, once: Boolean(opts && opts.once) });
globalThis.removeEventListener = () => {};
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
globalThis.matchMedia = () => ({ matches: false });
globalThis.CSS = { escape: (v) => String(v) };
globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } };
globalThis.navigator = {};
globalThis.RoomFeedback = { slip(o) { record.slips.push(o); }, plaque() {}, clear() {} };
require(ROOT + '/static/room/cards.js');
require(ROOT + '/static/room/editor.js');
const ESSAYS = {
  known: { id: 'known', title: 'Known', body: 'words', frontmatter: { title: 'Known' }, row: { id: 'known', status: 'Writers Room' } },
};
globalThis.RoomApi = {
  async getJson(url) {
    record.calls.push(url);
    if (url === '/api/app/status') return { config: {} };
    const m = url.match(/^\/api\/essays\/([^/?]+)$/);
    if (m) {
      const essay = ESSAYS[decodeURIComponent(m[1])];
      if (essay) return essay;
      const error = new Error('Not found');
      error.kind = 'not-found';
      throw error;
    }
    return {};
  },
  async postJson(url) { record.calls.push(url); return {}; },
};
// settings.js has its own fetch wrappers.
globalThis.__status = { setup: {}, config: {} };
globalThis.fetch = async (url) => {
  record.fetched.push(url);
  return { ok: true, status: 200, json: async () => globalThis.__status };
};
globalThis.AirdateTour = {
  state: '',
  tourState() { return this.state; },
  placeBeside() {},
  start(options) { record.tour.push('start'); record.tourOptions = options; return true; },
  replay(options) { record.tour.push('replay'); record.tourOptions = options; return true; },
};
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));
"""


def run_room(body: str):
    """Run `body` (async, may await settle()) after the fake DOM is up and
    print what it returns as JSON."""
    script = ROOM_DOM.replace("__ROOT__", json.dumps(str(ROOT))) + f"""
(async () => {{
  const result = await (async () => {{ {body} }})();
  process.stdout.write(JSON.stringify(result === undefined ? null : result));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, capture_output=True, text=True)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


def run_pure(expression: str):
    script = f"""
const Cards = require({json.dumps(str(ROOT / "static" / "room" / "cards.js"))});
const E = require({json.dumps(str(ROOT / "static" / "room" / "editor.js"))});
const tour = require({json.dumps(str(ROOT / "static" / "airdate-tour.js"))});
process.stdout.write(JSON.stringify((() => {{ return {expression}; }})()));
"""
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


# ---- the routes ----------------------------------------------------------------


class RouteTests(HttpCase):
    def fetch(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_airdate_is_the_room(self):
        status, headers, body = self.fetch("/airdate")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("content-type", ""))
        self.assertEqual(body, ROOM_HTML.read_bytes())

    def test_the_old_room_address_redirects_to_airdate(self):
        status, headers, _ = self.fetch("/airdate/room")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/airdate")

    def test_the_redirect_carries_the_query_string(self):
        # An old deep link must still open its essay.
        status, headers, _ = self.fetch("/airdate/room?essay=c5e72f4c-c829%20x&from=mail")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/airdate?essay=c5e72f4c-c829%20x&from=mail")

    def test_the_root_still_goes_to_airdate(self):
        status, headers, _ = self.fetch("/")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/airdate")

    def test_the_old_page_is_not_served(self):
        for path in ("/static/airdate.js", "/static/airdate.css", "/static/tokens.css",
                     "/static/airdate-filters.js", "/static/airdate-deep-link.js"):
            with self.subTest(path=path):
                status, _, _ = self.fetch(path)
                self.assertEqual(status, 404)

    def test_the_room_assets_the_page_names_are_served(self):
        html = ROOM_HTML.read_text(encoding="utf-8")
        assets = re.findall(r'(?:src|href)="(/static/[^"?]+)', html)
        self.assertGreater(len(assets), 20)
        for path in assets:
            with self.subTest(path=path):
                status, _, _ = self.fetch(path)
                self.assertEqual(status, 200)


class RedirectLocationTests(unittest.TestCase):
    def setUp(self):
        import server  # noqa: PLC0415
        self.server = server

    def test_no_query_is_plain_airdate(self):
        self.assertEqual(self.server.room_redirect_location(""), "/airdate")

    def test_a_line_break_never_reaches_the_header(self):
        self.assertEqual(self.server.room_redirect_location("essay=a\r\nSet-Cookie: x"), "/airdate?essay=aSet-Cookie: x")


class OldPageIsGoneTests(unittest.TestCase):
    def test_the_old_files_are_deleted(self):
        for name in ("airdate.html", "static/airdate.js", "static/airdate.css", "static/tokens.css",
                     "static/airdate-filters.js", "static/airdate-deep-link.js"):
            with self.subTest(name=name):
                self.assertFalse((ROOT / name).exists())

    def test_the_shared_modules_the_room_loads_stay(self):
        html = ROOM_HTML.read_text(encoding="utf-8")
        for name in ("airdate-tour.js", "airdate-presets.js", "airdate-wizard.js"):
            with self.subTest(name=name):
                self.assertTrue((ROOT / "static" / name).exists())
                self.assertIn(f"/static/{name}?v=", html)


# ---- the links -------------------------------------------------------------------


class RoomLinkTests(unittest.TestCase):
    def test_no_room_file_spells_the_old_address(self):
        for path in [ROOM_HTML, *sorted((ROOT / "static").rglob("*.js"))]:
            with self.subTest(path=path.name):
                self.assertNotIn("/airdate/room", path.read_text(encoding="utf-8"))

    def test_the_sidebar_links_are_airdate_and_its_hashes(self):
        html = ROOM_HTML.read_text(encoding="utf-8")
        nav = html[html.index('<nav class="room-nav"'):html.index("</nav>")]
        self.assertEqual(re.findall(r'href="([^"]+)"', nav),
                         ["/airdate", "/airdate#shelf", "/airdate#rainy-day", "/airdate#settings"])
        self.assertIn('<a class="room-brand" href="/airdate">', html)

    def test_editor_links_and_the_room_url(self):
        self.assertEqual(run_pure("Cards.editorHref({id: 'a b'})"), "/airdate?essay=a%20b")
        self.assertEqual(run_pure("E.editorUrl('a b')"), "/airdate?essay=a%20b")
        self.assertEqual(run_pure("[E.roomUrl(''), E.roomUrl('#shelf')]"), ["/airdate", "/airdate#shelf"])

    def test_a_card_link_is_the_editors(self):
        here = "http://127.0.0.1:8787/airdate#shelf"
        cases = {
            "/airdate?essay=abc": "abc",
            "http://127.0.0.1:8787/airdate?essay=a%20b": "a b",
            # The old address is a real navigation now; the server redirects it.
            "/airdate/room?essay=abc": "",
            "/airdate": "",
            "https://example.com/airdate?essay=abc": "",
            "/airdate?other=1": "",
        }
        for href, expected in cases.items():
            with self.subTest(href=href):
                self.assertEqual(run_pure(f"E.essayFromLink({json.dumps(href)}, {json.dumps(here)})"), expected)

    def test_a_route_is_read_from_the_hash_alone(self):
        self.assertEqual(
            run_pure("['/airdate', '/airdate#shelf', '/airdate#rainy-day', '/airdate#settings', '#settings', '/airdate#nope']"
                     ".map(Cards.routeOfHref)"),
            ["essays", "shelf", "rainy-day", "settings", "settings", "essays"],
        )

    def test_finish_setup_from_the_pool_goes_to_settings(self):
        pool = (ROOT / "static" / "room" / "pool.js").read_text(encoding="utf-8")
        self.assertIn('<a href="/airdate#settings">settings</a>', pool)


class CardClickOpensInPlaceTests(unittest.TestCase):
    """editor-view.js takes a plain click on a card title and opens the editor
    without loading the page again. Before the swap it matched the path
    /airdate/room; left as it was, every card click would be a page load."""

    def click(self, href, **keys):
        return run_room(f"""
require(ROOT + '/static/room/editor-view.js');
byId('editor').hidden = true;
const link = makeEl('card-link');
link.href = new URL({json.dumps(href)}, location.href).href;
let prevented = false;
const event = {{ type: 'click', button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false,
  defaultPrevented: false, ...{json.dumps(keys)},
  target: {{ closest: (sel) => (sel === 'a.card-link' ? link : null) }},
  preventDefault() {{ prevented = true; }} }};
fireDoc('click', event);
await settle();
return {{ prevented, history: record.history, calls: record.calls, open: !byId('editor').hidden }};
""")

    def test_a_plain_click_opens_the_editor_in_place(self):
        out = self.click("/airdate?essay=known")
        self.assertTrue(out["prevented"], "the browser was left to load the page")
        self.assertTrue(out["open"])
        self.assertIn("/api/essays/known", out["calls"])
        self.assertIn(["push", "/airdate?essay=known"], out["history"])

    def test_a_modified_click_is_the_browsers(self):
        out = self.click("/airdate?essay=known", metaKey=True)
        self.assertFalse(out["prevented"])
        self.assertFalse(out["open"])

    def test_a_link_somewhere_else_is_followed(self):
        out = self.click("/airdate/room?essay=known")
        self.assertFalse(out["prevented"])


class SidebarRouteTests(unittest.TestCase):
    """settings.js owns aria-current on the sidebar for every route. It used to
    find the route by stripping '/airdate/room' from each href."""

    def current(self, hash_):
        return run_room(f"""
location.hash = {json.dumps(hash_)};
require(ROOT + '/static/room/settings.js');
await settle();
return navLinks.filter((l) => l.getAttribute('aria-current') === 'page').map((l) => l.getAttribute('href'));
""")

    def test_each_route_marks_its_own_link(self):
        for hash_, href in (("", "/airdate"), ("#shelf", "/airdate#shelf"),
                            ("#rainy-day", "/airdate#rainy-day"), ("#settings", "/airdate#settings")):
            with self.subTest(hash=hash_):
                self.assertEqual(self.current(hash_), [href])

    def test_a_hash_change_moves_it(self):
        out = run_room("""
require(ROOT + '/static/room/settings.js');
location.hash = '#shelf';
fireWin('hashchange');
return navLinks.filter((l) => l.getAttribute('aria-current') === 'page').map((l) => l.getAttribute('href'));
""")
        self.assertEqual(out, ["/airdate#shelf"])


# ---- the home tour ---------------------------------------------------------------


class HomeTourAnchorTests(unittest.TestCase):
    """The ten keys stay pinned in test_airdate_tour.py; these are the room's
    anchors for them."""

    EXPECTED = {
        "essays": ".pool-title-row, #pool-heading",
        "card": "#pool .card",
        "lifecycle": "#pool .card .stamp",
        "calendar": "#board .board-head",
        "filing": "#pool-drop-show",
        "filters": ".pool-filters",
        "editor": "#pool .card .card-link",
        "shelf": "#nav-shelf",
        "rainy": "#nav-rainy-day",
        "settings": "#nav-settings",
    }

    def test_each_stop_is_anchored_in_the_room(self):
        stops = {stop["key"]: stop["anchor"] for stop in run_pure("tour.STOPS")}
        self.assertEqual(stops, self.EXPECTED)

    def test_the_anchors_exist(self):
        html = ROOM_HTML.read_text(encoding="utf-8")
        for needle in ('class="pool-title-row"', 'id="pool-heading"', 'id="board"', 'id="pool-filing"',
                       'class="pool-filters"', 'class="pool-columns" id="pool"',
                       'href="/airdate#shelf" id="nav-shelf"', 'href="/airdate#settings" id="nav-settings"'):
            with self.subTest(needle=needle):
                self.assertIn(needle, html)
        card = run_pure("Cards.cardMarkup({id: 'x', title: 'T', status: 'Writers Room', word_count: 900}, "
                        "{now: new Date(), totems: {}, redPen: {enabled: false, lines: []}, presets: []})")
        self.assertTrue(card.startswith('<article class="card') or 'class="card ' in card, card[:120])
        self.assertIn('class="card-link"', card)
        self.assertIn('class="stamp ', card)

    def test_the_copy_speaks_in_the_four_phases(self):
        stops = run_pure("tour.STOPS")
        words = " ".join(f"{stop['title']} {stop['text']}" for stop in stops)
        self.assertNotIn("Published", words)
        self.assertNotRegex(words, r"\bpublished\b")
        for phase in ("writers room", "writers likey", "ready for air", "live"):
            self.assertIn(phase, words)
        self.assertIn("live essays", next(s["text"] for s in stops if s["key"] == "shelf"))
        for stop in stops:
            with self.subTest(stop=stop["key"]):
                self.assertEqual(stop["text"], stop["text"].lower())
                self.assertEqual(stop["title"], stop["title"].lower())


class HomeTourStyleTests(unittest.TestCase):
    """The old page's .tour-ring and .tour-callout went with airdate.css. The
    room styles both tours; the dim is the ring's shadow."""

    CSS = (ROOT / "static" / "room" / "room.css").read_text(encoding="utf-8")

    def rule(self, selector):
        start = self.CSS.index(f"\n{selector} {{") + 1
        return self.CSS[start:self.CSS.index("}", start)]

    def test_the_ring_dims_the_page(self):
        ring = self.rule(".tour-ring")
        self.assertIn("position: fixed", ring)
        self.assertIn("box-shadow: 0 0 0 4000px rgba(0, 0, 0, 0.70)", ring)
        self.assertIn("pointer-events: none", ring)

    def test_the_callout_is_the_sticky(self):
        callout = self.rule(".tour-callout")
        self.assertIn("position: fixed", callout)
        self.assertIn("var(--pad-canary)", callout)
        self.assertIn("z-index: 61", callout)

    def test_the_home_tours_buttons_are_styled_on_the_sticky(self):
        # The home tour's buttons carry settings-button (pinned in test_room_tour).
        self.assertIn(".tour-callout .tour-actions button {", self.CSS)

    def test_the_editor_css_no_longer_holds_the_base(self):
        editor = (ROOT / "static" / "room" / "editor.css").read_text(encoding="utf-8")
        self.assertNotIn(".tour-ring.ed-tour {", editor)


class HomeTourStartTests(unittest.TestCase):
    """The old page started the tour after the wizard. The room's wizard
    finish loads the page again, so it leaves a one-shot flag the next load
    reads and clears."""

    def boot(self, before="", after=""):
        return run_room(f"""
{before}
globalThis.RoomPool = {{ isLoaded: () => true }};
require(ROOT + '/static/room/settings.js');
await settle();
{after}
await settle();
return {{ tour: record.tour, flag: sessionStorage.getItem('airdate.tour.after-setup'), replaced: record.replaced,
  history: record.history, hash: location.hash,
  options: record.tourOptions ? {{ keys: Object.keys(record.tourOptions), beside: record.tourOptions.place === AirdateTour.placeBeside }} : null }};
""")

    def test_it_runs_once_after_setup(self):
        out = self.boot(before="sessionStorage.setItem('airdate.tour.after-setup', '1');")
        self.assertEqual(out["tour"], ["start"])
        self.assertIsNone(out["flag"], "the flag must be one-shot")
        # The home tour, sitting beside its anchors: no other option, so its
        # ten stops and its key in this browser are the defaults.
        self.assertEqual(out["options"], {"keys": ["place"], "beside": True})

    def test_a_tour_already_seen_is_not_started_again(self):
        out = self.boot(before="sessionStorage.setItem('airdate.tour.after-setup', '1'); AirdateTour.state = 'seen';")
        self.assertEqual(out["tour"], [])
        self.assertIsNone(out["flag"])

    def test_an_ordinary_load_starts_nothing(self):
        self.assertEqual(self.boot()["tour"], [])

    def test_it_waits_for_the_pool(self):
        out = run_room("""
sessionStorage.setItem('airdate.tour.after-setup', '1');
let loaded = false;
globalThis.RoomPool = { isLoaded: () => loaded };
require(ROOT + '/static/room/settings.js');
await settle();
const before = record.tour.slice();
loaded = true;
document.dispatchEvent(new CustomEvent('room:pool-ready', {}));
await settle();
return [before, record.tour];
""")
        self.assertEqual(out, [[], ["start"]])

    def test_the_wizard_finish_flags_it_and_loads_the_essays_view(self):
        out = run_room("""
globalThis.__status = { setup: { wizard: true }, config: {} };
let options = null;
globalThis.AirdateWizard = { open(o) { options = o; } };
globalThis.RoomPool = { isLoaded: () => true };
require(ROOT + '/static/room/settings.js');
await settle();
await options.onFinish();
return { flag: sessionStorage.getItem('airdate.tour.after-setup'), replaced: record.replaced, hash: location.hash };
""")
        self.assertEqual(out["flag"], "1")
        # Same path, no #settings: the reload lands on the essays view.
        self.assertEqual(out["replaced"], ["/airdate"])

    def test_replay_from_settings_goes_to_the_essays_and_replays(self):
        out = self.boot(before="location.hash = '#settings';",
                        after="byId('set-replay-tour').fire('click'); location.hash = ''; fireWin('hashchange');")
        self.assertEqual(out["tour"], ["replay"])
        self.assertEqual(out["options"], {"keys": ["place"], "beside": True})
        self.assertIn(["replace", "/airdate"], out["history"])

    def test_settings_has_the_replay_button_and_the_gear_keeps_the_editor_tour(self):
        html = ROOM_HTML.read_text(encoding="utf-8")
        self.assertIn('<button type="button" class="set-btn" id="set-replay-tour">replay the tour</button>', html)
        self.assertIn('id="gs-replay">replay the editor tour</button>', html)


# ---- the send fields ---------------------------------------------------------------


SEND_FIELDS = {
    "social_title": ("seo_social", 'id="ed-f-social_title"', "social title"),
    "social_description": ("seo_social", 'id="ed-f-social_description"', "social description"),
    "social_image": ("seo_social", 'id="ed-social-file"', "social image"),
    "thumbnail_alt": ("thumbnail", 'id="ed-f-thumbnail_alt"', "thumbnail alt text"),
    "free_unlock_at": ("advanced", 'id="ed-f-free_unlock_at"', "free unlock date"),
}

NOTE = """---
title: "Borrowed Light"
summary: A line about the essay.
status: Writers Likey
tags:
  - craft
---
The first line of the page.
"""


class SendFieldMarkupTests(unittest.TestCase):
    HTML = ROOM_HTML.read_text(encoding="utf-8")

    def section(self, key):
        start = self.HTML.index(f'data-section="{key}"')
        return self.HTML[start:self.HTML.index("</section>", start)]

    def test_each_field_is_drawn_in_its_section_with_a_lowercase_label(self):
        for field, (section, control, label) in SEND_FIELDS.items():
            with self.subTest(field=field):
                body = self.section(section)
                self.assertIn(control, body)
                target = control.split('"')[1]
                self.assertIn(f'<label for="{target}">{label}</label>', body)
                self.assertEqual(label, label.lower())

    def test_the_free_unlock_date_is_a_date(self):
        self.assertIn('id="ed-f-free_unlock_at" data-field="free_unlock_at" type="date"', self.HTML)

    def test_the_social_image_can_be_cleared(self):
        self.assertIn('id="ed-social-clear">clear the social image</button>', self.HTML)

    def test_the_editor_holds_them(self):
        fields = run_pure("E.FIELDS")
        for field in SEND_FIELDS:
            self.assertIn(field, fields)


class SendFieldRulesTests(unittest.TestCase):
    def test_the_placeholders_are_the_fallbacks(self):
        self.assertEqual(run_pure("E.sendFieldPlaceholders({title: 'T', summary: '', subtitle: 'S'})"),
                         {"social_title": "T", "social_description": "S", "thumbnail_alt": "T"})
        self.assertEqual(run_pure("E.sendFieldPlaceholders({title: 'T', summary: 'Sum', subtitle: 'S'})")["social_description"], "Sum")

    def test_the_defaulting_rule_still_fills_them_on_open(self):
        out = run_pure("E.fromEssay({id: 'x', frontmatter: {}, metadata_defaults: {social_title: 'T', social_description: 'D', thumbnail_alt: 'T'}}).values")
        self.assertEqual((out["social_title"], out["social_description"], out["thumbnail_alt"]), ("T", "D", "T"))

    def test_a_date_field_shows_the_day(self):
        self.assertEqual(run_pure("[E.dayValue('2026-10-05T09:00'), E.dayValue('2026-10-05'), E.dayValue('soon'), E.dayValue('')]"),
                         ["2026-10-05", "2026-10-05", "", ""])

    def test_an_upload_points_the_field_at_it_unsaved(self):
        out = run_pure("(() => { const s = E.fromEssay({id: 'x', frontmatter: {}});"
                       " const next = E.adoptSocialImage(s, {relativePath: './drafts/assets/card.png'});"
                       " return [next.values.social_image, E.savePayload(next).updates.social_image, s.values.social_image]; })()")
        self.assertEqual(out, ["./drafts/assets/card.png", "./drafts/assets/card.png", ""])

    def test_clearing_sends_a_blank_so_the_server_removes_the_key(self):
        out = run_pure("(() => { const s = E.fromEssay({id: 'x', frontmatter: {social_image: './drafts/assets/a.png'}});"
                       " s.values.social_image = ''; return E.savePayload(s).updates.social_image; })()")
        self.assertEqual(out, "")

    def test_an_empty_row_goes_to_the_field_when_its_section_shows(self):
        on = run_pure("E.readinessList({blockers: [{key: 'persisted_social_title', field: 'social_title', message: 'x'}]},"
                      " {title: 'T', social_title: ''}, {mode: 'complete', seo_social: true}, 'b').rows[0].target")
        off = run_pure("E.readinessList({blockers: [{key: 'persisted_social_title', field: 'social_title', message: 'x'}]},"
                       " {title: 'T', social_title: ''}, {mode: 'simplified', seo_social: false}, 'b').rows[0].target")
        self.assertEqual((on, off), ("ed-f-social_title", "ed-f-title"))


class SendFieldRoundTripTests(EditorCase):
    """Each field the editor now draws is saved into the note and read back."""

    VALUES = {
        "social_title": "A title for the share",
        "social_description": "A line for the share.",
        "social_image": "./drafts/assets/borrowed-light-card.png",
        "thumbnail_alt": "A lamp on a desk",
        "free_unlock_at": "2026-10-19",
    }

    def test_each_field_round_trips_through_save(self):
        essay_id = self.write_note("Send Fields.md", NOTE)
        for field, value in self.VALUES.items():
            with self.subTest(field=field):
                result = self.server.save_essay_updates(essay_id, {field: value})
                essay_id = result["new_id"]
                self.assertEqual(str(self.frontmatter("Send Fields.md")[field]), value)
                detail = self.server.get_essay_detail(essay_id)
                state = run_pure(f"E.fromEssay({json.dumps(detail, default=str)}).values")
                self.assertEqual(state[field], value)

    def test_the_send_reads_them_from_the_note(self):
        essay_id = self.write_note("Send Read.md", NOTE)
        result = self.server.save_essay_updates(essay_id, dict(self.VALUES))
        payload, *_ = self.server.prepare_essay_publish_payload(result["new_id"], {}, None)
        self.assertEqual(payload["socialTitle"], self.VALUES["social_title"])
        self.assertEqual(payload["socialDescription"], self.VALUES["social_description"])
        self.assertEqual(payload["thumbnailAlt"], self.VALUES["thumbnail_alt"])
        self.assertEqual(payload["freeUnlockAt"], self.VALUES["free_unlock_at"])
        self.assertTrue(payload["socialImage"].endswith("borrowed-light-card.png"))

    def test_a_blank_takes_the_key_off(self):
        essay_id = self.write_note("Send Clear.md", NOTE)
        essay_id = self.server.save_essay_updates(essay_id, {"social_image": "./drafts/assets/x.png"})["new_id"]
        self.server.save_essay_updates(essay_id, {"social_image": ""})
        self.assertNotIn("social_image", self.frontmatter("Send Clear.md"))


if __name__ == "__main__":
    unittest.main()
