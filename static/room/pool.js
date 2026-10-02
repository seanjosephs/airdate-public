// The pool: every essay in the room as a script card, three flex columns.
//
// Loads /api/essays (default scope, active), plus the writer's config from
// /api/app/status for totems, tag presets and the red pen. Owns search, the
// filters, the sort and the star round trip.
//
// The board (board-view.js) changes essays too. Every changed row travels as
// one "room:essay" event on document, { oldId, essay, source }, and both sides
// take it; a row that has left the active list (live, rainy day) leaves the
// pool. window.RoomPool is what placing mode (placing.js) builds on.
(function startPool() {
  const Cards = window.AirdateCards;
  const Api = window.RoomApi;
  const Keys = window.RoomKeys;
  const Feedback = window.RoomFeedback;

  const PHASE_STATUS = {
    'writers-room': 'Writers Room',
    'writers-likey': 'Writers Likey',
    'ready-for-air': 'Ready for Air',
  };
  const DEFAULT_SORT = 'closest-to-air';

  const state = {
    essays: [],
    config: null,
    phase: 'all',
    totems: new Set(),
    query: '',
    // Needs attention stacks with the phase, totem and topic filters, so it
    // is a switch of its own and not another phase.
    attention: false,
    topic: '',
    sort: DEFAULT_SORT,
    columns: 0,
    busy: new Set(),
    errors: new Map(),
    loaded: false,
    // Cards whose note has gone up to the board (lifted with the grabber, or
    // dragged): they leave the pool's layout until the note is set or put back.
    up: new Set(),
    // The needs-filing lane (slice 6): the last /intake-suggest answer for an
    // essay, keyed by id. Undefined until the writer asks ("file it").
    intake: {},
  };
  // Phases that live in the default, active list. Anything else is the
  // shelf's or rainy day's.
  const ACTIVE = new Set(['Writers Room', 'Writers Likey', 'Ready for Air']);
  // A phase pill value that is not a status: filters on needs_intake instead.
  const NEEDS_FILING = 'needs-filing';

  const els = {};
  let roving = null;
  let searchTimer = 0;
  // The last option list written into the topic menu, so it is only rewritten
  // when the topics change.
  let topicOptions = '';

  function $(id) { return document.getElementById(id); }

  function presets() {
    const cfg = state.config || {};
    return Array.isArray(cfg.tag_presets) ? cfg.tag_presets : [];
  }

  function context(essay) {
    const cfg = state.config || {};
    const totems = {};
    for (const item of (cfg.totems && Array.isArray(cfg.totems.items) ? cfg.totems.items : [])) {
      if (item && item.key) totems[String(item.key).toLowerCase()] = item;
    }
    return {
      now: new Date(),
      totems,
      redPen: cfg.red_pen || { enabled: true, lines: [] },
      presets: presets(),
      error: essay ? state.errors.get(String(essay.id)) || '' : '',
      intake: state.intake,
      attention: state.attention,
    };
  }

  // The topic the card's own line names, so what the menu picks is what the
  // card says.
  function topicName(essay) {
    const topic = Cards.topicFor(essay, presets());
    return topic ? topic.name : '';
  }

  function validSort(value) {
    return Cards.POOL_SORTS.some((sort) => sort.key === value) ? value : DEFAULT_SORT;
  }

  function matches(essay) {
    if (state.phase === NEEDS_FILING) {
      if (!essay.needs_intake) return false;
    } else if (state.phase !== 'all' && essay.status !== PHASE_STATUS[state.phase]) {
      return false;
    }
    if (state.attention && !Cards.needsAttention(essay)) return false;
    if (state.topic && topicName(essay) !== state.topic) return false;
    if (state.totems.size && !state.totems.has(String(essay.totem_raw || '').trim().toLowerCase())) return false;
    const terms = state.query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return true;
    const hay = `${essay.search_blob || ''} ${String(essay.excerpt || '').toLowerCase()}`;
    return terms.every((term) => hay.includes(term));
  }

  // What the pool shows, in the order the sort menu names.
  function shownEssays() {
    return Cards.poolSort(state.essays.filter(matches), state.sort);
  }

  function columnCount() {
    const width = els.pool.clientWidth || window.innerWidth;
    if (width >= 900) return 3;
    if (width >= 600) return 2;
    return 1;
  }

  function cardHtml(essay) {
    return Cards.cardMarkup(essay, context(essay));
  }

  function renderHead() {
    const likey = state.essays.filter((essay) => essay.status === 'Writers Likey').length;
    els.likeyCount.textContent = String(likey);
    // Read aloud, "writers likey · 2" becomes "writers likey dot 2", right beside
    // a pill named "writers likey" that toggles the same filter. Name it for
    // what the number is. It keeps the visible words, as label-in-name requires.
    els.starPill.setAttribute('aria-label', `writers likey, ${likey} starred`);
    for (const button of els.phaseButtons) {
      button.setAttribute('aria-pressed', String(button.dataset.phase === state.phase));
    }
    els.starPill.setAttribute('aria-pressed', String(state.phase === 'writers-likey'));
    const filing = state.essays.filter((essay) => essay.needs_intake).length;
    if (els.filingPill) {
      els.filingPill.hidden = filing === 0;
      els.filingCount.textContent = String(filing);
      els.filingPill.setAttribute('aria-label', `needs filing, ${filing}`);
      els.filingPill.setAttribute('aria-pressed', String(state.phase === NEEDS_FILING));
    }
    renderTopics();
    const attention = state.essays.filter((essay) => Cards.needsAttention(essay)).length;
    if (els.attentionPill) {
      // It stays while it is on, so it can be turned off with nothing left to show.
      els.attentionPill.hidden = attention === 0 && !state.attention;
      els.attentionCount.textContent = String(attention);
      els.attentionPill.setAttribute('aria-label', `needs attention, ${attention}`);
      els.attentionPill.setAttribute('aria-pressed', String(state.attention));
    }
    for (const button of els.totemGroup.querySelectorAll('button[data-totem]')) {
      button.setAttribute('aria-pressed', String(state.totems.has(button.dataset.totem)));
    }
    renderDropLabels();
  }

  // The drop-downs say what they are set to, so a closed one still reads.
  const SHOW_LABELS = {
    all: 'all',
    'writers-room': 'writers room',
    'writers-likey': 'writers likey',
    'ready-for-air': 'ready for air',
  };
  function renderDropLabels() {
    if (els.showLabel) els.showLabel.textContent = state.phase === NEEDS_FILING ? 'needs filing' : (SHOW_LABELS[state.phase] || 'all');
    if (els.totemLabel) {
      const picked = Array.from(els.totemGroup.querySelectorAll('button[data-totem]'))
        .filter((button) => state.totems.has(button.dataset.totem));
      els.totemLabel.textContent = !picked.length ? 'any' : (picked.length === 1 ? picked[0].textContent.trim() : `${picked.length} picked`);
    }
  }

  // A drop-down closes when something outside it is pressed, on Escape (focus
  // goes back to its label), and, for "show", once a choice is made. The totem
  // menu stays open while totems are ticked, since more than one can be.
  function bindDrops() {
    const drops = Array.from(document.querySelectorAll('.pool-drop'));
    document.addEventListener('click', (event) => {
      for (const drop of drops) if (drop.open && !drop.contains(event.target)) drop.open = false;
    });
    for (const drop of drops) {
      drop.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !drop.open) return;
        event.stopPropagation();
        drop.open = false;
        const summary = drop.querySelector('summary');
        if (summary) summary.focus();
      });
      drop.addEventListener('toggle', () => {
        if (!drop.open) return;
        for (const other of drops) if (other !== drop) other.open = false;
      });
    }
    const show = document.getElementById('pool-drop-show');
    const menu = show && show.querySelector('.pool-drop-menu');
    if (menu) {
      menu.addEventListener('click', (event) => {
        if (!event.target.closest('button')) return;
        show.open = false;
        show.querySelector('summary').focus();
      });
    }
  }

  // The topic menu offers the topics the essays in the room carry. A topic no
  // essay has any more (the last one was moved or went live) is dropped
  // rather than left filtering to nothing.
  function renderTopics() {
    if (!els.topic) return;
    const names = Cards.distinctTopics(state.essays, presets());
    if (state.topic && !names.includes(state.topic)) state.topic = '';
    const options = ['<option value="">all topics</option>']
      .concat(names.map((name) => `<option value="${Cards.escapeHtml(name)}">${Cards.escapeHtml(name)}</option>`))
      .join('');
    if (options !== topicOptions) {
      els.topic.innerHTML = options;
      topicOptions = options;
    }
    els.topic.value = state.topic;
    els.topicBox.hidden = !names.length;
  }

  function renderTotems() {
    const cfg = state.config || {};
    const items = cfg.totems && cfg.totems.enabled && Array.isArray(cfg.totems.items) ? cfg.totems.items : [];
    els.totemGroup.hidden = !items.length;
    els.totemDrop.hidden = !items.length;
    els.totemGroup.innerHTML = items.map((item) => {
      const key = Cards.escapeHtml(String(item.key).toLowerCase());
      const label = Cards.escapeHtml(String(item.label || item.key).toLowerCase());
      const color = /^#[0-9a-f]{3,8}$/i.test(String(item.color || '')) ? ` style="--dot:${item.color}"` : '';
      return `<button type="button" class="pool-totem" data-totem="${key}" aria-pressed="false"><span class="pool-totem-dot"${color} aria-hidden="true"></span>${label}</button>`;
    }).join('');
  }

  function renderMessage(html, role) {
    els.pool.innerHTML = `<div class="pool-message"${role ? ` role="${role}"` : ''}>${html}</div>`;
  }

  function sortLabel() {
    const sort = Cards.POOL_SORTS.find((item) => item.key === state.sort);
    return sort ? sort.label : DEFAULT_SORT;
  }

  // announce says how many essays to a screen reader; a note (a new sort says
  // how the essays are sorted) goes on the end.
  function render(announce, note) {
    renderHead();
    if (!state.loaded) return;
    const shown = shownEssays();
    els.count.textContent = String(shown.length);
    if (announce) {
      const words = shown.length === 1 ? '1 essay' : `${shown.length} essays`;
      els.results.textContent = note ? `${words}, ${note}` : words;
    }
    if (!state.essays.length) {
      renderMessage('<p>no essays yet. a note in your essays folder shows up here.</p>');
      return;
    }
    if (!shown.length) {
      renderMessage('<p>nothing matches.</p><button type="button" class="pool-reset" data-action="reset">show every essay</button>');
      return;
    }
    state.columns = columnCount();
    const columns = Cards.distribute(shown, state.columns);
    els.pool.innerHTML = columns
      .map((column) => `<div class="pool-column">${column.map(cardHtml).join('')}</div>`)
      .join('');
    for (const id of state.busy) {
      const card = cardElement(id);
      if (card) card.setAttribute('aria-busy', 'true');
    }
    for (const id of state.up) cardElement(id)?.classList.add('is-up');
    roving.refresh();
  }

  function cardElement(id) {
    return Array.from(els.pool.querySelectorAll('.card')).find((card) => card.dataset.essayId === String(id)) || null;
  }

  // Swap one card in place, keeping the pool's order and the writer's focus.
  function replaceCard(oldId, essay, focusStar) {
    const card = cardElement(oldId);
    if (!card) return;
    const holder = document.createElement('div');
    holder.innerHTML = cardHtml(essay);
    const next = holder.firstElementChild;
    if (state.busy.has(String(essay.id))) next.setAttribute('aria-busy', 'true');
    if (state.up.has(String(oldId)) || state.up.has(String(essay.id))) next.classList.add('is-up');
    card.replaceWith(next);
    roving.refresh(String(essay.id));
    if (focusStar) {
      const target = next.querySelector('[data-action="star"]') || next.querySelector('.card-link');
      if (target) target.focus();
    }
    renderHead();
  }

  function starFailure(error, starred) {
    const lead = starred ? 'could not star it.' : 'could not take the star off.';
    if (error && error.kind === 'refused') return error.message;
    if (error && error.kind === 'file-changed') return `${lead} the note changed in obsidian, so reload the room and try again.`;
    if (error && error.kind === 'not-found') return `${lead} the note is no longer in your essays folder.`;
    if (error && error.kind === 'network') return `${lead} airdate is not answering. try again.`;
    return `${lead} the note was not saved. try again.`;
  }

  // The star flips at once; the server confirms or the card goes back.
  async function toggleStar(card) {
    const id = card.dataset.essayId;
    const index = state.essays.findIndex((essay) => String(essay.id) === id);
    if (index < 0 || state.busy.has(id)) return;
    const before = state.essays[index];
    const starred = before.status !== 'Writers Likey';
    const optimistic = {
      ...before,
      status: starred ? 'Writers Likey' : 'Writers Room',
      starred_at: starred ? new Date().toISOString() : '',
    };
    state.essays[index] = optimistic;
    state.errors.delete(id);
    state.busy.add(id);
    replaceCard(id, optimistic, true);
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(id)}/star`, { starred });
      state.busy.delete(id);
      const fresh = result && result.essay ? result.essay : optimistic;
      const at = state.essays.findIndex((essay) => String(essay.id) === id);
      if (at >= 0) state.essays[at] = fresh;
      replaceCard(id, fresh, Boolean(cardElement(id)?.contains(document.activeElement)));
      document.dispatchEvent(new CustomEvent('room:essay', {
        detail: { oldId: id, essay: fresh, source: 'pool' },
      }));
    } catch (error) {
      state.busy.delete(id);
      const at = state.essays.findIndex((essay) => String(essay.id) === id);
      if (at >= 0) state.essays[at] = before;
      state.errors.set(id, starFailure(error, starred));
      replaceCard(id, before, Boolean(cardElement(id)?.contains(document.activeElement)));
    }
  }

  // A row the board changed. Swap the card in place when it is still showing
  // and still belongs here; otherwise redraw, which drops a card that has
  // gone live or been parked, or adds one the board sent back.
  function takeEssay(detail) {
    const oldId = String(detail.oldId || '');
    const row = detail.essay || null;
    const at = state.essays.findIndex((essay) => String(essay.id) === oldId);
    const belongs = Boolean(row && ACTIVE.has(row.status));
    if (at >= 0 && belongs) state.essays[at] = row;
    else if (at >= 0) state.essays.splice(at, 1);
    else if (belongs) state.essays.push(row);
    else return;
    if (oldId && row && String(row.id) !== oldId && state.errors.has(oldId)) {
      state.errors.set(String(row.id), state.errors.get(oldId));
      state.errors.delete(oldId);
    }
    const card = cardElement(oldId);
    if (belongs && card && matches(row)) {
      replaceCard(oldId, row, card.contains(document.activeElement));
      return;
    }
    // A card that leaves (or one that arrives) moves the rest: let them glide.
    flight(() => render(false));
  }

  // ---- the umbrella: save for a rainy day (slice 6) ------------------------

  function parkFailure(error) {
    if (error && error.kind === 'refused') return error.message;
    if (error && error.kind === 'file-changed') return 'could not park it. the note changed in obsidian, so reload the room and try again.';
    if (error && error.kind === 'not-found') return 'could not park it. the note is no longer in your essays folder.';
    if (error && error.kind === 'network') return 'could not park it. airdate is not answering. try again.';
    return 'could not park it. the note was not saved. try again.';
  }

  function phaseWord(status) {
    return status === 'Writers Likey' ? 'writers likey' : 'writers room';
  }

  // Back to the room: the slip's undo, and the card's own "back to the room"
  // button when it is somehow still reachable. The essay is gone from the
  // pool by the time either fires, so it is re-added rather than replaced.
  async function backToRoom(id, fallbackTitle) {
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(id)}/back-to-room`, {});
      const fresh = result && result.row;
      if (fresh) {
        const at = state.essays.findIndex((row) => String(row.id) === String(fresh.id));
        if (at >= 0) state.essays[at] = fresh;
        else state.essays.push(fresh);
        render(false);
        document.dispatchEvent(new CustomEvent('room:essay', {
          detail: { oldId: id, essay: fresh, source: 'pool' },
        }));
      }
      Feedback.slip({
        tone: 'green',
        text: `${String((fresh && fresh.title) || fallbackTitle).toLowerCase()} is back in the room as ${phaseWord(fresh && fresh.status)}.`,
      });
      // The undo that brought it back is going with its slip (it may still be
      // fading out, focus and all). The card that returned takes focus, unless
      // the writer has already moved it somewhere else.
      const now = document.activeElement;
      if (fresh && (!now || now === document.body || !now.isConnected || now.closest('.slip'))) {
        const card = cardElement(fresh.id);
        const link = card ? card.querySelector('.card-link') : null;
        if (link) link.focus();
      }
    } catch (error) {
      Feedback.slip({ tone: 'red', text: `could not bring it back. ${error && error.message ? error.message : 'try again.'}` });
    }
  }

  // The card has left the pool once this succeeds, so the sentence goes on
  // the slip, with an undo - the umbrella's own promise (§15.2).
  async function parkEssay(id) {
    const index = state.essays.findIndex((essay) => String(essay.id) === id);
    if (index < 0 || state.busy.has(id)) return;
    const essay = state.essays[index];
    const card = cardElement(id);
    state.busy.add(id);
    if (card) card.setAttribute('aria-busy', 'true');
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(id)}/archive`, {});
      state.busy.delete(id);
      const fresh = result && result.row;
      const neighbourId = Keys.afterLeaving(shownEssays().map((row) => row.id), id);
      const at = state.essays.findIndex((row) => String(row.id) === id);
      if (at >= 0) state.essays.splice(at, 1);
      render(false);
      document.dispatchEvent(new CustomEvent('room:essay', {
        detail: { oldId: id, essay: fresh, source: 'pool' },
      }));
      const title = (fresh && fresh.title) || essay.title || 'the essay';
      // Undo with the id parking RETURNS, never the one we held. A note with no
      // uid gets one minted on this write, and the file moved into Archive/,
      // so the old path-based id no longer resolves - undo would 404. That is
      // most real notes, which is how this was found at review.
      const parkedId = String((result && result.new_id) || (fresh && fresh.id) || id);
      const slip = Feedback.slip({
        tone: 'green',
        text: `${String(title).toLowerCase()} saved for a rainy day.`,
        undo: () => backToRoom(parkedId, title),
      });
      Keys.focusAfterLeaving({ slip, neighbourId, card: cardElement, heading: $('pool-heading') });
    } catch (error) {
      state.busy.delete(id);
      if (card) card.removeAttribute('aria-busy');
      const host = cardElement(id)?.querySelector('.card-feedback');
      if (host) {
        Feedback.plaque(host, { tone: error && error.kind === 'refused' ? 'amber' : 'red', text: parkFailure(error) });
      }
    }
  }

  // ---- the needs-filing lane (slice 6) --------------------------------------

  async function suggestFiling(id) {
    if (state.busy.has(id)) return;
    state.busy.add(id);
    const card = cardElement(id);
    if (card) card.setAttribute('aria-busy', 'true');
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(id)}/intake-suggest`, {});
      state.intake[id] = result;
    } catch (error) {
      const host = cardElement(id)?.querySelector('.card-feedback');
      if (host) Feedback.plaque(host, { tone: 'red', text: 'could not suggest a topic. try again.' });
    } finally {
      state.busy.delete(id);
      const essay = state.essays.find((row) => String(row.id) === id);
      if (essay) replaceCard(id, essay, true);
      else if (card) card.removeAttribute('aria-busy');
    }
  }

  function filingFailure(error) {
    if (error && error.kind === 'refused') return error.message;
    if (error && error.kind === 'file-changed') return 'could not file it. the note changed in obsidian, so reload the room and try again.';
    if (error && error.kind === 'not-found') return 'could not file it. the note is no longer in your essays folder.';
    if (error && error.kind === 'network') return 'could not file it. airdate is not answering. try again.';
    return 'could not file it. try again.';
  }

  // Reuses /intake-suggest then /intake-apply as is; the writer can swap the
  // suggested topic for another before confirming.
  async function applyFiling(card) {
    const id = card.dataset.essayId;
    if (state.busy.has(id)) return;
    const suggestion = state.intake[id] || {};
    const select = card.querySelector('[data-role="file-category"]');
    const category = select ? select.value : (suggestion.category || '');
    if (!category && suggestion.category_mode !== 'off') {
      const host = card.querySelector('.card-feedback');
      if (host) Feedback.plaque(host, { tone: 'amber', text: 'pick a topic first.' });
      return;
    }
    state.busy.add(id);
    card.setAttribute('aria-busy', 'true');
    try {
      // No status: filing sorts the note into a topic and keeps its phase.
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(id)}/intake-apply`, {
        category, totem: suggestion.totem || '',
      });
      state.busy.delete(id);
      delete state.intake[id];
      const fresh = result && result.essay && result.essay.row;
      const at = state.essays.findIndex((row) => String(row.id) === id);
      if (fresh) {
        if (at >= 0) state.essays[at] = fresh;
        else state.essays.push(fresh);
        replaceCard(id, fresh, true);
        document.dispatchEvent(new CustomEvent('room:essay', {
          detail: { oldId: id, essay: fresh, source: 'pool' },
        }));
      } else {
        render(false);
      }
    } catch (error) {
      state.busy.delete(id);
      card.removeAttribute('aria-busy');
      const host = card.querySelector('.card-feedback');
      if (host) {
        Feedback.plaque(host, { tone: error && error.kind === 'refused' ? 'amber' : 'red', text: filingFailure(error) });
      }
    }
  }

  // ---- the grabber: a card's note goes up to the board and comes back ------

  function flight(change) {
    const Flight = window.RoomFlight;
    return Flight ? Flight.flip(els.pool, change) : change();
  }

  // The card's note goes up: the card shrinks into its post-it and the cards
  // below slide up into the space. Returns what a flight needs (where the
  // post-it was, and a copy of it to fly), or null when the card is not showing.
  // options.collapse: false hides it at once, for a drag, where the browser is
  // already carrying the note.
  function tuck(id, options) {
    const key = String(id);
    if (state.up.has(key)) return null;
    const card = cardElement(key);
    state.up.add(key);
    if (!card) return null;
    const postit = card.querySelector('.card-postit');
    const rect = (postit || card).getBoundingClientRect();
    const note = postit ? postit.cloneNode(true) : null;
    const Flight = window.RoomFlight;
    const hide = () => {
      card.classList.add('is-up');
      roving.refresh();
    };
    if (!Flight) {
      hide();
    } else if (options && options.collapse === false) {
      Flight.flip(els.pool, hide);
    } else {
      // The slide waits for the card to be gone, or the cards below would
      // move under it.
      Flight.collapse(card, rect).then(() => {
        if (state.up.has(key)) Flight.flip(els.pool, hide);
      });
    }
    return { rect, note };
  }

  // The note comes back (put back, or set and done): the card takes its place
  // again, the cards around it make room, and the card settles in.
  // options.from: a rectangle to fly the note back from (the board's ghost);
  // the card waits for it to land.
  function untuck(id, options) {
    const key = String(id);
    if (!state.up.delete(key)) return Promise.resolve();
    const card = cardElement(key);
    if (!card) return Promise.resolve();
    const Flight = window.RoomFlight;
    const show = () => {
      card.classList.remove('is-up');
      roving.refresh();
    };
    if (!Flight) {
      show();
      return Promise.resolve();
    }
    // Hidden (opacity 0) but laid out, so the cards around it make room now.
    card.style.opacity = '0';
    Flight.flip(els.pool, show);
    const postit = card.querySelector('.card-postit');
    const from = options && options.from;
    if (from && postit && !Flight.reducedMotion()) {
      return Flight.fly(postit, from, postit.getBoundingClientRect(), { turnFrom: 0, turnTo: -7 })
        .then(() => Flight.arrive(card));
    }
    return Flight.arrive(card);
  }

  function setPhase(phase) {
    state.phase = (phase in PHASE_STATUS || phase === NEEDS_FILING) ? phase : 'all';
    render(true);
  }

  function bind() {
    els.pool.addEventListener('click', (event) => {
      const star = event.target.closest('[data-action="star"]');
      if (star) {
        event.preventDefault();
        const card = star.closest('.card');
        if (card) toggleStar(card);
        return;
      }
      const park = event.target.closest('[data-action="park"]');
      if (park) {
        event.preventDefault();
        const card = park.closest('.card');
        if (card) parkEssay(card.dataset.essayId);
        return;
      }
      const suggest = event.target.closest('[data-action="intake-suggest"]');
      if (suggest) {
        event.preventDefault();
        const card = suggest.closest('.card');
        if (card) suggestFiling(card.dataset.essayId);
        return;
      }
      const apply = event.target.closest('[data-action="intake-apply"]');
      if (apply) {
        event.preventDefault();
        const card = apply.closest('.card');
        if (card) applyFiling(card);
        return;
      }
      if (event.target.closest('[data-action="reset"]')) {
        // Every filter, not the sort: the order is how the writer reads the
        // pool, not something that hides an essay.
        state.phase = 'all';
        state.totems.clear();
        state.attention = false;
        state.topic = '';
        state.query = '';
        els.search.value = '';
        render(true);
        els.search.focus();
      }
    });

    for (const button of els.phaseButtons) {
      button.addEventListener('click', () => setPhase(button.dataset.phase));
    }
    // The star pill is the writers likey filter under another name: pressing
    // it again goes back to all.
    els.starPill.addEventListener('click', () => {
      setPhase(state.phase === 'writers-likey' ? 'all' : 'writers-likey');
    });
    if (els.filingPill) {
      els.filingPill.addEventListener('click', () => {
        setPhase(state.phase === NEEDS_FILING ? 'all' : NEEDS_FILING);
      });
    }
    if (els.attentionPill) {
      els.attentionPill.addEventListener('click', () => {
        state.attention = !state.attention;
        render(true);
      });
    }
    if (els.topic) {
      els.topic.addEventListener('change', () => {
        state.topic = els.topic.value;
        render(true);
      });
    }
    if (els.sort) {
      els.sort.addEventListener('change', () => {
        state.sort = validSort(els.sort.value);
        els.sort.value = state.sort;
        render(true, `sorted by ${sortLabel()}`);
      });
    }
    els.totemGroup.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-totem]');
      if (!button) return;
      const key = button.dataset.totem;
      if (state.totems.has(key)) state.totems.delete(key);
      else state.totems.add(key);
      render(true);
    });
    els.search.addEventListener('input', () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        state.query = els.search.value.trim();
        render(true);
      }, 120);
    });

    let resizeTimer = 0;
    window.addEventListener('resize', () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        if (state.loaded && columnCount() !== state.columns) render(false);
      }, 150);
    });
  }

  async function load() {
    els.pool.setAttribute('aria-busy', 'true');
    renderMessage('<p>loading the essays…</p>', 'status');
    try {
      const [status, catalog] = await Promise.all([
        Api.getJson('/api/app/status'),
        Api.getJson('/api/essays'),
      ]);
      state.config = (status && status.config) || {};
      state.essays = Array.isArray(catalog && catalog.essays) ? catalog.essays : [];
      state.loaded = true;
      renderTotems();
      render(false);
      // The home tour waits for this: four of its stops are on the cards.
      document.dispatchEvent(new CustomEvent('room:pool-ready', { detail: { count: state.essays.length } }));
    } catch (error) {
      if (error && error.kind === 'setup') {
        renderMessage('<p>finish setup first: choose your vault and essays folder in <a href="/airdate#settings">settings</a>.</p>', 'alert');
      } else {
        renderMessage(`<p>could not load the essays. ${Cards.escapeHtml(error && error.message ? error.message : '')}</p>`, 'alert');
      }
    } finally {
      els.pool.removeAttribute('aria-busy');
    }
  }

  function start() {
    els.pool = $('pool');
    els.count = $('pool-count');
    els.results = $('pool-results');
    els.search = $('pool-search');
    els.phaseButtons = Array.from(document.querySelectorAll('[data-phase]'));
    els.starPill = $('pool-star');
    els.likeyCount = $('pool-likey-count');
    els.filingPill = $('pool-filing');
    els.filingCount = $('pool-filing-count');
    els.attentionPill = $('pool-attention');
    els.attentionCount = $('pool-attention-count');
    els.topicBox = $('pool-topic-box');
    els.topic = $('pool-topic');
    els.sort = $('pool-sort');
    els.totemGroup = $('pool-totems');
    els.totemDrop = $('pool-totem-drop');
    els.showLabel = $('pool-show-label');
    els.totemLabel = $('pool-totem-label');
    if (!els.pool || !Cards || !Api || !Keys || !Feedback) return;
    roving = Keys.createRoving({
      container: els.pool,
      itemSelector: '.card:not(.is-up)',
      primarySelector: '.card-link',
      columnSelector: '.pool-column',
    });
    bind();
    bindDrops();
    document.addEventListener('room:essay', (event) => {
      const detail = event.detail || {};
      if (detail.source === 'pool' || !state.loaded) return;
      takeEssay(detail);
    });
    document.addEventListener('room:reload', () => { load(); });
    window.RoomPool = {
      onKey: (handler) => roving.onKey(handler),
      card: (id) => cardElement(id),
      feedbackHost: (id) => cardElement(id)?.querySelector('.card-feedback') || null,
      tuck,
      untuck,
      isLoaded: () => state.loaded,
    };
    load();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}());
