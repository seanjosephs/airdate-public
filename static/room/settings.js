// The settings view in the room, plus the bootstrap that opens the first-run
// wizard. One page, written through the /api/settings* routes; nothing saves
// until "save settings" is pressed. The wizard itself is
// static/airdate-wizard.js, driving the wizard markup in room.html.
(function startRoomSettings() {
  const esc = window.AirdateCards ? window.AirdateCards.escapeHtml : (value) => String(value ?? '');
  const BOARD_COLORS = ['canary', 'blue', 'orange', 'pink', 'green'];

  async function getJson(path) {
    const response = await fetch(path);
    const payload = await response.json();
    if (!response.ok) {
      const error = new Error(payload.message || payload.error || 'request failed.');
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  async function postJson(path, body) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const payload = await response.json();
    if (!response.ok) {
      const error = new Error(payload.message || payload.error || 'request failed.');
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  const els = {};
  function $(id) { return document.getElementById(id); }

  // ---- the route: the hash picks one of four views, hides the board and the
  // pool for all but "essays", and owns aria-current on the sidebar nav for
  // every route (shelf.js and rainy-day.js each still own their own view's
  // hidden state and lazy load, the same way this module already did). --

  // The hash names the view; cards.js holds the table so node can test it.
  const Cards = window.AirdateCards;

  function currentRoute() {
    return Cards.routeOfHash(window.location.hash);
  }

  function applyRoute() {
    const route = currentRoute();
    const settings = route === 'settings';
    document.body.classList.toggle('route-settings', settings);
    document.body.classList.toggle('route-shelf', route === 'shelf');
    document.body.classList.toggle('route-rainy-day', route === 'rainy-day');
    if (els.view) els.view.hidden = !settings;
    for (const link of document.querySelectorAll('.room-nav a')) {
      const linkRoute = Cards.routeOfHref(link.getAttribute('href'));
      if (linkRoute === route) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    if (settings && !els.loaded) load();
  }

  // ---- rendering ------------------------------------------------------------

  function setMessage(text, kind) {
    if (!els.messages) return;
    els.messages.textContent = text || '';
    els.messages.dataset.kind = kind || '';
  }

  function setSaved(text, kind) {
    if (!els.saved) return;
    els.saved.textContent = text || '';
    els.saved.dataset.kind = kind || '';
  }

  function renderBoardColors(selected) {
    els.boardColors.innerHTML = BOARD_COLORS.map((key) => (
      `<button type="button" class="set-color pad-${key}" data-color="${key}" role="radio" aria-checked="${key === selected}" aria-label="${key}"></button>`
    )).join('');
  }

  function selectedBoardColor() {
    const pressed = els.boardColors.querySelector('[aria-checked="true"]');
    return pressed ? pressed.dataset.color : 'canary';
  }

  els._bindBoardColors = function bindBoardColors() {
    els.boardColors.addEventListener('click', (event) => {
      const button = event.target.closest('[data-color]');
      if (!button) return;
      for (const node of els.boardColors.querySelectorAll('[data-color]')) node.setAttribute('aria-checked', String(node === button));
    });
  };

  function renderTotems(slots, enabled) {
    els.totemList.classList.toggle('is-off', !enabled);
    els.totemList.innerHTML = (slots || []).map((slot) => `
      <div class="set-totem-row" data-totem-key="${esc(slot.key)}">
        <img src="${esc(slot.image)}" alt="" width="40" height="40">
        <input class="set-totem-label" value="${esc(slot.label)}" aria-label="totem name" autocomplete="off">
        <input class="set-totem-color" type="color" value="${esc(slot.color || '#8092b0')}" aria-label="totem color">
        <input class="set-totem-image" value="${esc(slot.image_path || '')}" placeholder="a path in your vault, or blank for airdate's own art" aria-label="totem art: a path in your vault" autocomplete="off" spellcheck="false">
      </div>`).join('');
  }

  function readTotems() {
    return [...els.totemList.querySelectorAll('.set-totem-row')].map((row) => ({
      key: row.dataset.totemKey,
      label: row.querySelector('.set-totem-label').value.trim(),
      color: row.querySelector('.set-totem-color').value,
      // The writer's own art: a path in the vault. Blank falls back to the art
      // airdate ships, not to a placeholder.
      image: row.querySelector('.set-totem-image').value.trim(),
    }));
  }

  // When the vault was last indexed, in the writer's own time zone.
  function indexedMoment(appStatus) {
    const Dates = window.RoomDates;
    return Dates ? Dates.formatMoment(appStatus.scanned_at) : '';
  }

  function fillUnderTheHood(appStatus) {
    const expose = Boolean(appStatus.local_paths_visible);
    $('set-uh-url').textContent = appStatus.app_url || '';
    $('set-uh-port').textContent = String(appStatus.port || '');
    $('set-uh-auth').textContent = appStatus.auth_required ? 'required' : 'off on loopback';
    $('set-uh-paths').textContent = expose ? 'visible' : 'hidden';
    $('set-uh-vault').textContent = expose ? (appStatus.obsidian_dir || '') : 'hidden';
    $('set-uh-data').textContent = expose ? (appStatus.data_dir || '') : 'hidden';
    $('set-uh-drafts').textContent = expose ? (appStatus.drafts_dir || '') : 'hidden';
    const indexedAt = indexedMoment(appStatus);
    const scanned = indexedAt ? `, ${indexedAt}` : '';
    $('set-uh-index').textContent = `${appStatus.essay_count || 0} essays${scanned}`;
  }

  function fillConnection(appStatus) {
    const substack = appStatus.substack || {};
    const connected = Boolean(substack.connected || substack.session_ok);
    els.connectRow.dataset.connected = String(connected);
    els.connectText.textContent = connected
      ? `substack connected${substack.publication ? ` · ${substack.publication}` : ''}`
      : 'substack not connected';
  }

  function fillForm(appStatus) {
    const setup = appStatus.setup || {};
    const config = appStatus.config || {};
    const values = setup.form || {};
    const pathsEditable = Boolean(setup.paths_editable);
    els.vaultPathField.hidden = !pathsEditable;
    els.vaultPathNote.hidden = pathsEditable;
    els.vaultPath.value = values.vault_path || '';
    els.essaysFolder.value = values.essays_folder || '';
    els.vaultName.value = values.vault_name || '';
    els.publication.value = values.publication || '';
    els.publicationName.value = values.publication_name || '';
    els.categoryMode.value = values.category_mode || 'folders';
    els.publishDay.value = config.publish_day || '';

    const pad = values.board_default_pad || 'sticky';
    const padInput = els.view.querySelector(`input[name="set-board-pad"][value="${CSS.escape(pad)}"]`);
    if (padInput) padInput.checked = true;
    renderBoardColors(values.board_default_color || 'canary');

    els.redPenEnabled.checked = values.red_pen_enabled !== false;
    els.redPenLines.value = values.red_pen_lines || '';
    els.paperFresh.checked = Boolean(values.paper_fresh_on_promotion);

    els.totemsEnabled.checked = Boolean(config.totems && config.totems.enabled);
    renderTotems((config.totems && config.totems.slots) || [], els.totemsEnabled.checked);

    window.AirdatePresets && window.AirdatePresets.mount(els.tagPresets, config.tag_presets || []);

    fillUnderTheHood(appStatus);
    fillConnection(appStatus);

    const vaultStatus = [setup.vault_message, setup.essays_message].filter(Boolean).join(' ')
      || (setup.vault_ok && setup.essays_ok ? `vault found. ${appStatus.essay_count || 0} essays, indexed ${indexedMoment(appStatus) || 'just now'}.` : '');
    els.vaultStatus.textContent = vaultStatus;
    els.vaultStatus.dataset.kind = setup.vault_ok && setup.essays_ok ? 'good' : 'bad';

    const problems = [setup.load_error, ...(setup.errors || [])].filter(Boolean);
    if (problems.length) setMessage(problems.join('\n'), 'bad');
    else setMessage('', '');
  }

  function formPayload() {
    const padInput = els.view.querySelector('input[name="set-board-pad"]:checked');
    return {
      essays_folder: els.essaysFolder.value.trim(),
      vault_name: els.vaultName.value.trim(),
      publication: els.publication.value.trim(),
      publication_name: els.publicationName.value.trim(),
      category_mode: els.categoryMode.value,
      publish_day: els.publishDay.value,
      totems_enabled: els.totemsEnabled.checked,
      totems: readTotems(),
      tag_presets: window.AirdatePresets ? window.AirdatePresets.read(els.tagPresets) : undefined,
      board_default_pad: padInput ? padInput.value : 'sticky',
      board_default_color: selectedBoardColor(),
      red_pen_enabled: els.redPenEnabled.checked,
      red_pen_lines: els.redPenLines.value,
      paper_fresh_on_promotion: els.paperFresh.checked,
      ...(els.vaultPathField.hidden ? {} : { vault_path: els.vaultPath.value.trim() }),
    };
  }

  async function save() {
    setSaved('', '');
    setMessage('saving…', '');
    let result;
    try {
      result = await postJson('/api/settings', formPayload());
    } catch (error) {
      setMessage(error.payload && error.payload.errors ? error.payload.errors.join('\n') : error.message, 'bad');
      return;
    }
    if (result.errors && result.errors.length) {
      setMessage(result.errors.join('\n'), 'bad');
      return;
    }
    await load();
    setSaved('saved in config.json', 'good');
    setMessage('', '');
  }

  // ---- reset the writers room, behind an in-page confirmation --------------

  function bindReset() {
    els.resetButton.addEventListener('click', () => {
      els.resetConfirm.hidden = false;
      els.resetConfirmYes.focus();
    });
    els.resetConfirmNo.addEventListener('click', () => {
      els.resetConfirm.hidden = true;
      els.resetButton.focus();
    });
    els.resetConfirmYes.addEventListener('click', async () => {
      els.resetConfirm.hidden = true;
      try {
        const result = await postJson('/api/settings/reset-room', { confirm: true });
        setSaved(`reset. ${result.cleared || 0} essays start fresh.`, 'good');
      } catch (error) {
        setMessage(error.message, 'bad');
      }
      els.resetButton.focus();
    });
  }

  // The connect button: asks the connector to open the substack sign-in
  // window in obsidian. Tests never press it; it would pair for real.
  function bindConnect() {
    els.connectButton.addEventListener('click', async () => {
      setMessage('opening the secure substack sign-in window…', '');
      try {
        const result = await postJson('/api/substack/connect', {});
        setMessage(result.pending
          ? 'finish signing in to substack in the obsidian window, then come back here.'
          : 'substack connected through obsidian.', result.pending ? '' : 'good');
      } catch (error) {
        setMessage(`could not open obsidian sign-in: ${error.message}`, 'bad');
      }
      await load();
    });
  }

  // Settings shows the connection and the index as they are now, and every
  // save, connect and reload ends here, so it always reads fresh; the views
  // that load after it share what it read.
  async function load() {
    els.loaded = true;
    try {
      const appStatus = await window.RoomApi.appStatus({ fresh: true });
      fillForm(appStatus);
    } catch (error) {
      setMessage(`could not load settings: ${error.message}`, 'bad');
    }
  }

  function bind() {
    els.view = $('settings-view');
    els.saved = $('set-saved');
    els.messages = $('set-messages');
    els.saveButton = $('set-save');
    els.vaultPathField = $('set-vault-path-field');
    els.vaultPathNote = $('set-vault-path-note');
    els.vaultPath = $('set-vault-path');
    els.essaysFolder = $('set-essays-folder');
    els.vaultName = $('set-vault-name');
    els.vaultStatus = $('set-vault-status');
    els.publication = $('set-publication');
    els.publicationName = $('set-publication-name');
    els.connectRow = $('set-connect-row');
    els.connectText = $('set-connect-text');
    els.connectButton = $('set-connect');
    els.categoryMode = $('set-category-mode');
    els.boardColors = $('set-board-colors');
    els.publishDay = $('set-publish-day');
    els.redPenEnabled = $('set-red-pen-enabled');
    els.redPenLines = $('set-red-pen-lines');
    els.paperFresh = $('set-paper-fresh');
    els.resetButton = $('set-reset-room');
    els.resetConfirm = $('set-reset-confirm');
    els.resetConfirmYes = $('set-reset-confirm-yes');
    els.resetConfirmNo = $('set-reset-confirm-no');
    els.totemsEnabled = $('set-totems-enabled');
    els.totemList = $('set-totem-list');
    els.tagPresets = $('set-tag-presets');
    els.uhReload = $('set-uh-reload');
    els.replayTour = $('set-replay-tour');
    if (!els.view) return;

    els._bindBoardColors();
    bindReset();
    bindConnect();
    els.saveButton.addEventListener('click', save);
    els.totemsEnabled.addEventListener('change', () => els.totemList.classList.toggle('is-off', !els.totemsEnabled.checked));
    els.uhReload.addEventListener('click', async () => {
      try {
        const result = await postJson('/api/app/refresh-essays', {});
        setSaved(`reloaded ${result.count || 0} essays.`, 'good');
        await load();
      } catch (error) {
        setMessage(`reload failed: ${error.message}`, 'bad');
      }
    });

    if (els.replayTour) els.replayTour.addEventListener('click', () => runTour(true));

    window.addEventListener('hashchange', applyRoute);
    applyRoute();
  }

  // ---- the home tour ---------------------------------------------------------
  //
  // static/airdate-tour.js's own nine stops on the essays view, seen or
  // dismissed kept in this browser (its TOUR_KEY). It runs once after
  // first-run setup, and again whenever the writer presses "replay the tour".
  // The wizard's finish reloads the page, so the hand-over is a one-shot flag
  // in this tab's session, read and cleared on the next load.

  const TOUR_AFTER_SETUP = 'airdate.tour.after-setup';

  function session() {
    try { return window.sessionStorage || null; } catch (error) { return null; }
  }

  function flagTourAfterSetup() {
    try { session()?.setItem(TOUR_AFTER_SETUP, '1'); } catch (error) { /* no tour then */ }
  }

  function takeTourAfterSetup() {
    try {
      const store = session();
      if (!store || store.getItem(TOUR_AFTER_SETUP) !== '1') return false;
      store.removeItem(TOUR_AFTER_SETUP);
      return true;
    } catch (error) {
      return false;
    }
  }

  // Four stops are on the cards, so the tour waits for the pool to draw them.
  function whenPoolReady(run) {
    const pool = window.RoomPool;
    if (pool && pool.isLoaded && pool.isLoaded()) {
      run();
      return;
    }
    document.addEventListener('room:pool-ready', () => run(), { once: true });
  }

  // The stops are on the essays view; from settings, go there first. Clearing
  // the hash leaves a bare "#" in the address, which is tidied away; every
  // view reads the hash, so the route has already moved.
  function showEssaysThen(run) {
    if (currentRoute() === 'essays') {
      run();
      return;
    }
    window.addEventListener('hashchange', () => run(), { once: true });
    window.location.hash = '';
    window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
  }

  // The sticky sits beside its anchor, never over it (the editor tour's own
  // rule). Everything else is the home tour's default: its nine stops and its
  // key in this browser.
  function tourOptions(Tour) {
    return { place: Tour.placeBeside };
  }

  function runTour(replay) {
    const Tour = window.AirdateTour;
    if (!Tour) return;
    showEssaysThen(() => whenPoolReady(() => window.requestAnimationFrame(() => {
      if (replay) Tour.replay(tourOptions(Tour));
      else Tour.start(tourOptions(Tour));
    })));
  }

  function maybeTourAfterSetup() {
    if (!takeTourAfterSetup()) return;
    const Tour = window.AirdateTour;
    // The tour's own rule: once seen or skipped in this browser, it waits
    // for "replay the tour".
    if (!Tour || Tour.tourState()) return;
    // An essay link opened the editor over the room; the tour would ring
    // what the editor covers.
    if (window.RoomEditorView && window.RoomEditorView.isOpen()) return;
    runTour(false);
  }

  // ---- the first-run wizard --------------------------------------------------

  async function maybeOpenWizard() {
    let appStatus;
    try {
      appStatus = await window.RoomApi.appStatus();
    } catch (error) {
      return;
    }
    if (!(appStatus.setup && appStatus.setup.wizard) || !window.AirdateWizard) return;
    // Settings is the background behind the wizard: a fresh install has no
    // board or pool worth showing through the overlay.
    if (window.location.hash !== '#settings') window.location.hash = '#settings';
    window.AirdateWizard.open({
      status: appStatus,
      getJson,
      postJson,
      escapeHtml: esc,
      onFinish: async () => {
        // The room's board and pool already self-started against the old
        // (unset-up) status; loading the page again is the simplest way to
        // hand them the configuration the wizard just wrote. It loads on the
        // essays view, where the home tour then runs once.
        flagTourAfterSetup();
        window.location.replace(window.location.pathname + window.location.search);
      },
    });
  }

  function start() {
    bind();
    maybeOpenWizard();
    maybeTourAfterSetup();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}());
