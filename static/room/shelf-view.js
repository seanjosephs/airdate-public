// The shelf: drawing it and the round trips (build spec §9.2, §15.2).
//
// The rules (grouping by month, filtering, the row) live in shelf.js and are
// tested there. This file reads scope=shelf, wires search/totems/topic/year,
// and draws what shelf.js returns.
(function startShelfView() {
  const Shelf = window.AirdateShelf;
  const Cards = window.AirdateCards;
  const Api = window.RoomApi;

  const state = {
    essays: [],
    config: null,
    query: '',
    totems: new Set(),
    topic: '',
    year: '',
    loaded: false,
  };

  const els = {};
  let searchTimer = 0;

  function $(id) { return document.getElementById(id); }

  function isRoute() {
    return window.location.hash === '#shelf';
  }

  function totemsMap() {
    const cfg = state.config || {};
    const map = {};
    for (const item of (cfg.totems && Array.isArray(cfg.totems.items) ? cfg.totems.items : [])) {
      if (item && item.key) map[String(item.key).toLowerCase()] = item;
    }
    return map;
  }

  function presets() {
    const cfg = state.config || {};
    return Array.isArray(cfg.tag_presets) ? cfg.tag_presets : [];
  }

  function filters() {
    return { query: state.query, totems: state.totems, topic: state.topic, year: state.year };
  }

  function renderTotemButtons() {
    const items = Object.values(totemsMap());
    els.totems.hidden = !items.length;
    els.totems.innerHTML = items.map((item) => {
      const key = Cards.escapeHtml(String(item.key).toLowerCase());
      const label = String(item.label || item.key).toLowerCase();
      const pressed = state.totems.has(String(item.key).toLowerCase());
      const art = item.image
        ? `<img src="${Cards.escapeHtml(item.image)}" alt="" width="28" height="28" decoding="async" loading="lazy">`
        : '';
      return `<button type="button" class="shelf-totem" data-totem="${key}" aria-pressed="${pressed}" aria-label="filter by ${Cards.escapeHtml(label)}" title="${Cards.escapeHtml(label)}">${art}</button>`;
    }).join('');
  }

  function renderSelectOptions(select, values, allLabel) {
    const current = select.value;
    select.innerHTML = `<option value="">${allLabel}</option>`
      + values.map((value) => `<option value="${Cards.escapeHtml(value)}">${Cards.escapeHtml(value)}</option>`).join('');
    if (values.includes(current)) select.value = current;
  }

  function render(announce) {
    const shown = state.essays.filter((essay) => Shelf.matchesFilters(essay, filters(), presets()));
    els.count.textContent = String(shown.length);
    if (announce) els.results.textContent = shown.length === 1 ? '1 live essay' : `${shown.length} live essays`;
    if (!state.loaded) return;

    if (!state.essays.length) {
      els.body.innerHTML = '<p class="shelf-empty">nothing published yet. the shelf fills as essays go live.</p>';
      return;
    }
    if (!shown.length) {
      els.body.innerHTML = '<p class="shelf-empty">nothing matches.</p>';
      return;
    }

    const ctx = { totems: totemsMap(), presets: presets() };
    const { onShelf, archive } = Shelf.splitByMonth(shown, new Date());
    const onLabel = Shelf.monthLabel(Shelf.todayKey(new Date())) || 'this month';
    let html = '<section class="shelf-group">'
      + Shelf.monthHeadingMarkup(`on the shelf · ${onLabel}`, 'live this month. these move to the archive on the first.')
      + (onShelf.length
        ? Shelf.tableMarkup(`on the shelf, ${onLabel}`, onShelf, ctx)
        : '<p class="shelf-empty shelf-empty-inline">nothing live this month.</p>')
      + '</section>';

    if (archive.length) {
      html += '<h2 class="shelf-archive-heading">the archive <span>everything from earlier months, newest first. search covers both.</span></h2>';
      html += archive.map((group) => (
        '<section class="shelf-group">'
        + Shelf.monthHeadingMarkup(group.label, `${group.essays.length} ${group.essays.length === 1 ? 'essay' : 'essays'}`)
        + Shelf.tableMarkup(group.label, group.essays, ctx)
        + '</section>'
      )).join('');
    }
    els.body.innerHTML = html;
  }

  function bind() {
    els.search.addEventListener('input', () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        state.query = els.search.value.trim();
        render(true);
      }, 120);
    });
    els.totems.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-totem]');
      if (!button) return;
      const key = button.dataset.totem;
      if (state.totems.has(key)) state.totems.delete(key);
      else state.totems.add(key);
      renderTotemButtons();
      render(true);
    });
    els.topic.addEventListener('change', () => {
      state.topic = els.topic.value;
      render(true);
    });
    els.year.addEventListener('change', () => {
      state.year = els.year.value;
      render(true);
    });
  }

  async function load() {
    els.body.setAttribute('aria-busy', 'true');
    els.body.innerHTML = '<p class="shelf-empty">loading the shelf…</p>';
    try {
      const [status, catalog] = await Promise.all([
        Api.getJson('/api/app/status'),
        Api.getJson('/api/essays?scope=shelf'),
      ]);
      state.config = (status && status.config) || {};
      state.essays = Array.isArray(catalog && catalog.essays) ? catalog.essays : [];
      state.loaded = true;
      renderTotemButtons();
      renderSelectOptions(els.topic, Shelf.distinctTopics(state.essays, presets()), 'all topics');
      renderSelectOptions(els.year, Shelf.distinctYears(state.essays), 'all years');
      render(false);
    } catch (error) {
      els.body.innerHTML = `<p class="shelf-empty">could not load the shelf. ${Cards.escapeHtml(error && error.message ? error.message : '')}</p>`;
    } finally {
      els.body.removeAttribute('aria-busy');
    }
  }

  function applyRoute() {
    const active = isRoute();
    els.view.hidden = !active;
    if (active && !state.loaded) load();
  }

  function takeEssay(detail) {
    const row = detail.essay || null;
    const oldId = String(detail.oldId || '');
    const at = state.essays.findIndex((essay) => String(essay.id) === oldId);
    const belongs = Boolean(row && row.status === 'Live');
    if (at >= 0 && belongs) state.essays[at] = row;
    else if (at >= 0) state.essays.splice(at, 1);
    else if (belongs) state.essays.push(row);
    else return;
    render(false);
  }

  function start() {
    els.view = $('shelf-view');
    els.body = $('shelf-body');
    els.count = $('shelf-count');
    els.results = $('shelf-results');
    els.search = $('shelf-search');
    els.totems = $('shelf-totems');
    els.topic = $('shelf-topic');
    els.year = $('shelf-year');
    if (!els.view || !Shelf || !Cards || !Api) return;
    bind();
    window.addEventListener('hashchange', applyRoute);
    document.addEventListener('room:essay', (event) => {
      const detail = event.detail || {};
      if (detail.source === 'shelf' || !state.loaded) return;
      takeEssay(detail);
    });
    applyRoute();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}());
