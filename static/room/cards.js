// The script card: pure functions from an index row to what the card shows.
//
// Nothing in here touches the DOM or the network, so node can test all of it
// (tests/test_room_cards_js.py). pool.js renders what cardMarkup returns and
// owns the round trips.
(function installAirdateCards(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AirdateCards = api;
})(typeof globalThis === 'object' ? globalThis : this, function createAirdateCards() {
  const DAY_MS = 86400000;
  const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
  const WEEKDAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

  // Five ages of paper, counted in local calendar days from arrival. Each has
  // its own writing tool lying on the page.
  const AGE_TIERS = [
    { key: 'under-two-weeks', below: 14, tool: 'fountain', toolName: 'silver fountain pen', label: 'under two weeks' },
    { key: 'under-a-month', below: 30, tool: 'rollerball', toolName: 'black rollerball', label: 'under a month' },
    { key: 'under-three-months', below: 91, tool: 'ballpoint', toolName: 'cheap ballpoint', label: 'under three months' },
    { key: 'under-six-months', below: 182, tool: 'pencil', toolName: 'pencil', label: 'under six months' },
    { key: 'past-six-months', below: Infinity, tool: 'crayon', toolName: 'chewed crayon', label: 'past six months' },
  ];
  const OLDEST = 'past-six-months';
  // The red pen writes on pages from three months old.
  const JAB_TIERS = new Set(['under-six-months', OLDEST]);
  // A line about the coffee ring or the crayon only makes sense on the paper
  // that has them, so it is kept for the oldest. Matched on the words, so a
  // writer's own line about either gets the same treatment.
  const OLDEST_ONLY = /coffee ring|crayon/i;

  const PHASES = {
    'Writers Room': 'room',
    'Writers Likey': 'likey',
    'Ready for Air': 'ready',
    Live: 'live',
    Archived: 'archived',
  };

  // Cover height and title size by phase. Likey and complete is the big one,
  // with the orange halo.
  const SIZES = {
    room: { key: 'room', cover: 290, title: 16, halo: false },
    likey: { key: 'likey', cover: 340, title: 18, halo: false },
    'likey-complete': { key: 'likey-complete', cover: 390, title: 21, halo: true },
    ready: { key: 'ready', cover: 340, title: 18, halo: false },
    live: { key: 'live', cover: 340, title: 18, halo: false },
  };

  const PADS = new Set(['sticky', 'paper', 'index']);
  const PAD_COLORS = new Set(['canary', 'blue', 'orange', 'pink', 'green']);

  const STAR_PATH = 'M20 4.5 L24.6 15.2 L36.5 16 L27.4 23.8 L30.6 35.5 L20 29 L9.8 35.8 L12.8 23.6 L3.5 16.4 L15.3 15.3 Z';
  // The gem for "open in obsidian", at the card's 26px.
  const OBSIDIAN_GEM = '<img class="obsidian-logo" src="/static/brand/obsidian-logo.png" alt="" width="22" height="22">';
  const AIR_ICON = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="2"/><path d="M7.5 7.5a6.5 6.5 0 0 0 0 9M16.5 7.5a6.5 6.5 0 0 1 0 9M4.5 4.5a10.5 10.5 0 0 0 0 15M19.5 4.5a10.5 10.5 0 0 1 0 15"/></svg>';
  const LIVE_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/></svg>';
  // The six-dot grip on the grabber.
  const GRIP_ICON = '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false"><circle cx="9" cy="6" r="1.9"/><circle cx="15" cy="6" r="1.9"/><circle cx="9" cy="12" r="1.9"/><circle cx="15" cy="12" r="1.9"/><circle cx="9" cy="18" r="1.9"/><circle cx="15" cy="18" r="1.9"/></svg>';
  // Back to the room: a parked card's umbrella becomes this.
  const BACK_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/></svg>';

  // How many stacked parts each writing tool is drawn from (cards.css styles
  // them by position).
  const TOOL_PARTS = { fountain: 6, rollerball: 9, ballpoint: 4, pencil: 4, crayon: 2 };

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function toDate(value) {
    if (value instanceof Date) return new Date(value.getTime());
    if (value === undefined || value === null || value === '') return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  // A local calendar day as a whole number, so day counts are calendar days
  // in the writer's own time zone and never drift across UTC midnight.
  function localDayNumber(date) {
    return Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / DAY_MS);
  }

  function ageDays(arrivedAt, now) {
    const arrived = toDate(arrivedAt);
    const today = toDate(now) || new Date();
    if (!arrived) return 0;
    return Math.max(0, localDayNumber(today) - localDayNumber(arrived));
  }

  function ageTier(arrivedAt, now) {
    const days = ageDays(arrivedAt, now);
    return AGE_TIERS.find((tier) => days < tier.below).key;
  }

  function tierInfo(tierKey) {
    return AGE_TIERS.find((tier) => tier.key === tierKey) || AGE_TIERS[0];
  }

  function toolFor(tierKey) {
    const tier = tierInfo(tierKey);
    return {
      key: tier.tool,
      name: tier.toolName,
      parts: TOOL_PARTS[tier.tool],
      label: `${tier.toolName}, ${tier.label} on the desk`,
    };
  }

  // Length as five signal bars, with no number beside them on a card:
  // under 500 words one, to 1,199 two, to 2,499 three, to 4,999 four, 5,000
  // and up five. The exact count is the bars' tooltip.
  const BAR_STEPS = [500, 1200, 2500, 5000];
  const BAR_NAMES = ['a note', 'short', 'essay length', 'long', 'very long'];
  function barCount(words) {
    const n = Number(words) || 0;
    return 1 + BAR_STEPS.filter((step) => n >= step).length;
  }

  function formatWords(words) {
    const n = Math.max(0, Math.round(Number(words) || 0));
    if (n < 1000) return String(n);
    return `${(n / 1000).toFixed(1).replace(/\.0$/, '')}k`;
  }

  function phaseOf(essay) {
    return PHASES[essay?.status] || 'room';
  }

  // A parked essay (rainy day) keeps the stamp, star and size of the phase it
  // had just before parking - "stamp, star and paper age stay" (§15.2). Only
  // room and likey ever reach Archived (the server refuses parking a
  // scheduled or live essay), so this always resolves to one of those two.
  const RESTORABLE_STATUSES = new Set(['Writers Room', 'Writers Likey']);
  function displayStatusOf(essay) {
    if (essay?.status !== 'Archived') return essay?.status;
    const previous = String(essay?.previous_status || '').trim();
    return RESTORABLE_STATUSES.has(previous) ? previous : 'Writers Room';
  }

  // The essay a card actually draws itself as: the real essay, or - when
  // parked - a shadow copy wearing its previous phase, so every phase-driven
  // function (sizeTier, stampFor, the post-it) reads it without knowing
  // rainy day exists.
  function displayEssay(essay) {
    return essay?.status === 'Archived' ? { ...essay, status: displayStatusOf(essay) } : essay;
  }

  function isComplete(essay) {
    return essay?.publish_readiness?.status === 'ready';
  }

  function sizeTier(essay) {
    const phase = phaseOf(essay);
    if (phase === 'likey') return isComplete(essay) ? SIZES['likey-complete'] : SIZES.likey;
    return SIZES[phase] || SIZES.room;
  }

  // A calendar date the writer typed or the board wrote ("2026-10-05", or the
  // first ten characters of a longer value) is read as that day, with no time
  // zone arithmetic that could move it. An instant (arrived, starred) is shown
  // on the local calendar.
  function calendarParts(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || '').trim());
    if (!match) return null;
    const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
    if (month < 1 || month > 12 || day < 1 || day > 31) return null;
    return { year, month, day, weekday: new Date(year, month - 1, day).getDay() };
  }

  function instantParts(value) {
    const date = toDate(value);
    if (!date) return null;
    return { year: date.getFullYear(), month: date.getMonth() + 1, day: date.getDate(), weekday: date.getDay() };
  }

  function formatStampDate(value, kind) {
    const parts = kind === 'calendar' ? calendarParts(value) : instantParts(value);
    return parts ? `${MONTHS[parts.month - 1]} ${parts.day} ${parts.year}` : '';
  }

  // "10.5.26": the brief form the card's air chip uses.
  function formatAirShort(value) {
    const parts = calendarParts(value);
    return parts ? `${parts.month}.${parts.day}.${String(parts.year).slice(-2)}` : '';
  }

  function formatAirDay(value) {
    const parts = calendarParts(value);
    return parts ? `${WEEKDAYS[parts.weekday]} ${MONTHS[parts.month - 1]} ${parts.day}` : '';
  }

  const STAMP_NAMES = { room: 'writers room', likey: 'writers likey', ready: 'ready for air', live: 'live' };

  // The stamp shows the latest phase and the date it was reached: arrival for
  // the room, the star for likey, the air date for ready, the post date for
  // live (falling back to the air date, which is what going live stamps).
  function stampFor(essay) {
    const phase = phaseOf(essay);
    let kind = phase;
    let date = '';
    if (phase === 'likey') {
      date = formatStampDate(essay.starred_at, 'instant');
    } else if (phase === 'ready') {
      date = formatStampDate(essay.scheduled_at, 'calendar');
    } else if (phase === 'live') {
      date = formatStampDate(essay.published_date, 'calendar') || formatStampDate(essay.scheduled_at, 'calendar');
    } else {
      kind = 'room';
      date = formatStampDate(essay?.arrived_at, 'instant');
    }
    const name = STAMP_NAMES[kind];
    return { kind, date, name, label: date ? `${name}, ${date}` : name };
  }

  // FNV-1a, 32 bit. Stable across runs and machines, which is all it is for.
  function hashString(value) {
    let hash = 0x811c9dc5;
    const text = String(value || '');
    for (let i = 0; i < text.length; i += 1) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return hash >>> 0;
  }

  // One jab per essay, picked by its id so it never changes from visit to
  // visit. The coffee-ring and crayon lines only land on the oldest paper;
  // on younger paper an essay whose pick is one of them gets another line,
  // again by its id.
  function jabFor(essayId, tierKey, lines) {
    if (!JAB_TIERS.has(tierKey)) return '';
    const all = (Array.isArray(lines) ? lines : []).map((line) => String(line || '').trim()).filter(Boolean);
    if (!all.length) return '';
    const hash = hashString(essayId);
    const pick = all[hash % all.length];
    if (tierKey === OLDEST || !OLDEST_ONLY.test(pick)) return pick;
    const general = all.filter((line) => !OLDEST_ONLY.test(line));
    return general.length ? general[hash % general.length] : '';
  }

  // What is written under the title: a red-pen jab from three months, else
  // the essay's first line, else nothing.
  function pageLine(essay, tierKey, redPen) {
    const pen = redPen || {};
    if (pen.enabled !== false) {
      const jab = jabFor(essay?.id, tierKey, pen.lines);
      if (jab) return { kind: 'jab', text: jab };
    }
    const excerpt = String(essay?.excerpt || '').trim();
    return excerpt ? { kind: 'excerpt', text: excerpt } : { kind: 'none', text: '' };
  }

  function arrivalTime(essay) {
    const date = toDate(essay?.arrived_at);
    return date ? date.getTime() : Number.POSITIVE_INFINITY;
  }

  function sortGroup(essay) {
    const phase = phaseOf(essay);
    if (phase === 'ready') return 0;
    if (phase === 'likey') return isComplete(essay) ? 1 : 2;
    if (phase === 'room') return 3;
    return 4;
  }

  function byTitle(a, b) {
    return String(a?.title || '').localeCompare(String(b?.title || ''));
  }

  // A date field as milliseconds, 0 when the essay has none.
  function timeOf(value) {
    const date = toDate(value);
    return date ? date.getTime() : 0;
  }

  // "closest to air": what is on the board by air date, then likey and
  // complete, then likey, then the room. Oldest paper first inside each.
  function closestToAirSort(essays) {
    return [...(Array.isArray(essays) ? essays : [])].sort((a, b) => {
      const group = sortGroup(a) - sortGroup(b);
      if (group) return group;
      if (sortGroup(a) === 0) {
        const airA = String(a.scheduled_at || '').slice(0, 10) || '9999-99-99';
        const airB = String(b.scheduled_at || '').slice(0, 10) || '9999-99-99';
        if (airA !== airB) return airA < airB ? -1 : 1;
      }
      const age = arrivalTime(a) - arrivalTime(b);
      if (age) return age;
      return byTitle(a, b);
    });
  }

  function archivedTime(essay) {
    return timeOf(essay?.archived_at);
  }

  // Rainy day's three sorts. Default is "longest in the rain": oldest parked
  // first, because the point of the screen is to see what has been waiting.
  function rainyDaySort(essays, mode) {
    const list = [...(Array.isArray(essays) ? essays : [])];
    if (mode === 'newest-parked') {
      return list.sort((a, b) => archivedTime(b) - archivedTime(a));
    }
    if (mode === 'title') {
      return list.sort(byTitle);
    }
    return list.sort((a, b) => archivedTime(a) - archivedTime(b));
  }

  // The pool's sorts, in the order the menu shows them. The first is the
  // default and is closestToAirSort; the rest are flat lists by one thing. An
  // essay with no date or word count has nothing to rank by, so it goes last,
  // and ties fall back to the title so the order never shuffles between loads.
  // Each `order` sorts the list it is given, a copy poolSort made.
  const POOL_SORTS = [
    { key: 'closest-to-air', label: 'closest to air', order: closestToAirSort },
    {
      key: 'last-touched',
      label: 'last touched',
      order: (list) => list.sort((a, b) => timeOf(b?.last_touched) - timeOf(a?.last_touched) || byTitle(a, b)),
    },
    {
      key: 'longest',
      label: 'longest',
      order: (list) => list.sort((a, b) => (Number(b?.word_count) || 0) - (Number(a?.word_count) || 0) || byTitle(a, b)),
    },
    { key: 'title', label: 'title', order: (list) => list.sort(byTitle) },
  ];

  function poolSort(essays, mode) {
    const sort = POOL_SORTS.find((item) => item.key === mode) || POOL_SORTS[0];
    return sort.order([...(Array.isArray(essays) ? essays : [])]);
  }

  function readinessOf(essay) {
    const readiness = essay?.publish_readiness;
    return readiness && typeof readiness === 'object' ? readiness : {};
  }

  // An essay that cannot go yet: details missing, or only its hero image. A
  // long source is never sent, so its readiness does not count; needing a
  // folder is the filing pill's business, not this one's.
  function needsAttention(essay) {
    if (!essay || essay.source_role === 'source') return false;
    const status = readinessOf(essay).status;
    return status === 'metadata' || status === 'image';
  }

  function missingWords(list) {
    return (Array.isArray(list) ? list : []).map((word) => String(word).trim().toLowerCase()).filter(Boolean);
  }

  // What is missing, in the room's words: the details first, then the hero.
  function attentionLine(essay) {
    if (!needsAttention(essay)) return '';
    const readiness = readinessOf(essay);
    const missing = [...missingWords(readiness.missing_metadata), ...missingWords(readiness.missing_images)];
    return missing.length ? `missing: ${missing.join(', ')}` : '';
  }

  function estimatedHeight(essay) {
    // Cover plus a footer of about two title lines. Only used to balance the
    // columns, so close is good enough.
    return sizeTier(essay).cover + 150;
  }

  // Pack cards into flex columns: each goes into the shortest column so far,
  // the leftmost on a tie, which keeps the sort order reading across.
  function distribute(items, columnCount, heightOf) {
    const count = Math.max(1, Math.floor(Number(columnCount) || 1));
    const measure = typeof heightOf === 'function' ? heightOf : estimatedHeight;
    const columns = Array.from({ length: count }, () => []);
    const heights = new Array(count).fill(0);
    for (const item of Array.isArray(items) ? items : []) {
      let target = 0;
      for (let i = 1; i < count; i += 1) {
        if (heights[i] < heights[target]) target = i;
      }
      columns[target].push(item);
      heights[target] += measure(item) + 24;
    }
    return columns;
  }

  // The topic line under the cover: the tag preset the essay's tags match
  // best (with its colour), else its folder, else nothing.
  function topicFor(essay, presets) {
    const tags = (Array.isArray(essay?.tags) ? essay.tags : [])
      .map((tag) => String(tag).toLowerCase().trim()).filter(Boolean);
    let best = null;
    let bestHits = 0;
    for (const preset of Array.isArray(presets) ? presets : []) {
      if (!preset || !preset.name) continue;
      const list = (Array.isArray(preset.tags) ? preset.tags : []).map((tag) => String(tag).toLowerCase());
      const hits = tags.filter((tag) => list.includes(tag)).length;
      if (hits > bestHits) {
        bestHits = hits;
        best = { name: String(preset.name).toLowerCase(), color: String(preset.color || '') };
      }
    }
    if (best) return best;
    const category = String(essay?.category || '').trim();
    return category ? { name: category.toLowerCase(), color: '' } : null;
  }

  // The topics the essays carry, named as the card line names them, sorted.
  // The pool's topic menu and the shelf's both list these.
  function distinctTopics(essays, presets) {
    const names = new Set();
    for (const essay of Array.isArray(essays) ? essays : []) {
      const topic = topicFor(essay, presets);
      if (topic && topic.name) names.add(topic.name);
    }
    return Array.from(names).sort();
  }

  function padFor(essay) {
    const pad = String(essay?.note_pad || '').trim().toLowerCase();
    const color = String(essay?.note_color || '').trim().toLowerCase();
    return {
      pad: PADS.has(pad) ? pad : 'sticky',
      color: PAD_COLORS.has(color) ? color : 'canary',
    };
  }

  function totemFor(essay, totems) {
    const raw = String(essay?.totem_raw || '').trim().toLowerCase();
    if (!raw || !totems) return null;
    const item = totems[raw];
    return item && item.image ? { key: raw, label: String(item.label || raw).toLowerCase(), image: String(item.image) } : null;
  }

  // The room lives at /airdate. Its four views are the hash: none is the
  // essays, and the sidebar's links name the other three.
  const ROOM_PATH = '/airdate';
  const ROUTE_HASHES = { '#settings': 'settings', '#shelf': 'shelf', '#rainy-day': 'rainy-day' };

  function routeOfHash(hash) {
    return ROUTE_HASHES[String(hash || '')] || 'essays';
  }

  // The view a room link goes to: "/airdate#shelf" is the shelf, "/airdate"
  // the essays. Only the hash decides, so the path a link spells is free to
  // change without moving aria-current off the right sidebar item.
  function routeOfHref(href) {
    const text = String(href || '');
    const at = text.indexOf('#');
    return routeOfHash(at >= 0 ? text.slice(at) : '');
  }

  // The room's own editor (slice 4). A real link, so it opens with the
  // keyboard and in a new tab; editor-view.js opens it in place on a plain
  // click and reads ?essay= when the room loads.
  function editorHref(essay) {
    return `${ROOM_PATH}?essay=${encodeURIComponent(String(essay?.id || ''))}`;
  }

  function isPostLink(value) {
    return /^https:\/\//i.test(String(value || '').trim());
  }

  // ---- markup -------------------------------------------------------------

  function stampMarkup(stamp) {
    const label = escapeHtml(stamp.label);
    const date = escapeHtml(stamp.date);
    if (stamp.kind === 'live') {
      const parts = stamp.date.split(' ');
      const top = escapeHtml(parts.length === 3 ? `${parts[0]} ${parts[1]}`.toUpperCase() : '');
      const year = escapeHtml(parts.length === 3 ? parts[2] : '');
      return `<div class="stamp stamp-live"><svg width="96" height="96" viewBox="0 0 96 96" fill="none" role="img" aria-label="${label}">`
        + '<circle cx="48" cy="48" r="44" stroke="currentColor" stroke-width="5" stroke-dasharray="2.5 3.2"/>'
        + '<circle cx="48" cy="48" r="39" stroke="currentColor" stroke-width="1.5"/>'
        + `<text x="48" y="31" font-size="11" letter-spacing="1.5" fill="currentColor" text-anchor="middle">${top}</text>`
        + '<path d="M10.5 37h75M10.5 61h75" stroke="currentColor" stroke-width="2"/>'
        + '<text x="49.5" y="56.5" font-size="21" letter-spacing="3" fill="currentColor" text-anchor="middle">LIVE</text>'
        + `<text x="48" y="77" font-size="11" letter-spacing="2" fill="currentColor" text-anchor="middle">${year}</text>`
        + '</svg></div>';
    }
    const dateSpan = (cls) => (stamp.date ? `<span class="${cls}">${date}</span>` : '');
    if (stamp.kind === 'likey') {
      return `<div class="stamp stamp-likey" role="img" aria-label="${label}">`
        + '<span class="stamp-rule"></span><span class="stamp-likey-name">writers likey</span>'
        + `${dateSpan('stamp-likey-date')}<span class="stamp-rule"></span></div>`;
    }
    if (stamp.kind === 'ready') {
      return `<div class="stamp stamp-ready" role="img" aria-label="${label}">`
        + '<span class="stamp-ready-name">ready for air</span>'
        + `${dateSpan('stamp-ready-date')}<span class="stamp-ready-mark">airdate</span></div>`;
    }
    return `<div class="stamp stamp-room" role="img" aria-label="${label}">`
      + `<span class="stamp-room-box">writers room</span>${dateSpan('stamp-room-date')}</div>`;
  }

  // The needs-filing lane (slice 6): a one-click route for an essay with no
  // category, through the existing intake-suggest/intake-apply routes.
  // `intake` is the last suggestion pool.js has for this essay, or undefined
  // before the writer has asked for one.
  // The first "file it" is in the card's meta row; once a topic is suggested
  // the picker and the confirm button need the width, so they sit under the
  // title.
  function filingMarkup(essay, intake) {
    if (!essay?.needs_intake) return '';
    if (!intake) return '';
    const categories = Array.isArray(intake.categories) ? intake.categories : [];
    const categoriesOff = intake.category_mode === 'off';
    const picker = (categoriesOff || !categories.length) ? '' : (
      '<select class="card-file-select" data-role="file-category" aria-label="topic for this essay">'
      + categories.map((cat) => `<option value="${escapeHtml(cat)}"${cat === intake.category ? ' selected' : ''}>${escapeHtml(String(cat).toLowerCase())}</option>`).join('')
      + '</select>'
    );
    const label = intake.category ? `file it into ${escapeHtml(String(intake.category).toLowerCase())}` : 'file it';
    const stuck = !categoriesOff && !categories.length;
    return `<div class="card-filing">${picker}<button type="button" class="card-file-btn" data-action="intake-apply"${stuck ? ' disabled' : ''}>${label}</button></div>`;
  }

  // Why a card is showing while "needs attention" is on: what it is missing.
  function attentionMarkup(essay) {
    const line = attentionLine(essay);
    return line ? `<p class="card-attention">${escapeHtml(line)}</p>` : '';
  }

  function toolMarkup(tierKey) {
    const tool = toolFor(tierKey);
    const parts = '<span class="tool-part"></span>'.repeat(tool.parts);
    return `<div class="tool tool-${tool.key}" role="img" aria-label="${escapeHtml(tool.label)}"><span class="tool-body">${parts}</span></div>`;
  }

  function barsMarkup(words) {
    const filled = barCount(words);
    let bars = '';
    for (let i = 1; i <= 5; i += 1) {
      bars += `<span class="bar${i <= filled ? ' is-filled' : ''}"></span>`;
    }
    return `<span class="bars bars-${filled}" aria-hidden="true">${bars}</span>`;
  }

  function postitMarkup(essay) {
    const { pad, color } = padFor(essay);
    const starred = phaseOf(essay) === 'likey';
    const pin = pad === 'index' ? '<span class="postit-pin" aria-hidden="true"></span>' : '';
    const star = starred
      ? `<svg class="postit-star is-drawn" width="44" height="44" viewBox="0 0 40 40" aria-hidden="true" focusable="false"><path d="${STAR_PATH}"/><path class="postit-scribble" d="M13 20 L27 19 M15 25 L25 24"/></svg>`
      : `<svg class="postit-star is-hint" width="44" height="44" viewBox="0 0 40 40" aria-hidden="true" focusable="false"><path d="${STAR_PATH}"/></svg>`;
    // One stable name; aria-pressed carries the state, so a screen reader
    // announces "pressed" or "not pressed" once rather than a second label.
    return `<button type="button" class="card-postit note pad-${pad} pad-${color}" data-action="star" aria-pressed="${starred}" aria-label="star for writers likey">${pin}${star}</button>`;
  }

  // A parked card's handle slot: when it was parked, and the way back.
  function unparkMarkup(sinceLabel) {
    return `<span class="card-rain-since">${escapeHtml(sinceLabel)}</span>`
      + `<button type="button" class="card-unpark" data-action="unpark">${BACK_ICON}back to the room</button>`;
  }

  // The grabber: a six-dot grip, the card's one way to move. On a writers
  // likey card it drags to a Monday on the board (or presses, or A, for
  // placing mode) and to rainy day in the sidebar. A writers room card can
  // only go to rainy day: it cannot go up on the board until it has its star.
  // A scheduled card's note is already up, so it has none.
  function handleMarkup(phase) {
    if (phase === 'room') {
      return `<button type="button" class="card-handle" data-action="place" draggable="true" aria-label="move to rainy day" title="drag to rainy day in the sidebar">${GRIP_ICON}</button>`;
    }
    return `<button type="button" class="card-handle" data-action="place" draggable="true" aria-label="place on the board" title="drag to the board or to rainy day, or press to place it with the keyboard">${GRIP_ICON}</button>`;
  }

  // ctx: { now, totems: {key: {label, image}}, redPen: {enabled, lines},
  //        presets: [...], error: '', intake: {},
  //        attention: false }
  function cardMarkup(essay, ctx) {
    const context = ctx || {};
    const id = String(essay?.id || '');
    const isParked = essay?.status === 'Archived';
    const shown = displayEssay(essay);
    const phase = phaseOf(shown);
    const size = sizeTier(shown);
    const tier = ageTier(essay?.arrived_at, context.now);
    const bars = barCount(essay?.word_count);
    const title = String(essay?.title || 'untitled');
    const stamp = stampFor(shown);
    const line = pageLine(essay, tier, context.redPen);
    const totem = totemFor(essay, context.totems);
    const topic = topicFor(essay, context.presets);
    const showPostit = phase === 'room' || phase === 'likey';
    // The drawn scheduled and live cards carry no writing tool: the note has
    // gone up to the board and the page is done with.
    const showTool = phase === 'room' || phase === 'likey';
    const titleId = `card-title-${escapeHtml(id)}`;
    const classes = ['card', `size-${size.key}`, `phase-${phase}`, `age-${tier}`];
    if (isParked) classes.push('is-parked');
    let cover = '';
    if (totem) {
      cover += `<img class="card-totem" src="${escapeHtml(totem.image)}" alt="${escapeHtml(totem.label)} totem" decoding="async" loading="lazy">`;
    }
    // The stack of sheets behind the page tops out at three.
    for (let k = 0; k < Math.min(bars, 4) - 1; k += 1) {
      cover += `<div class="card-sheet card-sheet-${k}" aria-hidden="true"></div>`;
    }
    let lineMarkup = '';
    if (line.kind === 'jab') {
      lineMarkup = `<p class="card-jab"><span class="visually-hidden">red pen: </span>${escapeHtml(line.text)}</p>`;
    } else if (line.kind === 'excerpt') {
      lineMarkup = `<p class="card-excerpt">${escapeHtml(line.text)}</p>`;
    }
    cover += '<div class="card-page">'
      + '<span class="card-page-gap" aria-hidden="true"></span>'
      + `<p class="card-page-title" aria-hidden="true">${escapeHtml(title)}</p>`
      + lineMarkup
      + (showTool ? toolMarkup(tier) : '')
      + '<span class="card-page-gap" aria-hidden="true"></span>'
      + stampMarkup(stamp)
      + '</div>';
    if (showPostit) cover += postitMarkup(shown);

    // One line above the title, left to right: the topic, the air chip, "file
    // it" (while nothing is suggested yet), the length bars, obsidian, and the
    // grabber. The topic takes what is left and is cut short before it can
    // push the rest off the card.
    let topicMarkup = '';
    if (topic) {
      // A preset colour is the writer's own setting, so it is shape-checked
      // before it reaches a style attribute.
      const dot = /^#[0-9a-f]{3,8}$/i.test(topic.color) ? ` style="--topic:${topic.color}"` : '';
      topicMarkup = `<span class="card-topic-dot"${dot} aria-hidden="true"></span><span class="card-topic">${escapeHtml(topic.name)}</span>`;
    }
    let meta = `<span class="card-meta-start">${topicMarkup}</span>`;
    if (phase === 'ready' && essay?.scheduled_at) {
      const short = formatAirShort(essay.scheduled_at);
      meta += `<span class="card-air" title="airs ${escapeHtml(formatAirDay(essay.scheduled_at))}">${AIR_ICON}airdate ${escapeHtml(short)}</span>`;
    } else if (phase === 'live' && isPostLink(essay?.substack_url)) {
      meta += `<a class="card-live" href="${escapeHtml(essay.substack_url)}" rel="noopener">${LIVE_ICON}live</a>`;
    }
    if (essay?.needs_intake && !(context.intake && context.intake[id])) {
      meta += '<button type="button" class="card-file-btn" data-action="intake-suggest">file it</button>';
    }
    const words = Number(essay?.word_count) || 0;
    const count = words.toLocaleString('en-US');
    meta += `<span class="card-length" title="${escapeHtml(count)} words · ${BAR_NAMES[barCount(words) - 1]}">${barsMarkup(words)}<span class="visually-hidden">${escapeHtml(count)} words</span></span>`;
    if (essay?.obsidian_url) {
      meta += `<a class="card-obsidian" href="${escapeHtml(essay.obsidian_url)}" aria-label="open in obsidian" title="open in obsidian">${OBSIDIAN_GEM}</a>`;
    }
    if (!isParked && (phase === 'likey' || phase === 'room')) meta += handleMarkup(phase);

    // A parked card has one more row: when it was parked, and the way back.
    // An essay parked by an older airdate has no archived_at, so no date.
    let actions = '';
    if (isParked) {
      const since = formatStampDate(essay?.archived_at, 'instant');
      actions = unparkMarkup(since ? `in the rain since ${since}` : 'in the rain');
    }

    const error = context.error
      ? `<p class="card-error" role="alert">${escapeHtml(context.error)}</p>`
      : '';

    return `<article class="${classes.join(' ')}" data-essay-id="${escapeHtml(id)}" aria-labelledby="${titleId}">`
      + `<div class="card-cover">${cover}</div>`
      + '<div class="card-foot">'
      + `<div class="card-meta">${meta}</div>`
      + `<h3 class="card-title"><a class="card-link" id="${titleId}" href="${escapeHtml(editorHref(essay))}">${escapeHtml(title)}</a></h3>`
      + (context.attention ? attentionMarkup(essay) : '')
      + filingMarkup(essay, context.intake && context.intake[id])
      + error
      + '<div class="card-feedback"></div>'
      + (actions ? `<div class="card-actions">${actions}</div>` : '')
      + '</div></article>';
  }

  return {
    AGE_TIERS,
    SIZES,
    ageDays,
    ageTier,
    toolFor,
    barCount,
    BAR_STEPS,
    barsMarkup,
    formatWords,
    phaseOf,
    isComplete,
    sizeTier,
    formatStampDate,
    formatAirDay,
    formatAirShort,
    stampFor,
    hashString,
    jabFor,
    pageLine,
    closestToAirSort,
    rainyDaySort,
    POOL_SORTS,
    poolSort,
    needsAttention,
    attentionLine,
    displayStatusOf,
    displayEssay,
    distribute,
    estimatedHeight,
    topicFor,
    distinctTopics,
    padFor,
    totemFor,
    ROOM_PATH,
    routeOfHash,
    routeOfHref,
    editorHref,
    escapeHtml,
    stampMarkup,
    filingMarkup,
    cardMarkup,
  };
});
