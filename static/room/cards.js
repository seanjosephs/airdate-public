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
  // The gem the old page draws for "open in obsidian", at the card's 26px.
  const OBSIDIAN_GEM = '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M12 2 4 9l3 13h10l3-13z"/><path d="M12 2 7 22"/><path d="M12 2 17 22"/><path d="M4 9h16"/></svg>';
  const AIR_ICON = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="2"/><path d="M7.5 7.5a6.5 6.5 0 0 0 0 9M16.5 7.5a6.5 6.5 0 0 1 0 9M4.5 4.5a10.5 10.5 0 0 0 0 15M19.5 4.5a10.5 10.5 0 0 1 0 15"/></svg>';
  const HANDLE_ICON = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M5 7h14M5 12h14M5 17h14"/></svg>';
  const LIVE_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/></svg>';

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

  // Length as signal bars: under 800 words one, to 1,499 two, to 2,999
  // three, 3,000 and up four.
  function barCount(words) {
    const n = Number(words) || 0;
    if (n >= 3000) return 4;
    if (n >= 1500) return 3;
    if (n >= 800) return 2;
    return 1;
  }

  function formatWords(words) {
    const n = Math.max(0, Math.round(Number(words) || 0));
    if (n < 1000) return String(n);
    return `${(n / 1000).toFixed(1).replace(/\.0$/, '')}k`;
  }

  function phaseOf(essay) {
    return PHASES[essay?.status] || 'room';
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
      return String(a.title || '').localeCompare(String(b.title || ''));
    });
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

  function editorHref(essay) {
    return `/airdate?essay=${encodeURIComponent(String(essay?.id || ''))}`;
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

  function toolMarkup(tierKey) {
    const tool = toolFor(tierKey);
    const parts = '<span class="tool-part"></span>'.repeat(tool.parts);
    return `<div class="tool tool-${tool.key}" role="img" aria-label="${escapeHtml(tool.label)}"><span class="tool-body">${parts}</span></div>`;
  }

  function barsMarkup(words) {
    const filled = barCount(words);
    let bars = '';
    for (let i = 1; i <= 4; i += 1) {
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

  // The three-bar handle: drag it to the board, or press it (or A) for
  // placing mode. Only a writers likey card has one - a writers room essay
  // cannot be scheduled, and a scheduled card's note is already up.
  function handleMarkup(placing) {
    return `<button type="button" class="card-handle" data-action="place" draggable="true" aria-pressed="${placing ? 'true' : 'false'}" aria-label="place on the board" title="drag to the board, or press to place it with the keyboard">${HANDLE_ICON}</button>`;
  }

  // ctx: { now, totems: {key: {label, image}}, redPen: {enabled, lines},
  //        presets: [...], error: '', placing: false }
  function cardMarkup(essay, ctx) {
    const context = ctx || {};
    const id = String(essay?.id || '');
    const phase = phaseOf(essay);
    const size = sizeTier(essay);
    const tier = ageTier(essay?.arrived_at, context.now);
    const bars = barCount(essay?.word_count);
    const title = String(essay?.title || 'untitled');
    const stamp = stampFor(essay);
    const line = pageLine(essay, tier, context.redPen);
    const totem = totemFor(essay, context.totems);
    const topic = topicFor(essay, context.presets);
    const showPostit = phase === 'room' || phase === 'likey';
    // The drawn scheduled and live cards carry no writing tool: the note has
    // gone up to the board and the page is done with.
    const showTool = phase === 'room' || phase === 'likey';
    const titleId = `card-title-${escapeHtml(id)}`;
    // Placing mode: the note is up on the board, and a dashed spot marks where
    // it was. Only a likey card can be placing.
    const placing = Boolean(context.placing) && phase === 'likey';

    const classes = ['card', `size-${size.key}`, `phase-${phase}`, `age-${tier}`];
    if (placing) classes.push('is-placing');
    let cover = '';
    if (totem) {
      cover += `<img class="card-totem" src="${escapeHtml(totem.image)}" alt="${escapeHtml(totem.label)} totem" decoding="async" loading="lazy">`;
    }
    for (let k = 0; k < bars - 1; k += 1) {
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
    if (placing) cover += '<div class="card-postit-spot" aria-hidden="true">note is<br>up</div>';
    else if (showPostit) cover += postitMarkup(essay);

    let meta = '';
    if (topic) {
      // A preset colour is the writer's own setting, so it is shape-checked
      // before it reaches a style attribute.
      const dot = /^#[0-9a-f]{3,8}$/i.test(topic.color) ? ` style="--topic:${topic.color}"` : '';
      meta += `<span class="card-topic-dot"${dot} aria-hidden="true"></span><span class="card-topic">${escapeHtml(topic.name)}</span>`;
    }
    const words = Number(essay?.word_count) || 0;
    meta += '<span class="card-spacer"></span>'
      + `<span class="card-length" title="${escapeHtml(words.toLocaleString('en-US'))} words">${barsMarkup(words)}${escapeHtml(formatWords(words))}<span class="visually-hidden"> words</span></span>`;

    let actions = '';
    if (phase === 'likey') {
      actions += handleMarkup(placing);
    } else if (phase === 'ready' && essay?.scheduled_at) {
      actions += `<span class="card-air">${AIR_ICON}airs ${escapeHtml(formatAirDay(essay.scheduled_at))}</span>`;
    } else if (phase === 'live' && isPostLink(essay?.substack_url)) {
      actions += `<a class="card-live" href="${escapeHtml(essay.substack_url)}" rel="noopener">${LIVE_ICON}live on substack</a>`;
    }
    actions += '<span class="card-spacer"></span>';
    if (essay?.obsidian_url) {
      actions += `<a class="card-obsidian" href="${escapeHtml(essay.obsidian_url)}" aria-label="open in obsidian" title="open in obsidian">${OBSIDIAN_GEM}</a>`;
    }

    const error = context.error
      ? `<p class="card-error" role="alert">${escapeHtml(context.error)}</p>`
      : '';

    return `<article class="${classes.join(' ')}" data-essay-id="${escapeHtml(id)}" aria-labelledby="${titleId}">`
      + `<div class="card-cover">${cover}</div>`
      + '<div class="card-foot">'
      + `<div class="card-meta">${meta}</div>`
      + `<h3 class="card-title"><a class="card-link" id="${titleId}" href="${escapeHtml(editorHref(essay))}">${escapeHtml(title)}</a></h3>`
      + error
      + '<div class="card-feedback"></div>'
      + `<div class="card-actions">${actions}</div>`
      + '</div></article>';
  }

  return {
    AGE_TIERS,
    SIZES,
    ageDays,
    ageTier,
    toolFor,
    barCount,
    formatWords,
    phaseOf,
    isComplete,
    sizeTier,
    formatStampDate,
    formatAirDay,
    stampFor,
    hashString,
    jabFor,
    pageLine,
    closestToAirSort,
    distribute,
    estimatedHeight,
    topicFor,
    padFor,
    totemFor,
    editorHref,
    escapeHtml,
    cardMarkup,
  };
});
