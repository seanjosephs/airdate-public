// Rainy day (slice 6): the parking spot the umbrella points at.
//
// Cards, not a table - the script face (paper age, jab, tool) is the
// information, same primitive as the pool at room size, three across.
// "Archived" on disk; "rainy day" is the only word the interface uses for it.
// Loads scope=archived (classify_essay already buckets a parked essay there)
// and listens for room:essay so a park/unpark anywhere else in the room
// (the pool's umbrella, the editor's) keeps this list honest without a
// reload, the same contract pool.js already keeps for the active list.
(function startRainyDay() {
  const Cards = window.AirdateCards;
  const Api = window.RoomApi;
  const Feedback = window.RoomFeedback;
  const Keys = window.RoomKeys;

  const state = {
    essays: [],
    config: null,
    query: '',
    sort: 'longest',
    columns: 0,
    busy: new Set(),
    loaded: false,
  };

  const els = {};
  let searchTimer = 0;

  function $(id) { return document.getElementById(id); }

  function isRoute() {
    return window.location.hash === '#rainy-day';
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
      presets: Array.isArray(cfg.tag_presets) ? cfg.tag_presets : [],
    };
  }

  function matches(essay) {
    const terms = state.query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return true;
    const hay = `${essay.search_blob || ''} ${String(essay.excerpt || '').toLowerCase()}`;
    return terms.every((term) => hay.includes(term));
  }

  function columnCount() {
    const width = els.columns.clientWidth || window.innerWidth;
    if (width >= 900) return 3;
    if (width >= 600) return 2;
    return 1;
  }

  function cardElement(id) {
    return Array.from(els.columns.querySelectorAll('.card')).find((card) => card.dataset.essayId === String(id)) || null;
  }

  function renderEmptyState() {
    els.columns.innerHTML = '<div class="rainy-empty-wrap">'
      + '<div class="rainy-empty" role="status">'
      + '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a9 9 0 0 1 9 9H3a9 9 0 0 1 9-9z"/><path d="M12 12v6a2 2 0 0 0 4 0M12 2v1"/></svg>'
      + '<p class="rainy-empty-title">no rain today.</p>'
      + '<p class="rainy-empty-sub">the umbrella on any script parks it here. it keeps its stamp and its star, and it comes back with one press.</p>'
      + '</div></div>';
  }

  function render(announce) {
    els.count.textContent = String(state.essays.length);
    if (!state.loaded) return;
    const shown = Cards.rainyDaySort(state.essays.filter(matches), state.sort);
    if (announce) els.results.textContent = shown.length === 1 ? '1 parked essay' : `${shown.length} parked essays`;
    if (!shown.length) {
      renderEmptyState();
      return;
    }
    state.columns = columnCount();
    const columns = Cards.distribute(shown, state.columns);
    els.columns.innerHTML = columns
      .map((column) => `<div class="rainy-column">${column.map((essay) => Cards.cardMarkup(essay, context(essay))).join('')}</div>`)
      .join('');
    for (const id of state.busy) {
      const card = cardElement(id);
      if (card) card.setAttribute('aria-busy', 'true');
    }
  }

  function backFailure(error) {
    if (error && error.kind === 'refused') return error.message;
    return `could not bring it back. ${(error && error.message) || 'try again.'}`;
  }

  function phaseWord(status) {
    return status === 'Writers Likey' ? 'writers likey' : 'writers room';
  }

  async function backToRoom(id) {
    if (state.busy.has(id)) return;
    const card = cardElement(id);
    state.busy.add(id);
    if (card) card.setAttribute('aria-busy', 'true');
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(id)}/back-to-room`, {});
      state.busy.delete(id);
      const row = result && result.row;
      const neighbourId = Keys.afterLeaving(Cards.rainyDaySort(state.essays.filter(matches), state.sort).map((essay) => essay.id), id);
      const at = state.essays.findIndex((essay) => String(essay.id) === id);
      if (at >= 0) state.essays.splice(at, 1);
      render(false);
      document.dispatchEvent(new CustomEvent('room:essay', {
        detail: { oldId: id, essay: row, source: 'rainy-day' },
      }));
      const slip = Feedback.slip({ tone: 'green', text: `${String((row && row.title) || 'the essay').toLowerCase()} is back in the room as ${phaseWord(row && row.status)}.` });
      Keys.focusAfterLeaving({ slip, neighbourId, card: cardElement, heading: $('rainy-heading') });
    } catch (error) {
      state.busy.delete(id);
      if (card) card.removeAttribute('aria-busy');
      Feedback.slip({ tone: 'red', text: backFailure(error) });
    }
  }

  function bind() {
    els.columns.addEventListener('click', (event) => {
      const button = event.target.closest('[data-action="unpark"]');
      if (!button) return;
      event.preventDefault();
      const card = button.closest('.card');
      if (card) backToRoom(card.dataset.essayId);
    });
    els.search.addEventListener('input', () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        state.query = els.search.value.trim();
        render(true);
      }, 120);
    });
    els.sort.addEventListener('change', () => {
      state.sort = els.sort.value;
      render(true);
    });
    let resizeTimer = 0;
    window.addEventListener('resize', () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        if (state.loaded && isRoute() && columnCount() !== state.columns) render(false);
      }, 150);
    });
  }

  async function load() {
    els.columns.setAttribute('aria-busy', 'true');
    els.columns.innerHTML = '<div class="rainy-empty-wrap"><p class="rainy-loading">loading the rain…</p></div>';
    try {
      const [status, catalog] = await Promise.all([
        Api.appStatus(),
        Api.getJson('/api/essays?scope=archived'),
      ]);
      state.config = (status && status.config) || {};
      state.essays = Array.isArray(catalog && catalog.essays) ? catalog.essays : [];
      state.loaded = true;
      render(false);
    } catch (error) {
      els.columns.innerHTML = `<div class="rainy-empty-wrap"><p class="rainy-loading">could not load rainy day. ${Cards.escapeHtml(error && error.message ? error.message : '')}</p></div>`;
    } finally {
      els.columns.removeAttribute('aria-busy');
    }
  }

  function applyRoute() {
    const active = isRoute();
    els.view.hidden = !active;
    if (active && !state.loaded) load();
  }

  // A park/unpark anywhere else in the room (the pool's umbrella, the
  // editor's). Only rows this screen did not originate itself matter here.
  function takeEssay(detail) {
    const oldId = String(detail.oldId || '');
    const row = detail.essay || null;
    const at = state.essays.findIndex((essay) => String(essay.id) === oldId);
    const belongs = Boolean(row && row.status === 'Archived');
    if (at >= 0 && belongs) state.essays[at] = row;
    else if (at >= 0) state.essays.splice(at, 1);
    else if (belongs) state.essays.push(row);
    else return;
    render(false);
  }

  function start() {
    els.view = $('rainy-view');
    els.columns = $('rainy-columns');
    els.count = $('rainy-count');
    els.results = $('rainy-results');
    els.search = $('rainy-search');
    els.sort = $('rainy-sort');
    if (!els.view || !Cards || !Api || !Feedback || !Keys) return;
    bind();
    window.addEventListener('hashchange', applyRoute);
    document.addEventListener('room:essay', (event) => {
      const detail = event.detail || {};
      if (detail.source === 'rainy-day' || !state.loaded) return;
      takeEssay(detail);
    });
    applyRoute();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}());
