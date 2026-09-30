// The pool: every essay in the room as a script card, three flex columns.
//
// Loads the same /api/essays the old page uses (default scope, active), plus
// the writer's config from /api/app/status for totems, tag presets and the
// red pen. Owns search, the filters, the sort and the star round trip.
//
// The board (board-view.js) changes essays too. Every changed row travels as
// one "room:essay" event on document, { oldId, essay, source }, and both sides
// take it; a row that has left the active list (live, rainy day) leaves the
// pool. window.RoomPool is what placing mode (placing.js) builds on.
(function startPool() {
  const Cards = window.AirdateCards;
  const Api = window.RoomApi;
  const Keys = window.RoomKeys;

  const PHASE_STATUS = {
    'writers-room': 'Writers Room',
    'writers-likey': 'Writers Likey',
    'ready-for-air': 'Ready for Air',
  };

  const state = {
    essays: [],
    config: null,
    phase: 'all',
    totems: new Set(),
    query: '',
    columns: 0,
    busy: new Set(),
    errors: new Map(),
    loaded: false,
    // The card whose note is up on the board in placing mode.
    placing: '',
  };
  // Phases that live in the default, active list. Anything else is the
  // shelf's or rainy day's.
  const ACTIVE = new Set(['Writers Room', 'Writers Likey', 'Ready for Air']);

  const els = {};
  let roving = null;
  let searchTimer = 0;

  function $(id) { return document.getElementById(id); }

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
      presets: Array.isArray(cfg.tag_presets) ? cfg.tag_presets : [],
      error: essay ? state.errors.get(String(essay.id)) || '' : '',
      placing: Boolean(essay && state.placing && state.placing === String(essay.id)),
    };
  }

  function matches(essay) {
    if (state.phase !== 'all' && essay.status !== PHASE_STATUS[state.phase]) return false;
    if (state.totems.size && !state.totems.has(String(essay.totem_raw || '').trim().toLowerCase())) return false;
    const terms = state.query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return true;
    const hay = `${essay.search_blob || ''} ${String(essay.excerpt || '').toLowerCase()}`;
    return terms.every((term) => hay.includes(term));
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
    for (const button of els.totemGroup.querySelectorAll('button[data-totem]')) {
      button.setAttribute('aria-pressed', String(state.totems.has(button.dataset.totem)));
    }
  }

  function renderTotems() {
    const cfg = state.config || {};
    const items = cfg.totems && cfg.totems.enabled && Array.isArray(cfg.totems.items) ? cfg.totems.items : [];
    els.totemGroup.hidden = !items.length;
    els.totemDivider.hidden = !items.length;
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

  function render(announce) {
    renderHead();
    if (!state.loaded) return;
    const shown = Cards.closestToAirSort(state.essays.filter(matches));
    els.count.textContent = String(shown.length);
    if (announce) els.results.textContent = shown.length === 1 ? '1 essay' : `${shown.length} essays`;
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
    render(false);
  }

  function setPlacing(id) {
    const before = state.placing;
    state.placing = id ? String(id) : '';
    for (const key of new Set([before, state.placing])) {
      if (!key) continue;
      const essay = state.essays.find((row) => String(row.id) === key);
      if (essay && cardElement(key)) replaceCard(key, essay, false);
    }
  }

  function setPhase(phase) {
    state.phase = phase in PHASE_STATUS ? phase : 'all';
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
      if (event.target.closest('[data-action="reset"]')) {
        state.phase = 'all';
        state.totems.clear();
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
    } catch (error) {
      if (error && error.kind === 'setup') {
        renderMessage('<p>finish setup first: choose your vault and essays folder in <a href="/airdate">settings</a>.</p>', 'alert');
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
    els.totemGroup = $('pool-totems');
    els.totemDivider = $('pool-totem-divider');
    if (!els.pool || !Cards || !Api || !Keys) return;
    roving = Keys.createRoving({
      container: els.pool,
      itemSelector: '.card',
      primarySelector: '.card-link',
      columnSelector: '.pool-column',
    });
    bind();
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
      setPlacing,
    };
    load();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}());
