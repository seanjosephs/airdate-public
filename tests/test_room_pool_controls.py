"""AD-038, the pool's controls the room had not rebuilt.

- needs attention: a pill that shows the essays missing details a draft needs,
  or only their hero image. It stacks with the phase, totem and topic filters,
  and while it is on each card says what is missing.
- topic: a select beside sort, using the rule the card's topic line and the
  shelf already use.
- sort: closest to air (still the default), last touched, longest, title.

cards.js holds the rules and runs through node. pool.js runs in node against a
small fake DOM, POOL_DOM below: element lookups by id return stand-ins that
record attributes and listeners, and the API is a fake that answers with the
essays a test gives it.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "static" / "room" / "cards.js"
ROOM_HTML = ROOT / "room.html"
NODE = shutil.which("node")

if NODE is None:  # pragma: no cover - depends on the machine
    raise RuntimeError("node is required for tests/test_room_pool_controls.py and was not found on PATH")


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def js(value) -> str:
    return json.dumps(value)


def cards(expression: str):
    script = f"""
const Cards = require({js(str(CARDS))});
const result = (() => {{ return {expression}; }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
"""
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def essay(**fields):
    base = {
        "id": "a1",
        "title": "A Title",
        "status": "Writers Room",
        "arrived_at": "2026-09-20T17:00:00+00:00",
        "last_touched": "2026-09-20T17:00:00+00:00",
        "word_count": 1000,
        "tags": [],
        "category": "",
        "source_role": "standalone",
        "publish_readiness": {"status": "ready", "missing_metadata": [], "missing_images": []},
    }
    base.update(fields)
    return base


NO_HERO = {"status": "image", "ready_except_image": True, "missing_metadata": [], "missing_images": ["hero image"]}
NO_TAGS = {"status": "metadata", "missing_metadata": ["tags"], "missing_images": ["hero image"]}


# ---- what needs attention ---------------------------------------------------


class NeedsAttentionTests(unittest.TestCase):
    def needs(self, **fields):
        return cards(f"Cards.needsAttention({js(essay(**fields))})")

    def test_missing_metadata_needs_attention(self):
        self.assertTrue(self.needs(publish_readiness=NO_TAGS))

    def test_only_the_hero_missing_needs_attention(self):
        self.assertTrue(self.needs(publish_readiness=NO_HERO))

    def test_a_ready_essay_does_not(self):
        self.assertFalse(self.needs())

    def test_a_source_note_never_does(self):
        # A long source is never sent, so its readiness does not count.
        self.assertFalse(self.needs(source_role="source", publish_readiness=NO_TAGS))

    def test_no_readiness_at_all_does_not(self):
        self.assertFalse(cards("Cards.needsAttention({id: 'x'})"))
        self.assertFalse(cards("Cards.needsAttention({id: 'x', publish_readiness: null})"))
        self.assertFalse(cards("Cards.needsAttention(null)"))

    def test_needing_a_folder_is_the_filing_pills_not_this_ones(self):
        self.assertFalse(self.needs(needs_intake=True))


class AttentionLineTests(unittest.TestCase):
    def line(self, **fields):
        return cards(f"Cards.attentionLine({js(essay(**fields))})")

    def test_metadata_first_then_the_hero(self):
        self.assertEqual(self.line(publish_readiness=NO_TAGS), "missing: tags, hero image")

    def test_the_hero_alone(self):
        self.assertEqual(self.line(publish_readiness=NO_HERO), "missing: hero image")

    def test_several_gaps(self):
        readiness = {"status": "metadata", "missing_metadata": ["Subtitle", " tags "], "missing_images": []}
        self.assertEqual(self.line(publish_readiness=readiness), "missing: subtitle, tags")

    def test_nothing_for_an_essay_that_does_not_need_attention(self):
        self.assertEqual(self.line(), "")
        self.assertEqual(self.line(source_role="source", publish_readiness=NO_TAGS), "")


class CardShowsWhyTests(unittest.TestCase):
    CTX = "{now: new Date('2026-09-30T12:00:00-07:00'), totems: {}, redPen: {enabled: false, lines: []}, presets: []"

    def markup(self, fields, attention=True):
        extra = ", attention: true" if attention else ""
        return cards(f"Cards.cardMarkup({js(essay(**fields))}, {self.CTX}{extra}}})")

    def test_the_line_sits_under_the_title_when_the_filter_is_on(self):
        html = self.markup({"publish_readiness": NO_TAGS})
        self.assertIn('<p class="card-attention">missing: tags, hero image</p>', html)
        self.assertGreater(html.index("card-attention"), html.index("</h3>"))
        self.assertLess(html.index("card-attention"), html.index('class="card-actions"'))

    def test_no_line_when_the_filter_is_off(self):
        self.assertNotIn("card-attention", self.markup({"publish_readiness": NO_TAGS}, attention=False))

    def test_no_line_for_an_essay_that_is_ready(self):
        self.assertNotIn("card-attention", self.markup({}))

    def test_the_words_are_escaped(self):
        readiness = {"status": "metadata", "missing_metadata": ["<script>x</script>"], "missing_images": []}
        html = self.markup({"publish_readiness": readiness})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


# ---- the sorts -----------------------------------------------------------------


class PoolSortTests(unittest.TestCase):
    ROWS = [
        essay(id="x", title="Banana", word_count=500, last_touched="2026-09-25T10:00:00+00:00",
              arrived_at="2026-09-01T10:00:00+00:00"),
        essay(id="y", title="apple", word_count=3000, last_touched="2026-09-10T10:00:00+00:00",
              arrived_at="2026-09-03T10:00:00+00:00"),
        essay(id="z", title="Cherry", word_count=1500, last_touched="2026-09-28T10:00:00+00:00",
              arrived_at="2026-09-02T10:00:00+00:00"),
    ]

    def order(self, mode, rows=None):
        rows = self.ROWS if rows is None else rows
        return cards(f"Cards.poolSort({js(rows)}, {js(mode)}).map((row) => row.id)")

    def test_the_four_sorts_in_menu_order(self):
        self.assertEqual(cards("Cards.POOL_SORTS"), [
            {"key": "closest-to-air", "label": "closest to air"},
            {"key": "last-touched", "label": "last touched"},
            {"key": "longest", "label": "longest"},
            {"key": "title", "label": "title"},
        ])

    def test_closest_to_air_is_the_card_sort_and_the_default(self):
        expected = cards(f"Cards.closestToAirSort({js(self.ROWS)}).map((row) => row.id)")
        self.assertEqual(self.order("closest-to-air"), expected)
        self.assertEqual(self.order(None), expected)
        self.assertEqual(self.order("something-else"), expected)

    def test_last_touched_is_newest_first(self):
        self.assertEqual(self.order("last-touched"), ["z", "x", "y"])

    def test_longest_is_most_words_first(self):
        self.assertEqual(self.order("longest"), ["y", "z", "x"])

    def test_title_is_a_to_z_whatever_the_case(self):
        self.assertEqual(self.order("title"), ["y", "x", "z"])

    def test_an_essay_with_no_date_or_count_goes_last(self):
        rows = [
            essay(id="none", title="A", word_count=None, last_touched=""),
            essay(id="some", title="B", word_count=10, last_touched="2026-09-01T00:00:00+00:00"),
        ]
        self.assertEqual(self.order("last-touched", rows), ["some", "none"])
        self.assertEqual(self.order("longest", rows), ["some", "none"])

    def test_ties_fall_back_to_the_title(self):
        rows = [
            essay(id="b", title="Beta", word_count=100, last_touched="2026-09-01T00:00:00+00:00"),
            essay(id="a", title="Alpha", word_count=100, last_touched="2026-09-01T00:00:00+00:00"),
        ]
        self.assertEqual(self.order("longest", rows), ["a", "b"])
        self.assertEqual(self.order("last-touched", rows), ["a", "b"])

    def test_the_list_it_was_given_is_left_alone(self):
        self.assertEqual(
            cards(f"(() => {{ const rows = {js(self.ROWS)}; Cards.poolSort(rows, 'title'); return rows.map((r) => r.id); }})()"),
            ["x", "y", "z"],
        )


# ---- topics ------------------------------------------------------------------------


class DistinctTopicsTests(unittest.TestCase):
    PRESETS = [
        {"name": "Craft", "color": "#3a7bd5", "tags": ["writing", "editing"]},
        {"name": "Culture", "color": "#d9653b", "tags": ["film"]},
    ]

    def topics(self, rows):
        return cards(f"Cards.distinctTopics({js(rows)}, {js(self.PRESETS)})")

    def test_the_topics_in_use_sorted(self):
        rows = [essay(id="a", tags=["film"]), essay(id="b", tags=["writing"]), essay(id="c", tags=["editing"])]
        self.assertEqual(self.topics(rows), ["craft", "culture"])

    def test_a_folder_is_a_topic_when_no_preset_fits(self):
        rows = [essay(id="a", category="Philosophy & Meaning"), essay(id="b", tags=["writing"])]
        self.assertEqual(self.topics(rows), ["craft", "philosophy & meaning"])

    def test_none_when_nothing_has_a_topic(self):
        self.assertEqual(self.topics([essay(id="a"), essay(id="b", tags=["unrelated"])]), [])
        self.assertEqual(self.topics([]), [])

    def test_it_is_the_cards_topic_line_that_decides(self):
        # What the filter offers is what the card says under its cover.
        rows = [essay(id="a", tags=["film", "writing", "editing"], category="Folder")]
        card_topic = cards(f"Cards.topicFor({js(rows[0])}, {js(self.PRESETS)}).name")
        self.assertEqual(self.topics(rows), [card_topic])


# ---- room.html ------------------------------------------------------------------------


class RoomHtmlTests(unittest.TestCase):
    def setUp(self):
        self.html = read("room.html")

    def test_the_attention_pill_is_there_hidden_and_unpressed(self):
        tag = re.search(r'<button[^>]*id="pool-attention"[^>]*>', self.html)
        self.assertIsNotNone(tag, "no #pool-attention button")
        for part in ('class="pool-pill pool-attention"', 'aria-pressed="false"', " hidden"):
            self.assertIn(part, tag.group(0))
        self.assertIn('id="pool-attention-count"', self.html)
        self.assertIn("needs attention", self.html[tag.start():tag.start() + 900])

    def test_the_pill_sits_after_needs_filing(self):
        self.assertLess(self.html.index('id="pool-filing"'), self.html.index('id="pool-attention"'))

    def test_topic_is_a_labelled_select_that_starts_hidden(self):
        self.assertRegex(self.html, r'<label for="pool-topic">topic</label>')
        select = re.search(r'<select id="pool-topic">(.*?)</select>', self.html, re.S)
        self.assertIsNotNone(select)
        self.assertEqual(re.findall(r'<option value="([^"]*)">([^<]*)</option>', select.group(1)), [("", "all topics")])
        box = re.search(r'<div[^>]*id="pool-topic-box"[^>]*>', self.html)
        self.assertIsNotNone(box)
        self.assertIn(" hidden", box.group(0))

    def test_topic_sits_before_sort(self):
        self.assertLess(self.html.index('id="pool-topic"'), self.html.index('id="pool-sort"'))

    def test_the_sort_menu_is_the_sort_functions(self):
        select = re.search(r'<select id="pool-sort">(.*?)</select>', self.html, re.S)
        options = re.findall(r'<option value="([^"]*)">([^<]*)</option>', select.group(1))
        sorts = cards("Cards.POOL_SORTS")
        self.assertEqual(options, [(item["key"], item["label"]) for item in sorts])

    def test_both_new_things_are_styled(self):
        css = read("static/room/cards.css")
        for selector in (".pool-attention", ".card-attention"):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)

    def test_every_edited_script_still_has_a_version(self):
        for name in ("cards.js", "pool.js", "cards.css"):
            with self.subTest(name=name):
                self.assertRegex(self.html, rf"/static/room/{re.escape(name)}\?v=\d+")


# ---- the words ----------------------------------------------------------------------------


class WordsTests(unittest.TestCase):
    def test_the_tour_names_what_the_filters_do(self):
        source = read("static/airdate-tour.js")
        stop = source[source.index("key: 'filters'"):source.index("key: 'editor'")]
        for word in ("topic", "sort", "needs attention"):
            with self.subTest(word=word):
                self.assertIn(word, stop)
        self.assertNotIn("—", stop)

    def test_setup_says_what_the_essays_view_filters_and_sorts_by(self):
        doc = read("SETUP.md")
        block = doc[doc.index("## The lifecycle"):doc.index("## Thumbnails")]
        for phrase in ("by topic", "needs attention", "needs filing", "last touched", "longest", "closest to air"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, block)
        self.assertNotIn("filters by phase and by totem", block)


# ---- pool.js, against a fake DOM ------------------------------------------------------------

POOL_DOM = r"""
const ROOT = __ROOT__;
const els = new Map();
const docListeners = [];
function makeEl(id) {
  const attrs = {};
  const listeners = [];
  const el = {
    id, hidden: false, textContent: '', innerHTML: '', value: '', className: '', dataset: {}, style: {},
    clientWidth: 400, isConnected: true, disabled: false,
    setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
    removeAttribute(k) { delete attrs[k]; },
    addEventListener(type, fn) { listeners.push({ type, fn }); },
    focus() {}, closest() { return null; }, contains() { return false; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    fire(type, extra) {
      for (const l of listeners.slice()) if (l.type === type) l.fn({ type, target: el, preventDefault() {}, ...(extra || {}) });
    },
    attrs,
  };
  return el;
}
function byId(id) { if (!els.has(id)) els.set(id, makeEl(id)); return els.get(id); }
const phaseButtons = ['all', 'writers-room', 'writers-likey', 'ready-for-air'].map((phase) => {
  const button = makeEl(`phase-${phase}`);
  button.dataset.phase = phase;
  return button;
});
globalThis.window = globalThis;
globalThis.document = {
  readyState: 'complete', activeElement: null, body: makeEl('body'),
  getElementById: byId,
  querySelector() { return null; },
  querySelectorAll(sel) { return sel === '[data-phase]' ? phaseButtons : []; },
  addEventListener(type, fn) { docListeners.push({ type, fn }); },
  dispatchEvent(event) { for (const l of docListeners.slice()) if (l.type === event.type) l.fn(event); return true; },
};
globalThis.addEventListener = () => {};
globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } };
const record = { posts: [], afterLeaving: [] };
globalThis.RoomKeys = {
  createRoving() { return { refresh() {}, onKey() {} }; },
  afterLeaving(order, id) { record.afterLeaving.push({ order, id }); return null; },
  focusAfterLeaving() {},
};
globalThis.RoomFeedback = { slip() { return {}; }, plaque() {}, clear() {} };
globalThis.__essays = [];
globalThis.__config = {};
globalThis.RoomApi = {
  async getJson(url) {
    if (url === '/api/app/status') return { config: globalThis.__config };
    if (url === '/api/essays') return { essays: globalThis.__essays };
    return {};
  },
  async postJson(url, body) {
    record.posts.push({ url, body });
    return globalThis.__post ? globalThis.__post(url, body) : {};
  },
};
const settle = () => new Promise((resolve) => setTimeout(resolve, 10));
function essay(id, extra) {
  return {
    id, title: id, status: 'Writers Room', arrived_at: '2026-09-20T17:00:00+00:00',
    last_touched: '2026-09-20T17:00:00+00:00', word_count: 1000, tags: [], category: '', needs_intake: false,
    source_role: 'standalone', totem_raw: '', excerpt: '', search_blob: id,
    publish_readiness: { status: 'ready', missing_metadata: [], missing_images: [] }, ...(extra || {}),
  };
}
const NO_HERO = { status: 'image', ready_except_image: true, missing_metadata: [], missing_images: ['hero image'] };
const NO_TAGS = { status: 'metadata', missing_metadata: ['tags'], missing_images: ['hero image'] };
const PRESETS = [
  { name: 'Craft', color: '#3a7bd5', tags: ['writing'] },
  { name: 'Culture', color: '#d9653b', tags: ['film'] },
];
const ids = () => [...byId('pool').innerHTML.matchAll(/data-essay-id="([^"]*)"/g)].map((m) => m[1]);
const reasons = () => [...byId('pool').innerHTML.matchAll(/class="card-attention">([^<]*)</g)].map((m) => m[1]);
const pressed = (id) => byId(id).getAttribute('aria-pressed');
const resetClick = () => byId('pool').fire('click', {
  target: { closest: (sel) => (sel === '[data-action="reset"]' ? {} : null) },
});
const boot = async () => {
  require(ROOT + '/static/room/cards.js');
  require(ROOT + '/static/room/pool.js');
  await settle();
};
const send = (oldId, row) => document.dispatchEvent(new CustomEvent('room:essay', { detail: { oldId, essay: row, source: 'board' } }));
"""


def run_pool(body: str):
    script = POOL_DOM.replace("__ROOT__", js(str(ROOT))) + f"""
(async () => {{
  const result = await (async () => {{ {body} }})();
  process.stdout.write(JSON.stringify(result === undefined ? null : result));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run([NODE, "-e", script], cwd=ROOT, capture_output=True, text=True)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


class NeedsAttentionPillTests(unittest.TestCase):
    FOUR = """
globalThis.__essays = [
  essay('a', { publish_readiness: NO_HERO }),
  essay('b', { publish_readiness: NO_TAGS }),
  essay('c'),
  essay('d', { source_role: 'source', publish_readiness: NO_TAGS }),
];
await boot();
"""

    def test_it_counts_what_needs_attention(self):
        out = run_pool(self.FOUR + """
const pill = byId('pool-attention');
return { hidden: pill.hidden, count: byId('pool-attention-count').textContent,
  label: pill.getAttribute('aria-label'), pressed: pressed('pool-attention') };
""")
        self.assertEqual(out, {"hidden": False, "count": "2", "label": "needs attention, 2", "pressed": "false"})

    def test_pressing_it_shows_only_those_and_says_why(self):
        out = run_pool(self.FOUR + """
byId('pool-attention').fire('click');
return { ids: ids(), reasons: reasons(), pressed: pressed('pool-attention'),
  shown: byId('pool-count').textContent, said: byId('pool-results').textContent };
""")
        self.assertEqual(out["ids"], ["a", "b"])
        self.assertEqual(out["reasons"], ["missing: hero image", "missing: tags, hero image"])
        self.assertEqual(out["pressed"], "true")
        self.assertEqual(out["shown"], "2")
        self.assertEqual(out["said"], "2 essays")

    def test_pressing_it_again_shows_everything_and_no_reasons(self):
        out = run_pool(self.FOUR + """
byId('pool-attention').fire('click');
byId('pool-attention').fire('click');
return { ids: ids(), reasons: reasons(), pressed: pressed('pool-attention') };
""")
        self.assertEqual(sorted(out["ids"]), ["a", "b", "c", "d"])
        self.assertEqual(out["reasons"], [])
        self.assertEqual(out["pressed"], "false")

    def test_it_is_hidden_when_nothing_needs_attention(self):
        out = run_pool("""
globalThis.__essays = [essay('a'), essay('b')];
await boot();
return byId('pool-attention').hidden;
""")
        self.assertTrue(out)

    def test_it_stays_while_it_is_on_so_it_can_be_turned_off(self):
        out = run_pool("""
globalThis.__essays = [essay('a', { publish_readiness: NO_HERO }), essay('b')];
await boot();
byId('pool-attention').fire('click');
send('a', essay('a'));
await settle();
return { hidden: byId('pool-attention').hidden, count: byId('pool-attention-count').textContent,
  pressed: pressed('pool-attention'), ids: ids(), body: byId('pool').innerHTML.includes('nothing matches') };
""")
        self.assertEqual(out["hidden"], False)
        self.assertEqual(out["count"], "0")
        self.assertEqual(out["pressed"], "true")
        self.assertEqual(out["ids"], [])
        self.assertTrue(out["body"])


class TopicSelectTests(unittest.TestCase):
    ESSAYS = """
globalThis.__config = { tag_presets: PRESETS };
globalThis.__essays = [
  essay('a', { tags: ['writing'] }),
  essay('b', { tags: ['film'] }),
  essay('c', { category: 'Philosophy & Meaning' }),
  essay('d'),
];
await boot();
"""

    def test_it_lists_the_topics_in_use(self):
        out = run_pool(self.ESSAYS + """
return { values: [...byId('pool-topic').innerHTML.matchAll(/value="([^"]*)"/g)].map((m) => m[1]),
  hidden: byId('pool-topic-box').hidden };
""")
        self.assertEqual(out["values"], ["", "craft", "culture", "philosophy &amp; meaning"])
        self.assertFalse(out["hidden"])

    def test_choosing_one_shows_only_its_essays(self):
        out = run_pool(self.ESSAYS + """
byId('pool-topic').value = 'craft';
byId('pool-topic').fire('change');
const craft = ids();
byId('pool-topic').value = 'philosophy & meaning';
byId('pool-topic').fire('change');
return { craft, folder: ids(), said: byId('pool-results').textContent };
""")
        self.assertEqual(out["craft"], ["a"])
        self.assertEqual(out["folder"], ["c"])
        self.assertEqual(out["said"], "1 essay")

    def test_all_topics_shows_everything_again(self):
        out = run_pool(self.ESSAYS + """
byId('pool-topic').value = 'craft';
byId('pool-topic').fire('change');
byId('pool-topic').value = '';
byId('pool-topic').fire('change');
return ids().sort();
""")
        self.assertEqual(out, ["a", "b", "c", "d"])

    def test_the_box_is_hidden_when_no_essay_has_a_topic(self):
        out = run_pool("""
globalThis.__essays = [essay('a'), essay('b')];
await boot();
return byId('pool-topic-box').hidden;
""")
        self.assertTrue(out)

    def test_a_topic_nobody_has_any_more_is_dropped(self):
        out = run_pool(self.ESSAYS + """
byId('pool-topic').value = 'craft';
byId('pool-topic').fire('change');
send('a', essay('a', { tags: [] }));
await settle();
return { ids: ids().sort(), value: byId('pool-topic').value };
""")
        self.assertEqual(out["ids"], ["a", "b", "c", "d"])
        self.assertEqual(out["value"], "")


class SortSelectTests(unittest.TestCase):
    THREE = """
globalThis.__essays = [
  essay('x', { title: 'Banana', word_count: 500, last_touched: '2026-09-25T10:00:00+00:00', arrived_at: '2026-09-01T10:00:00+00:00' }),
  essay('y', { title: 'apple', word_count: 3000, last_touched: '2026-09-10T10:00:00+00:00', arrived_at: '2026-09-03T10:00:00+00:00' }),
  essay('z', { title: 'Cherry', word_count: 1500, last_touched: '2026-09-28T10:00:00+00:00', arrived_at: '2026-09-02T10:00:00+00:00' }),
];
await boot();
const by = (mode) => { byId('pool-sort').value = mode; byId('pool-sort').fire('change'); return ids(); };
"""

    def test_each_sort_orders_the_pool(self):
        out = run_pool(self.THREE + """
return { start: ids(), touched: by('last-touched'), longest: by('longest'), title: by('title'), back: by('closest-to-air') };
""")
        self.assertEqual(out["start"], ["x", "z", "y"])
        self.assertEqual(out["touched"], ["z", "x", "y"])
        self.assertEqual(out["longest"], ["y", "z", "x"])
        self.assertEqual(out["title"], ["y", "x", "z"])
        self.assertEqual(out["back"], ["x", "z", "y"])

    def test_a_sort_is_announced(self):
        out = run_pool(self.THREE + """
by('longest');
return byId('pool-results').textContent;
""")
        self.assertEqual(out, "3 essays, sorted by longest")

    def test_a_value_that_is_not_a_sort_is_the_default(self):
        out = run_pool(self.THREE + "return by('nonsense');")
        self.assertEqual(out, ["x", "z", "y"])

    def test_parking_hands_focus_on_in_the_order_on_screen(self):
        out = run_pool(self.THREE + """
by('title');
globalThis.__post = () => ({ row: { id: 'x', title: 'Banana', status: 'Archived' }, new_id: 'x' });
byId('pool').fire('click', { target: { closest: (sel) => (sel === '[data-action="park"]'
  ? { closest: () => ({ dataset: { essayId: 'x' } }) } : null) } });
await settle();
return record.afterLeaving[0];
""")
        self.assertEqual(out, {"order": ["y", "x", "z"], "id": "x"})


class FiltersStackTests(unittest.TestCase):
    SET = """
globalThis.__config = { tag_presets: PRESETS };
globalThis.__essays = [
  essay('a', { tags: ['writing'], publish_readiness: NO_HERO }),
  essay('b', { tags: ['writing'] }),
  essay('c', { tags: ['film'], publish_readiness: NO_HERO }),
  essay('d', { tags: ['writing'], status: 'Writers Likey', publish_readiness: NO_HERO }),
];
await boot();
"""

    def test_attention_and_topic_and_phase_narrow_together(self):
        out = run_pool(self.SET + """
byId('pool-attention').fire('click');
const attention = ids().sort();
byId('pool-topic').value = 'craft';
byId('pool-topic').fire('change');
const withTopic = ids().sort();
phaseButtons[2].fire('click');
return { attention, withTopic, withPhase: ids() };
""")
        self.assertEqual(out["attention"], ["a", "c", "d"])
        self.assertEqual(out["withTopic"], ["a", "d"])
        self.assertEqual(out["withPhase"], ["d"])

    def test_show_every_essay_clears_the_filters_and_keeps_the_sort(self):
        out = run_pool(self.SET + """
byId('pool-attention').fire('click');
byId('pool-topic').value = 'craft';
byId('pool-topic').fire('change');
byId('pool-sort').value = 'title';
byId('pool-sort').fire('change');
byId('pool-topic').value = 'culture';
byId('pool-topic').fire('change');
resetClick();
return { ids: ids(), attention: pressed('pool-attention'), topic: byId('pool-topic').value, sort: byId('pool-sort').value };
""")
        self.assertEqual(out["ids"], ["a", "b", "c", "d"])
        self.assertEqual(out["attention"], "false")
        self.assertEqual(out["topic"], "")
        self.assertEqual(out["sort"], "title")


if __name__ == "__main__":
    unittest.main()
