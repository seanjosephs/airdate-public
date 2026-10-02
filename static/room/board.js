// The board: pure functions from the essay list to what the cork shows.
//
// One slot per Monday. An essay belongs to the week its air date falls in, so
// a Tuesday air date sits in that week's Monday slot; a week holding two says
// so. Nothing in here touches the DOM or the network, so node can test all of
// it (tests/test_room_board_js.py). board-view.js renders what this returns
// and owns the round trips.
(function installRoomBoard(root, factory) {
  const inNode = typeof module === 'object' && module.exports;
  /* global require */
  const Dates = inNode ? require('./dates.js') : root.RoomDates;
  const Cards = inNode ? require('./cards.js') : root.AirdateCards;
  const api = factory(Dates, Cards);
  if (inNode) module.exports = api;
  if (root) root.RoomBoard = api;
})(typeof globalThis === 'object' ? globalThis : this, function createRoomBoard(Dates, Cards) {
  const esc = Cards.escapeHtml;
  // How far placing mode looks for an open Monday before it gives up.
  const SEARCH_WEEKS = 520;
  const TILTS = [-1.5, 2, -2.5, 1.5];

  const UNSCHEDULE_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M9 14l-4-4 4-4M5 10h9a5 5 0 0 1 0 10h-3"/></svg>';
  const SENT_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 3L10 14M21 3l-7 18-4-7-7-4z"/></svg>';
  const PLUS_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14"/></svg>';
  const SHELF_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 20h18M6 20V9h4v11M10 20V5h4v15M15 20l2-12 3.5 1L18 20"/></svg>';
  const WARN_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 4l9 16H3zM12 10v4M12 17v.5"/></svg>';

  function lower(value) {
    return String(value || 'untitled').trim().toLowerCase();
  }

  // ---- which week holds what ----------------------------------------------

  // The calendar day an essay airs on the board, or '' if it is not on it.
  // Live essays keep their air date; one that went live without one falls
  // back to the day it was stamped.
  function airDayOf(essay) {
    const status = essay && essay.status;
    if (status === 'Ready for Air') return Dates.calendarDay(essay.scheduled_at);
    if (status === 'Live') return Dates.calendarDay(essay.scheduled_at) || Dates.calendarDay(essay.published_date);
    return '';
  }

  // monday -> [{essay, air, phase}], ready before live, then by air date.
  // `anchor` is the configured publish day as a weekday number (0 sunday .. 6
  // saturday, Dates.weekdayNumber's convention); it defaults to monday so
  // every existing caller keeps today's behavior.
  function weekIndex(essays, anchor) {
    const index = new Map();
    for (const essay of Array.isArray(essays) ? essays : []) {
      const air = airDayOf(essay);
      if (!air) continue;
      const monday = Dates.weekAnchorOf(air, anchor);
      const entry = { essay, air, phase: essay.status === 'Live' ? 'live' : 'ready' };
      if (!index.has(monday)) index.set(monday, []);
      index.get(monday).push(entry);
    }
    for (const list of index.values()) {
      list.sort((a, b) => {
        if (a.phase !== b.phase) return a.phase === 'ready' ? -1 : 1;
        if (a.air !== b.air) return a.air < b.air ? -1 : 1;
        return String(a.essay.title || '').localeCompare(String(b.essay.title || ''));
      });
    }
    return index;
  }

  function isPast(monday, today) {
    return Dates.dayNumber(monday) < Dates.dayNumber(today);
  }

  // Open: not yet passed, and nothing in that week.
  function isOpen(monday, today, index) {
    return !isPast(monday, today) && !((index.get(monday) || []).length);
  }

  // The nearest open Monday at or after today.
  function firstOpen(today, index, anchor) {
    let monday = Dates.weekAnchorOf(today, anchor);
    if (isPast(monday, today)) monday = Dates.addDays(monday, 7);
    for (let i = 0; i < SEARCH_WEEKS; i += 1) {
      if (isOpen(monday, today, index)) return monday;
      monday = Dates.addDays(monday, 7);
    }
    return '';
  }

  // The next open Monday from `from` in `direction` (+1 later, -1 earlier),
  // skipping taken and past ones. No wrap: '' when there is none.
  function stepOpen(from, direction, today, index) {
    const step = direction < 0 ? -7 : 7;
    let monday = Dates.addDays(from, step);
    for (let i = 0; i < SEARCH_WEEKS; i += 1) {
      if (step < 0 && isPast(monday, today)) return '';
      if (isOpen(monday, today, index)) return monday;
      monday = Dates.addDays(monday, step);
    }
    return '';
  }

  // The Mondays on the board. The window is last week plus `weeksShown` from
  // this week, moved by the pager's `offset`. On the home page it also keeps
  // any older week whose note is still asking "is it live?", so nothing that
  // aired unrecorded falls off the board.
  function windowMondays(today, weeksShown, offset, index, anchor) {
    const count = Math.max(1, Math.floor(Number(weeksShown) || 3));
    const base = Dates.addDays(Dates.weekAnchorOf(today, anchor), -7 + 7 * (Number(offset) || 0));
    const mondays = [];
    for (let i = 0; i <= count; i += 1) mondays.push(Dates.addDays(base, 7 * i));
    if (!offset) {
      for (const [monday, entries] of index) {
        if (Dates.dayNumber(monday) >= Dates.dayNumber(base)) continue;
        if (entries.some((entry) => entry.phase === 'ready' && Dates.daysBetween(today, entry.air) <= 0)) {
          mondays.push(monday);
        }
      }
      mondays.sort();
    }
    return mondays;
  }

  // The pager offset that brings `monday` into the consecutive weeks.
  function offsetShowing(monday, today, weeksShown, offset, anchor) {
    const count = Math.max(1, Math.floor(Number(weeksShown) || 3));
    const base = Dates.addDays(Dates.weekAnchorOf(today, anchor), -7);
    const at = Math.round(Dates.daysBetween(base, monday) / 7);
    const current = Number(offset) || 0;
    if (at < current) return at;
    if (at > current + count) return at - count;
    return current;
  }

  // "sep 21 to oct 12"
  function rangeLabel(mondays) {
    if (!mondays.length) return '';
    return `${Dates.formatShort(mondays[0])} to ${Dates.formatShort(mondays[mondays.length - 1])}`;
  }

  // ---- one slot ------------------------------------------------------------

  function countdown(days) {
    if (days === 1) return 'airs tomorrow';
    return `airs in ${days} days`;
  }

  // ctx: { today, index, placing: {essay, candidate} | null, shown: Map,
  //        settling: Set of mondays whose live plaque is still green }
  function slotModel(monday, ctx) {
    const entries = ctx.index.get(monday) || [];
    const past = isPast(monday, ctx.today);
    const open = !past && !entries.length;
    // The writer's own pick when they asked for the other one; otherwise the
    // note on air today, otherwise the first.
    const onAirAt = entries.findIndex((item) => item.phase === 'ready' && item.air === ctx.today);
    const asked = ctx.shown && ctx.shown.has(monday) ? Number(ctx.shown.get(monday)) || 0 : Math.max(0, onAirAt);
    const pick = entries.length ? asked % entries.length : 0;
    const entry = entries[pick] || null;
    const label = Dates.formatDay(monday);
    let kind = 'open';
    let plaque = 'open';
    let days = null;
    if (!entry) {
      kind = past ? 'past' : 'open';
      plaque = past ? 'past' : 'open';
    } else if (entry.phase === 'live') {
      kind = 'shelf';
      plaque = `aired ${Dates.formatDay(entry.air)} · on the shelf`;
    } else {
      days = Dates.daysBetween(ctx.today, entry.air);
      if (days === 0) { kind = 'onair'; plaque = 'ON AIR'; }
      // Aired and not marked live: no plaque. The note's live button pulses
      // red as the reminder.
      else if (days < 0) { kind = 'asking'; plaque = ''; }
      else { kind = 'scheduled'; plaque = countdown(days); }
    }
    const placing = ctx.placing || null;
    const candidate = Boolean(placing && placing.candidate === monday);
    let dim = kind === 'shelf' && !(ctx.settling && ctx.settling.has(monday));
    if (placing && !candidate && !open) {
      dim = true;
      plaque = past && !entry ? 'past' : 'taken';
    }
    if (candidate) plaque = `placing · airs ${label} if you set it`;
    return {
      monday,
      label,
      past,
      open,
      kind,
      plaque,
      days,
      entry,
      entries,
      pick,
      dim,
      candidate,
      // A taken week still in the future takes a drop, so it can say why not.
      takesDrop: !past && kind !== 'shelf',
    };
  }

  function zoneLabel(slot) {
    if (slot.candidate) return `${slot.label}, open. enter sets it here.`;
    if (!slot.entry) return `${slot.label}, ${slot.past ? 'past' : 'open'}`;
    const title = lower(slot.entry.essay.title);
    const more = slot.entries.length > 1 ? `, ${slot.entries.length} essays this week` : '';
    if (slot.kind === 'onair') return `${slot.label}, ${title}, on air today${more}`;
    if (slot.kind === 'shelf') return `${slot.label}, ${title}, on the shelf${more}`;
    if (slot.kind === 'asking') return `${slot.label}, ${title}, aired, not marked live yet${more}`;
    return `${slot.label}, ${title}, ${slot.plaque}${more}`;
  }

  // ---- the house voice -------------------------------------------------------

  // Why something failed and what to do, by the api's error kind.
  function why(error) {
    const kind = error && error.kind;
    if (kind === 'file-changed') return 'the note changed in obsidian, so the board has the latest now. try again.';
    if (kind === 'network') return 'airdate is not answering. try again.';
    if (kind === 'not-found') return 'the note is no longer in your essays folder.';
    if (kind === 'setup') return 'setup is not finished. finish it in settings first.';
    return 'obsidian did not save the note. try again.';
  }

  // One sentence each, lowercase: what happened, then what is true now.
  const say = {
    scheduled: (monday) => `on the board. airs ${Dates.formatDay(monday)}.`,
    taken: (monday, title) => `${Dates.formatDay(monday)} is taken. unschedule ${lower(title)} first.`,
    past: (monday, weekdayName) => `${Dates.formatDay(monday)} has passed. pick a ${weekdayName || 'monday'} from today on.`,
    unscheduled: (monday) => `back in the pool. ${Dates.formatDay(monday)} is open again.`,
    live: (title) => `live. ${lower(title)} moved to the shelf.`,
    notAPost: () => 'that is not a substack post link.',
    didNotAir: (monday) => `back in the pool. ${Dates.formatDay(monday)} stays empty.`,
    // Unschedule or "did not air" in a week that holds another note.
    stillThere: (monday, title) => `back in the pool. ${lower(title)} is still on ${Dates.formatDay(monday)}.`,
    putBack: () => 'put back. nothing changed.',
    lifting: (title, monday) => `placing ${lower(title)}. ${Dates.formatDay(monday)} is open. left and right move, enter sets, escape puts it back.`,
    moved: (monday) => `${Dates.formatDay(monday)} is open.`,
    noEarlier: (monday, weekdayName) => `no open ${weekdayName || 'monday'} before ${Dates.formatDay(monday)}. it stays there.`,
    noOpen: (weekdayName) => `there is no open ${weekdayName || 'monday'} to put it on. unschedule one first.`,
    cannotLift: (status) => (status === 'Writers Room'
      ? 'star it for writers likey before it can go on the board.'
      : 'it is on the board already. unschedule it from its note to move it.'),
    undoneSchedule: (monday) => `taken off the board. ${Dates.formatDay(monday)} is open again.`,
    undoneUnschedule: (monday) => `back on the board. airs ${Dates.formatDay(monday)}.`,
    undoTaken: (monday) => `${Dates.formatDay(monday)} is taken now, so it stays in the pool.`,
    undoPast: (monday) => `${Dates.formatDay(monday)} has passed, so it stays in the pool.`,
    undoStale: () => 'could not undo. the note changed since, so nothing was overwritten.',
    failed: (action, error) => {
      const lead = {
        schedule: 'could not put it on the board.',
        unschedule: 'could not unschedule.',
        live: 'could not mark it live.',
        didNotAir: 'could not take it off the board.',
        undo: 'could not undo.',
        load: 'could not load the board.',
      }[action] || 'that did not work.';
      return `${lead} ${why(error)}`;
    },
  };

  // ---- the live link, checked by shape --------------------------------------

  function bareHost(value) {
    let raw = String(value || '').trim();
    if (!raw) return '';
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) raw = `https://${raw}`;
    try {
      const host = new URL(raw).hostname.toLowerCase().replace(/\.$/, '');
      return host.startsWith('www.') ? host.slice(4) : host;
    } catch (error) {
      return '';
    }
  }

  // The same rule the server holds (live_link_refusal): https, substack.com or
  // a subdomain or the writer's custom domain, and one post: the path is
  // /p/<slug>, a query or fragment allowed. The host is DNS labels, none
  // empty, never %-escaped. Never fetched.
  const POST_PATH = /^\/p\/[^/\s]+\/?$/;
  const DNS_LABEL = /^[a-z0-9-]+$/;

  function isDnsHost(host) {
    const labels = String(host || '').split('.');
    return labels.length >= 2 && labels.every((label) => DNS_LABEL.test(label));
  }

  function liveLinkRefusal(value, publication) {
    const text = String(value || '').trim();
    // The URL parser decodes a %-escape in the host; the server does not, so
    // the raw text is checked before it gets the chance.
    const authority = (text.match(/^[a-z][a-z0-9+.-]*:\/\/([^/?#]*)/i) || [])[1] || '';
    if (authority.includes('%')) return say.notAPost();
    let url;
    try {
      url = new URL(text);
    } catch (error) {
      return say.notAPost();
    }
    if (url.protocol !== 'https:' || url.username || url.password) return say.notAPost();
    if (url.port && url.port !== '443') return say.notAPost();
    const host = url.hostname.toLowerCase().replace(/\.$/, '');
    if (!isDnsHost(host)) return say.notAPost();
    const custom = bareHost(publication);
    const onSubstack = host === 'substack.com' || host.endsWith('.substack.com');
    const onCustom = Boolean(custom) && (host === custom || host === `www.${custom}`);
    if (!onSubstack && !onCustom) return say.notAPost();
    if (!POST_PATH.test(url.pathname)) return say.notAPost();
    return null;
  }

  // ---- markup --------------------------------------------------------------

  function tiltFor(monday) {
    return TILTS[Cards.hashString(monday) % TILTS.length];
  }

  // A note on the cork. ctx: { asking, aired, error, ghost, value }. From air
  // day on the note asks "is it live?"; once the day has passed it says aired.
  function noteMarkup(entry, monday, ctx) {
    const context = ctx || {};
    const essay = entry.essay;
    const id = esc(String(essay.id || ''));
    const { pad, color } = Cards.padFor(essay);
    const title = String(essay.title || 'untitled');
    const asking = Boolean(context.asking);
    const classes = ['board-note', 'note', `pad-${pad}`, `pad-${color}`];
    if (asking) classes.push('is-asking');
    if (context.error) classes.push('has-error');
    if (context.ghost) classes.push('is-ghost');
    const tilt = context.ghost ? 0 : tiltFor(monday);
    let html = `<div class="${classes.join(' ')}" data-essay-id="${id}" style="--tilt:${tilt}deg"${context.ghost ? ' aria-hidden="true"' : ''}>`;
    if (pad === 'index') html += '<span class="note-pin" aria-hidden="true"></span>';
    html += `<p class="board-note-title">${esc(title)}</p>`;
    const dateText = context.aired ? `aired ${Dates.formatDay(entry.air)}` : Dates.formatDay(context.ghost ? monday : entry.air);
    html += `<p class="board-note-date">${esc(dateText)}</p>`;
    html += '<span class="board-note-grow"></span>';
    if (context.ghost) return `${html}</div>`;
    if (asking) {
      const field = `live-${id}`;
      const errorId = `live-error-${id}`;
      const invalid = context.error ? ` aria-invalid="true" aria-describedby="${errorId}"` : '';
      html += '<div class="board-note-ask">'
        + `<label for="${field}">is it live?</label>`
        + `<button type="button" class="note-text-button" data-action="did-not-air" data-essay-id="${id}">it did not air</button>`
        + '</div>'
        + '<div class="board-note-field">'
        + `<input id="${field}" type="url" inputmode="url" placeholder="paste the post link" autocomplete="off" spellcheck="false" data-essay-id="${id}" value="${esc(context.value || '')}"${invalid}>`
        + `<button type="button" class="note-live-button" data-action="live" data-essay-id="${id}">live</button>`
        + '</div>';
      if (context.error) {
        html += `<p class="board-note-error" id="${errorId}" role="alert">${WARN_ICON}<span>${esc(context.error)}</span></p>`;
      }
      return `${html}</div>`;
    }
    html += '<div class="board-note-actions">'
      + `<button type="button" class="note-pill" data-action="unschedule" data-essay-id="${id}" aria-label="unschedule ${esc(lower(title))}">${UNSCHEDULE_ICON}<span>unschedule</span></button>`;
    const sent = String(essay.substack_draft_url || '').trim();
    if (/^https:\/\//i.test(sent)) {
      html += `<a class="note-sent" href="${esc(sent)}" target="_blank" rel="noopener" aria-label="sent to substack, open the draft" title="sent to substack: open the draft">${SENT_ICON}<span>sent</span></a>`;
    }
    html += '</div></div>';
    return html;
  }

  // ctx adds to slotModel's: { lifted: essay row while placing, errors: Map
  // essayId -> sentence, values: Map essayId -> field text }
  function slotMarkup(slot, ctx) {
    const context = ctx || {};
    const classes = ['slot', `kind-${slot.kind}`];
    if (slot.open) classes.push('is-open');
    if (slot.dim) classes.push('is-dim');
    if (slot.candidate) classes.push('is-candidate');
    if (slot.takesDrop) classes.push('takes-drop');
    const monday = esc(slot.monday);
    let zone = '';
    if (slot.candidate && context.lifted) {
      zone = '<div class="slot-ghost">'
        + `<span class="slot-set-label">enter sets · ${esc(slot.label)}</span>`
        + `<div class="slot-ghost-frame">${noteMarkup({ essay: context.lifted, air: slot.monday }, slot.monday, { ghost: true })}</div>`
        + '</div>';
    } else if (slot.entry && slot.kind === 'shelf') {
      zone = `<span class="slot-chip">${SHELF_ICON}on the shelf</span>`;
    } else if (slot.entry) {
      const id = String(slot.entry.essay.id || '');
      const errors = context.errors || new Map();
      const values = context.values || new Map();
      zone = noteMarkup(slot.entry, slot.monday, {
        asking: slot.kind === 'asking' || slot.kind === 'onair',
        aired: slot.kind === 'asking',
        error: errors.get(id) || '',
        value: values.get(id) || '',
      });
    } else if (slot.past) {
      zone = '<span class="slot-chip">nothing aired</span>';
    } else if (context.placing) {
      zone = '<span class="slot-chip">open</span>';
    } else {
      zone = `<span class="slot-chip slot-chip-empty">${PLUS_ICON}drag an essay here</span>`;
    }
    if (slot.open) {
      zone += `<span class="slot-drop-label" aria-hidden="true">drop to air ${esc(slot.label)}</span>`;
    }
    let more = '';
    if (slot.entries.length > 1 && !slot.candidate) {
      more = `<button type="button" class="slot-more" data-action="show-other" data-monday="${monday}" data-pick="${slot.pick}">${slot.pick + 1} of ${slot.entries.length} this week · show the other</button>`;
    }
    const plaque = slot.kind === 'onair' && !context.placing
      ? '<span class="slot-onair" role="status">ON AIR</span>'
      : (slot.plaque ? `<span class="slot-plaque">${esc(slot.plaque)}</span>` : '');
    return `<li class="${classes.join(' ')}" data-monday="${monday}">`
      + `<span class="slot-date">${esc(slot.label)}</span>`
      + `<div class="slot-zone" tabindex="-1" role="group" data-monday="${monday}" aria-label="${esc(zoneLabel(slot))}">${zone}</div>`
      + more
      + `<div class="slot-foot" data-monday="${monday}">${plaque}</div>`
      + '</li>';
  }

  return {
    airDayOf,
    weekIndex,
    isPast,
    isOpen,
    firstOpen,
    stepOpen,
    windowMondays,
    offsetShowing,
    rangeLabel,
    slotModel,
    zoneLabel,
    say,
    why,
    liveLinkRefusal,
    noteMarkup,
    slotMarkup,
  };
});
