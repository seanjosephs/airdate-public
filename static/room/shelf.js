// The shelf's pure functions and markup (slice 6): grouping by month, the
// row, filtering. Nothing here touches the DOM or the network, so node can
// test all of it, the same split as cards.js/pool.js and board.js/board-view.js.
// shelf-view.js renders what this returns and owns the round trips.
//
// Two words that must not collide: "the shelf" (this screen) shows every LIVE
// essay. "On the shelf" is the current month; "the archive" is everything
// earlier, grouped by month, newest first - a VIEW only, never the Archive/
// folder on disk (that is rainy day, status Archived, a different essay
// entirely). Nothing here ever reads or writes status Archived.
(function installShelf(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AirdateShelf = api;
})(typeof globalThis === 'object' ? globalThis : this, function createShelf() {
  const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july',
    'august', 'september', 'october', 'november', 'december'];

  function esc(value) {
    const Cards = (typeof globalThis === 'object' && globalThis.AirdateCards) || (typeof window === 'object' && window.AirdateCards);
    return Cards ? Cards.escapeHtml(value) : String(value ?? '');
  }

  // The air date a live essay went up on: published_date (stamped when it
  // went live, since slice 3 the air date) falling back to scheduled_at, the
  // same fallback the card's stamp uses.
  function airDateOf(essay) {
    const value = String((essay && essay.published_date) || (essay && essay.scheduled_at) || '').trim();
    return /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : '';
  }

  function monthKey(dateStr) {
    return /^\d{4}-\d{2}/.test(String(dateStr || '')) ? dateStr.slice(0, 7) : '';
  }

  function monthLabel(key) {
    const match = /^(\d{4})-(\d{2})$/.exec(String(key || ''));
    if (!match) return '';
    const month = Number(match[2]);
    return month >= 1 && month <= 12 ? `${MONTHS[month - 1]} ${match[1]}` : '';
  }

  function todayKey(now) {
    const date = now instanceof Date ? now : new Date();
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
  }

  // "on the shelf": live this month. "the archive": every earlier month,
  // newest first - a essay with no readable date sits with the current
  // month rather than vanishing from the screen.
  function splitByMonth(essays, now) {
    const current = todayKey(now);
    const onShelf = [];
    const byMonth = new Map();
    for (const essay of Array.isArray(essays) ? essays : []) {
      const key = monthKey(airDateOf(essay));
      if (!key || key === current) {
        onShelf.push(essay);
        continue;
      }
      if (!byMonth.has(key)) byMonth.set(key, []);
      byMonth.get(key).push(essay);
    }
    onShelf.sort((a, b) => airDateOf(b).localeCompare(airDateOf(a)));
    const archive = Array.from(byMonth.keys())
      .sort((a, b) => b.localeCompare(a))
      .map((key) => ({
        key,
        label: monthLabel(key),
        essays: byMonth.get(key).sort((a, b) => airDateOf(b).localeCompare(airDateOf(a))),
      }));
    return { onShelf, archive, currentKey: current };
  }

  function yearOf(essay) {
    const date = airDateOf(essay);
    return date ? date.slice(0, 4) : '';
  }

  function topicOf(essay, presets) {
    const Cards = (typeof globalThis === 'object' && globalThis.AirdateCards) || (typeof window === 'object' && window.AirdateCards);
    return Cards ? Cards.topicFor(essay, presets) : null;
  }

  // search covers title, tags and the essay text - the index's search_blob,
  // the same engine the pool already searches with.
  function matchesFilters(essay, filters, presets) {
    const f = filters || {};
    if (f.totems && f.totems.size && !f.totems.has(String(essay.totem_raw || '').trim().toLowerCase())) return false;
    if (f.topic) {
      const topic = topicOf(essay, presets);
      if (!topic || topic.name !== f.topic) return false;
    }
    if (f.year && yearOf(essay) !== f.year) return false;
    const terms = String(f.query || '').toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return true;
    const hay = `${essay.search_blob || ''} ${String(essay.excerpt || '').toLowerCase()}`;
    return terms.every((term) => hay.includes(term));
  }

  // The pool's topic menu asks the same function, so the two list the same
  // topics by construction.
  function distinctTopics(essays, presets) {
    const Cards = (typeof globalThis === 'object' && globalThis.AirdateCards) || (typeof window === 'object' && window.AirdateCards);
    return Cards ? Cards.distinctTopics(essays, presets) : [];
  }

  function distinctYears(essays) {
    const years = new Set();
    for (const essay of Array.isArray(essays) ? essays : []) {
      const year = yearOf(essay);
      if (year) years.add(year);
    }
    return Array.from(years).sort((a, b) => b.localeCompare(a));
  }

  function isPostLink(value) {
    return /^https:\/\//i.test(String(value || '').trim());
  }

  // "sep 21 2026" - the Courier cell renders it uppercase through CSS, the
  // same way the card's stamp does.
  function formatAirDate(dateStr) {
    const Cards = (typeof globalThis === 'object' && globalThis.AirdateCards) || (typeof window === 'object' && window.AirdateCards);
    return Cards ? Cards.formatStampDate(dateStr, 'calendar') : (dateStr || '');
  }

  // One row. totem | title+subtitle | topic | length | went live | read on
  // substack | open in obsidian. The title is the row's own open action.
  function rowMarkup(essay, ctx) {
    const context = ctx || {};
    const Cards = (typeof globalThis === 'object' && globalThis.AirdateCards) || (typeof window === 'object' && window.AirdateCards);
    const id = esc(essay.id);
    const title = String(essay.title || 'untitled');
    const subtitle = String(essay.subtitle || '').trim();
    const totem = context.totems && essay.totem_raw ? context.totems[String(essay.totem_raw).toLowerCase()] : null;
    const totemCell = totem && totem.image
      ? `<img src="${esc(totem.image)}" alt="${esc(totem.label || essay.totem_raw)} totem" width="40" height="52" decoding="async" loading="lazy">`
      : '<span class="shelf-no-totem" aria-hidden="true"></span>';
    const topic = topicOf(essay, context.presets);
    const topicCell = topic
      ? `<span class="shelf-topic-dot"${/^#[0-9a-f]{3,8}$/i.test(topic.color) ? ` style="--topic:${topic.color}"` : ''} aria-hidden="true"></span><span class="shelf-topic-name">${esc(topic.name)}</span>`
      : '<span class="shelf-topic-name shelf-muted">&mdash;</span>';
    const words = Number(essay.word_count) || 0;
    const bars = Cards ? Cards.barsMarkup(words) : '';
    const went = airDateOf(essay);
    const wentCell = went ? esc(formatAirDate(went)) : '<span class="shelf-muted">&mdash;</span>';
    const readLink = isPostLink(essay.substack_url)
      ? `<a class="shelf-read" href="${esc(essay.substack_url)}" rel="noopener">read on substack<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M14 5h5v5M19 5l-8 8M11 7H6v11h11v-5"/></svg></a>`
      : '<span class="shelf-muted">&mdash;</span>';
    const obsidianLink = essay.obsidian_url
      ? `<a class="shelf-obsidian" href="${esc(essay.obsidian_url)}" aria-label="open ${esc(title)} in obsidian" title="open in obsidian"><img class="obsidian-logo" src="/static/brand/obsidian-logo.png" alt="" width="24" height="24"></a>`
      : '';
    return `<div role="row" class="shelf-row" data-essay-id="${id}">`
      + `<div role="cell" class="shelf-cell shelf-cell-totem">${totemCell}</div>`
      + `<div role="cell" class="shelf-cell shelf-cell-title"><a class="shelf-title-link" href="/airdate?essay=${id}">${esc(title)}</a>`
      + (subtitle ? `<span class="shelf-subtitle">${esc(subtitle)}</span>` : '')
      + '</div>'
      + `<div role="cell" class="shelf-cell shelf-cell-topic">${topicCell}</div>`
      + `<div role="cell" class="shelf-cell shelf-cell-length" title="${esc(words.toLocaleString('en-US'))} words">${bars}${esc(words.toLocaleString('en-US'))}</div>`
      + `<div role="cell" class="shelf-cell shelf-cell-went">${wentCell}</div>`
      + `<div role="cell" class="shelf-cell shelf-cell-read">${readLink}</div>`
      + `<div role="cell" class="shelf-cell shelf-cell-obsidian">${obsidianLink}</div>`
      + '</div>';
  }

  // A data table needs column headers, or a screen reader reads each cell
  // with no idea which column it is in. The design draws no visible header
  // row, so this one is visually hidden: present to assistive tech, invisible
  // on screen, and out of the grid's layout.
  const COLUMN_HEADERS = ['totem', 'title', 'topic', 'length', 'went live', 'post', 'obsidian'];
  const HEADER_ROW = '<div role="row" class="shelf-colheads visually-hidden">'
    + COLUMN_HEADERS.map((name) => `<div role="columnheader">${name}</div>`).join('')
    + '</div>';

  function tableMarkup(label, rows, ctx) {
    return `<div role="table" aria-label="${esc(label)}" class="shelf-table">${HEADER_ROW}${rows.map((essay) => rowMarkup(essay, ctx)).join('')}</div>`;
  }

  function monthHeadingMarkup(label, sub) {
    return `<div class="shelf-month-head"><span class="shelf-month-tag">${esc(label)}</span>`
      + (sub ? `<span class="shelf-month-sub">${esc(sub)}</span>` : '') + '</div>';
  }

  return {
    airDateOf,
    monthKey,
    monthLabel,
    todayKey,
    splitByMonth,
    yearOf,
    topicOf,
    matchesFilters,
    distinctTopics,
    distinctYears,
    isPostLink,
    formatAirDate,
    rowMarkup,
    tableMarkup,
    monthHeadingMarkup,
  };
});
