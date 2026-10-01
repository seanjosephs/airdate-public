// The essay editor page: drawing it, reading what the writer does, and the
// round trips (build spec §8; slice 4a the page and save, 4b send).
//
// The rules live in editor.js and are tested there. This file only moves
// values between the page and that state object, which is the single source
// of truth: a section that is switched off is simply not drawn, and its
// values are still in the state and still saved.
//
// Nothing autosaves. The writer presses save (or cmd/ctrl+s). Closing with
// changes the writer made asks first, in the page; a note that changed in
// Obsidian since it was opened is never overwritten.
(function startEditor() {
  const Editor = window.RoomEditor;
  const Cards = window.AirdateCards;
  const Api = window.RoomApi;
  const Feedback = window.RoomFeedback;

  const els = {};
  let state = null;
  let essay = null;
  let visibility = null;
  let configPromise = null;
  let config = null;
  let returnFocus = null;
  let guardReturn = null;
  let pushed = false;
  let ignorePop = false;
  let saving = false;
  let opening = 0;
  let savedAt = null;
  let conflictShown = false;
  let heightTimer = 0;
  let countFrame = 0;
  // Send (slice 4b). `substack` is the last /api/substack/status: undefined
  // while it is being read, null when it could not be.
  let substack;
  let readiness = { state: 'loading', rows: [], source: false, connection: [], at: null };
  let checkToken = 0;
  let statusToken = 0;
  let sending = false;
  let refused = false;
  let madeDraft = null;

  const CHIP_X = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const DOT = '<span class="ed-state-dot" aria-hidden="true"></span>';
  const CHECK = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
  const WARN = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 4l9 16H3zM12 10v4M12 17v.5"/></svg>';
  const GO = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M9 5l7 7-7 7"/></svg>';
  const OUT = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M14 5h5v5M19 5l-8 8M11 7H6v11h11v-5"/></svg>';

  function $(id) { return document.getElementById(id); }
  const esc = (value) => Cards.escapeHtml(value);

  function isOpen() {
    return Boolean(els.root && !els.root.hidden);
  }

  // ---- config ---------------------------------------------------------------

  function loadConfig() {
    if (!configPromise) {
      configPromise = Api.getJson('/api/app/status')
        .then((status) => {
          config = (status && status.config) || {};
          return config;
        })
        .catch((error) => {
          configPromise = null;
          throw error;
        });
    }
    return configPromise;
  }

  function totemItems() {
    const totems = config && config.totems;
    return totems && totems.enabled && Array.isArray(totems.items) ? totems.items : [];
  }

  // ---- showing and hiding ---------------------------------------------------

  function background() {
    return [document.querySelector('.skip-link'), document.querySelector('.room-sidebar'), $('room-main')]
      .filter(Boolean);
  }

  function show() {
    els.root.hidden = false;
    document.body.classList.add('editor-open');
    // The page behind stops taking focus and clicks. The slip (feedback.js)
    // and slice 4c's tour callout sit on document.body outside these, so
    // they stay reachable.
    for (const el of background()) el.inert = true;
  }

  function hide() {
    els.root.hidden = true;
    document.body.classList.remove('editor-open');
    for (const el of background()) el.inert = false;
  }

  function focusAfterClose(id) {
    let target = returnFocus && returnFocus.isConnected ? returnFocus : null;
    if (!target && id) {
      const card = Array.from(document.querySelectorAll('.card')).find((el) => el.dataset.essayId === String(id));
      target = card ? card.querySelector('.card-link') : null;
    }
    if (!target) target = $('room-main');
    returnFocus = null;
    if (target && typeof target.focus === 'function') target.focus({ preventScroll: false });
  }

  function roomUrl() {
    return `/airdate/room${window.location.hash || ''}`;
  }

  function close() {
    const id = state ? state.id : '';
    hideGuard();
    hideConflict();
    Feedback.clear(els.feedback);
    Feedback.clear(els.heroFeedback);
    Feedback.clear(els.resizeFeedback);
    Feedback.clear(els.moreFeedback);
    Feedback.clear(els.readinessFeedback);
    hide();
    state = null;
    essay = null;
    savedAt = null;
    opening += 1;
    if (pushed) {
      pushed = false;
      ignorePop = true;
      window.history.back();
    } else if (Editor.essayFromSearch(window.location.search)) {
      window.history.replaceState(null, '', roomUrl());
    }
    focusAfterClose(id);
  }

  // Close, asking first when the writer has changed something. airdate's own
  // suggestions are not asked about: they are made again on the next open.
  function requestClose() {
    if (!isOpen()) return;
    if (state && Editor.writerEdited(state)) {
      showGuard();
      return;
    }
    close();
  }

  function showGuard() {
    if (!els.guard.hidden) {
      els.guardKeep.focus();
      return;
    }
    guardReturn = document.activeElement;
    els.guard.hidden = false;
    els.guardKeep.focus();
  }

  function hideGuard() {
    if (els.guard.hidden) return;
    els.guard.hidden = true;
    const back = guardReturn;
    guardReturn = null;
    return back;
  }

  // ---- opening --------------------------------------------------------------

  function openFailure(error) {
    if (error && error.kind === 'not-found') return 'that essay is not in your essays folder any more.';
    if (error && error.kind === 'setup') return 'finish setup first: choose your vault and essays folder in settings.';
    if (error && error.kind === 'network') return 'airdate is not answering, so the essay did not open. try again.';
    return 'the essay did not open. try again.';
  }

  async function open(id, options) {
    const opts = options || {};
    const essayId = String(id || '').trim();
    if (!essayId) return;
    if (isOpen()) return;
    const token = ++opening;
    returnFocus = opts.returnFocus || null;
    els.form.setAttribute('aria-busy', 'true');
    els.body.hidden = true;
    els.title.textContent = 'opening the essay…';
    els.status.textContent = '';
    els.scrap.hidden = true;
    els.state.textContent = '';
    show();
    els.title.focus();
    try {
      const [detail] = await Promise.all([
        Api.getJson(`/api/essays/${encodeURIComponent(essayId)}`),
        loadConfig(),
      ]);
      if (token !== opening) return;
      fill(detail);
      if (opts.push) {
        window.history.pushState({ roomEditor: state.id }, '', Editor.editorUrl(state.id));
        pushed = true;
      } else if (Editor.essayFromSearch(window.location.search) !== state.id) {
        window.history.replaceState(null, '', Editor.editorUrl(state.id));
      }
      els.title.focus();
      runChecks();
    } catch (error) {
      if (token !== opening) return;
      hide();
      if (Editor.essayFromSearch(window.location.search)) window.history.replaceState(null, '', roomUrl());
      focusAfterClose('');
      Feedback.slip({ tone: 'red', text: openFailure(error) });
    }
  }

  // ---- drawing --------------------------------------------------------------

  // What the stamp and the status line read: the catalog row for the dates
  // only the index knows, and the note itself for the phase and air date.
  function phaseEssay() {
    const row = (essay && essay.row) || {};
    const fm = (state && state.frontmatter) || {};
    return {
      ...row,
      id: state ? state.id : row.id,
      status: row.status || (essay && essay.status) || '',
      scheduled_at: String(fm.scheduled_at || row.scheduled_at || ''),
      published_date: String(fm.published_date || row.published_date || ''),
    };
  }

  function drawHeader() {
    const phase = phaseEssay();
    const stamp = Cards.stampFor(phase);
    els.scrap.innerHTML = Cards.stampMarkup(stamp);
    els.scrap.setAttribute('aria-label', `status: ${stamp.label}`);
    els.scrap.hidden = false;
    drawTitle();
    els.status.textContent = Editor.statusLine(phase);
    const url = String((essay && essay.obsidian_url) || '');
    els.obsidian.hidden = !url;
    if (url) els.obsidian.href = url;
  }

  function drawTitle() {
    const title = String(state.values.title || '').trim() || String((essay && essay.title) || '') || 'untitled';
    els.title.textContent = title;
    els.noteTitle.textContent = title;
  }

  function setControl(el, key) {
    const value = state.values[key];
    if (el.type === 'checkbox') el.checked = value === true;
    else if (el.type === 'radio') el.checked = String(value) === el.value;
    else if (Array.isArray(value)) el.value = value.join(', ');
    else el.value = value == null ? '' : String(value);
  }

  function drawTotems() {
    const items = totemItems();
    const current = Editor.normalize('totem', state.values.totem);
    let html = '<option value="">none</option>';
    let known = false;
    for (const item of items) {
      const key = String(item.key || '').toLowerCase();
      if (!key) continue;
      if (key === current) known = true;
      html += `<option value="${esc(key)}">${esc(String(item.label || key).toLowerCase())}</option>`;
    }
    // A totem the note carries that is not one of the writer's own stays
    // selectable, so it is shown rather than silently read as "none".
    if (current && !known) html += `<option value="${esc(current)}">${esc(current)} (not one of your totems)</option>`;
    els.totem.innerHTML = html;
    els.totem.value = current;
  }

  function drawPresets() {
    const presets = Array.isArray(config && config.tag_presets) ? config.tag_presets : [];
    els.presets.hidden = !presets.length;
    els.preset.innerHTML = '<option value="">add a preset</option>' + presets
      .map((preset, index) => `<option value="${index}">${esc(String(preset.name || '').toLowerCase())}</option>`)
      .join('');
  }

  function drawChips() {
    const tags = Array.isArray(state.values.tags) ? state.values.tags : [];
    els.chips.innerHTML = tags.map((tag) => `<li class="ed-chip"><span>${esc(tag)}</span>`
      + `<button type="button" class="ed-chip-remove" data-tag="${esc(tag)}" aria-label="remove ${esc(tag)}">${CHIP_X}</button></li>`).join('');
    els.chips.hidden = !tags.length;
  }

  function fileName(path) {
    const parts = String(path || '').split('/');
    return parts[parts.length - 1] || '';
  }

  function drawHero() {
    const path = String(state.values.hero_image || '').trim();
    const url = String((essay && essay.hero_url) || '');
    const filled = Boolean(path);
    els.heroEmpty.hidden = filled;
    els.heroFilled.hidden = !filled;
    els.hero.classList.toggle('is-filled', filled);
    if (filled) {
      els.heroName.textContent = fileName(path);
      if (url) {
        els.heroImg.src = url;
        els.heroImg.hidden = false;
      } else {
        els.heroImg.removeAttribute('src');
        els.heroImg.hidden = true;
      }
    }
  }

  function drawReadOnly() {
    const fm = state.frontmatter || {};
    const published = String(fm.published_date || '').trim();
    els.published.textContent = published ? (Editor.formatDay(published) || published) : 'stamped when you mark it live';
    els.published.classList.toggle('is-empty', !published);
    const live = String(fm.substack_url || '').trim();
    if (/^https:\/\//i.test(live)) {
      els.live.innerHTML = `<a href="${esc(live)}" rel="noopener">${esc(live)}</a>`;
      els.live.classList.remove('is-empty');
    } else {
      els.live.textContent = 'captured when you paste the post link on the board';
      els.live.classList.add('is-empty');
    }
  }

  function drawRail() {
    const air = Editor.airDate(phaseEssay());
    els.airValue.textContent = air.text;
    els.airValue.classList.toggle('is-empty', !air.onBoard);
    els.airHint.textContent = air.hint;
    els.airHint.hidden = !air.hint;
    drawNote();
    drawGates();
    drawDraftLink();
  }

  function drawNote() {
    const note = Editor.noteFor(state.values, config && config.board);
    els.note.className = `board-note ed-note pad-${note.pad} pad-${note.color}`;
    els.notePin.hidden = note.pad !== 'index';
    const air = Editor.airDate(phaseEssay());
    els.noteDate.textContent = air.onBoard ? air.text : 'no air date yet';
    for (const button of els.pads) button.setAttribute('aria-pressed', String(button.dataset.pad === note.pad));
    for (const button of els.colors) button.setAttribute('aria-pressed', String(button.dataset.color === note.color));
  }

  function drawSections() {
    const totemsOn = totemItems().length > 0;
    visibility = Editor.sectionVisibility(config && config.editor, { totemsEnabled: totemsOn });
    for (const el of els.root.querySelectorAll('[data-section]')) {
      el.hidden = !visibility[el.dataset.section];
    }
    const line = Editor.hiddenSentence(visibility);
    els.hiddenText.textContent = line;
    els.hiddenLine.hidden = !line;
  }

  function drawCount() {
    countFrame = 0;
    if (state) els.count.textContent = Editor.wordLine(state.body);
  }

  function drawScriptHeight() {
    const remembered = config && config.editor ? config.editor.script_height : null;
    applyHeight(Number.isInteger(remembered) ? remembered : null);
  }

  function fill(detail) {
    essay = detail;
    state = Editor.fromEssay(detail);
    savedAt = null;
    for (const el of els.form.querySelectorAll('[data-field]')) {
      const key = el.dataset.field;
      if (key === 'body') el.value = state.body;
      else if (key !== 'totem') setControl(el, key);
    }
    drawTotems();
    drawPresets();
    drawChips();
    drawHero();
    drawReadOnly();
    drawSections();
    drawHeader();
    drawRail();
    drawCount();
    for (const button of els.root.querySelectorAll('.ed-disclosure-button')) setDisclosure(button, true);
    els.tagInput.value = '';
    els.preset.value = '';
    els.body.hidden = false;
    // Measured once the page is showing, so an unremembered height reads true.
    drawScriptHeight();
    els.form.removeAttribute('aria-busy');
    resetSend();
    refreshState();
  }

  // The unsaved / saved line and the save button's look.
  function refreshState() {
    if (!state) return;
    const dirty = Editor.isDirty(state);
    els.save.classList.toggle('is-dirty', dirty);
    let html = '';
    let tone = '';
    if (saving) {
      html = 'saving…';
    } else if (dirty) {
      html = `${DOT}unsaved changes`;
      tone = 'is-dirty';
    } else if (savedAt) {
      html = `${CHECK}${esc(Editor.savedLine(savedAt))}`;
      tone = 'is-saved';
    }
    if (els.state.dataset.html !== html) {
      els.state.innerHTML = html;
      els.state.dataset.html = html;
    }
    els.state.className = `ed-state ${tone}`.trim();
  }

  // ---- reading the page -----------------------------------------------------

  function onField(event) {
    const el = event.target.closest('[data-field]');
    if (!el || !state || !els.form.contains(el)) return;
    const key = el.dataset.field;
    if (key === 'body') {
      state.body = el.value;
      if (!countFrame) countFrame = window.requestAnimationFrame(drawCount);
    } else if (el.type === 'checkbox') {
      state.values[key] = el.checked;
    } else if (el.type === 'radio') {
      if (!el.checked) return;
      state.values[key] = el.value;
    } else if (key === 'test_email_recipients') {
      state.values[key] = Editor.normalize(key, el.value);
    } else {
      state.values[key] = el.value;
    }
    if (key === 'title') drawTitle();
    refreshState();
  }

  function setDisclosure(button, open) {
    button.setAttribute('aria-expanded', String(open));
    const panel = $(button.getAttribute('aria-controls'));
    if (panel) panel.hidden = !open;
  }

  // ---- tags -----------------------------------------------------------------

  function commitTagInput() {
    if (!state) return false;
    const typed = els.tagInput.value;
    if (!typed.trim()) return false;
    const before = state.values.tags.length;
    state.values.tags = Editor.addTags(state.values.tags, typed);
    els.tagInput.value = '';
    drawChips();
    refreshState();
    const added = state.values.tags.length - before;
    say(els.tagSay, added === 1 ? 'added 1 tag.' : `added ${added} tags.`);
    return true;
  }

  function say(target, text) {
    target.textContent = '';
    window.setTimeout(() => { target.textContent = text; }, 60);
  }

  // ---- the script height ----------------------------------------------------

  function applyHeight(height) {
    const clamped = height == null ? null : Editor.clampScriptHeight(height);
    els.script.style.height = clamped ? `${clamped}px` : '';
    const now = clamped || Math.round(els.script.getBoundingClientRect().height) || 560;
    els.resize.setAttribute('aria-valuenow', String(now));
    els.resize.setAttribute('aria-valuetext', `${now} pixels tall`);
    return clamped;
  }

  function rememberHeight() {
    window.clearTimeout(heightTimer);
    heightTimer = window.setTimeout(async () => {
      const height = Number(els.resize.getAttribute('aria-valuenow'));
      if (!Number.isFinite(height)) return;
      try {
        const result = await Api.postJson('/api/settings/editor', { script_height: height });
        if (config && result && result.editor) config.editor = result.editor;
        Feedback.clear(els.resizeFeedback);
      } catch (error) {
        const text = error && error.kind === 'network'
          ? 'airdate is not answering, so it could not remember the size.'
          : 'airdate could not remember the size.';
        Feedback.plaque(els.resizeFeedback, { tone: 'amber', text });
      }
    }, 400);
  }

  function bindResize() {
    let startY = 0;
    let startH = 0;
    let dragging = false;
    els.resize.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      dragging = true;
      startY = event.clientY;
      startH = els.script.getBoundingClientRect().height;
      els.resize.setPointerCapture(event.pointerId);
      els.resize.classList.add('is-dragging');
    });
    els.resize.addEventListener('pointermove', (event) => {
      if (!dragging) return;
      applyHeight(startH + (event.clientY - startY));
    });
    const stop = (event) => {
      if (!dragging) return;
      dragging = false;
      els.resize.classList.remove('is-dragging');
      if (els.resize.hasPointerCapture(event.pointerId)) els.resize.releasePointerCapture(event.pointerId);
      rememberHeight();
    };
    els.resize.addEventListener('pointerup', stop);
    els.resize.addEventListener('pointercancel', stop);
    els.resize.addEventListener('keydown', (event) => {
      const now = Number(els.resize.getAttribute('aria-valuenow')) || els.script.getBoundingClientRect().height;
      const steps = { ArrowUp: -40, ArrowDown: 40, PageUp: -160, PageDown: 160 };
      let next = null;
      if (event.key in steps) next = now + steps[event.key];
      else if (event.key === 'Home') next = Editor.SCRIPT_HEIGHT_MIN;
      else if (event.key === 'End') next = Editor.SCRIPT_HEIGHT_MAX;
      if (next == null) return;
      event.preventDefault();
      applyHeight(next);
      rememberHeight();
    });
  }

  // ---- saving ---------------------------------------------------------------

  function saveFailure(error) {
    if (error && error.kind === 'refused') return error.message;
    if (error && error.kind === 'not-found') return 'the note is no longer in your essays folder, so nothing was saved.';
    if (error && error.kind === 'network') return 'airdate is not answering, so nothing was saved. try again.';
    if (error && error.kind === 'setup') return 'finish setup first. nothing was saved.';
    const message = String((error && error.message) || '').trim();
    return message ? `nothing was saved. ${message.charAt(0).toLowerCase()}${message.slice(1)}` : 'nothing was saved. try again.';
  }

  // Hand the fresh card to the pool and the board.
  function announceRow(oldId, row) {
    if (!row) return;
    essay.row = row;
    document.dispatchEvent(new CustomEvent('room:essay', {
      detail: { oldId: String(oldId), essay: row, source: 'editor' },
    }));
  }

  function followId(oldId) {
    if (!state || state.id === oldId) return;
    window.history.replaceState(pushed ? { roomEditor: state.id } : null, '', Editor.editorUrl(state.id));
  }

  async function save() {
    if (!state || saving || sending) return false;
    commitTagInput();
    if (conflictShown) {
      els.conflictSay.focus();
      return false;
    }
    const payload = Editor.savePayload(state);
    if (!Object.keys(payload.updates).length && !('body' in payload)) {
      Feedback.plaque(els.feedback, { tone: 'green', text: 'nothing new to save. the note in obsidian already matches.' });
      return true;
    }
    payload.return_row = true;
    const sent = Editor.snapshot(state);
    const oldId = state.id;
    const token = opening;
    saving = true;
    els.save.setAttribute('aria-busy', 'true');
    els.save.textContent = 'saving…';
    refreshState();
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(oldId)}/save`, payload);
      if (token !== opening || !state) return true;
      state = Editor.adoptSave(state, sent, result);
      savedAt = new Date();
      Feedback.clear(els.feedback);
      followId(oldId);
      announceRow(oldId, result && result.row);
      drawHeader();
      drawRail();
      drawReadOnly();
      runReadiness();
      return true;
    } catch (error) {
      if (token !== opening || !state) return false;
      if (error && error.kind === 'file-changed') showConflict();
      else Feedback.plaque(els.feedback, { tone: error && error.kind === 'refused' ? 'amber' : 'red', text: saveFailure(error) });
      return false;
    } finally {
      saving = false;
      els.save.removeAttribute('aria-busy');
      els.save.textContent = 'save';
      refreshState();
    }
  }

  // ---- the note changed in obsidian -----------------------------------------

  function showConflict() {
    conflictShown = true;
    els.conflict.hidden = false;
    Feedback.clear(els.conflictFeedback);
    say(els.conflictSay, 'this note changed in obsidian after you opened it. airdate saved nothing, so the version in obsidian is safe.');
  }

  function hideConflict() {
    conflictShown = false;
    els.conflict.hidden = true;
    els.conflictSay.textContent = '';
    Feedback.clear(els.conflictFeedback);
  }

  async function reloadFromObsidian() {
    if (!state) return;
    const id = state.id;
    const token = opening;
    els.reload.setAttribute('aria-busy', 'true');
    try {
      const detail = await Api.getJson(`/api/essays/${encodeURIComponent(id)}`);
      if (token !== opening) return;
      hideConflict();
      fill(detail);
      followId(id);
      Feedback.plaque(els.feedback, { tone: 'green', text: 'this is the note as it is in obsidian now.' });
      els.title.focus();
    } catch (error) {
      Feedback.plaque(els.conflictFeedback, { tone: 'red', text: openFailure(error) });
    } finally {
      els.reload.removeAttribute('aria-busy');
    }
  }

  async function copyScript() {
    if (!state) return;
    const text = state.body;
    try {
      if (!navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') throw new Error('no clipboard');
      await navigator.clipboard.writeText(text);
      Feedback.plaque(els.conflictFeedback, { tone: 'green', text: 'the script is on your clipboard.' });
    } catch (error) {
      els.script.focus();
      els.script.select();
      Feedback.plaque(els.conflictFeedback, {
        tone: 'amber',
        text: 'the browser would not copy it. the script is selected, so copy it with the keyboard.',
      });
    }
  }

  // ---- the hero image -------------------------------------------------------

  function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  const IMAGE_TYPES = new Set(['image/gif', 'image/jpeg', 'image/png', 'image/webp']);

  // Writes the image into the vault and points the note at it, straight
  // away: attach-hero is its own save, as it was in the old editor. What
  // else the writer has typed stays unsaved until they press save.
  async function attachHero(file) {
    if (!state || !file) return;
    if (conflictShown) {
      els.conflictSay.focus();
      return;
    }
    if (!IMAGE_TYPES.has(file.type)) {
      Feedback.plaque(els.heroFeedback, { tone: 'amber', text: 'that is not an image airdate can use. choose a gif, jpeg, png or webp.' });
      return;
    }
    const oldId = state.id;
    const token = opening;
    els.hero.setAttribute('aria-busy', 'true');
    Feedback.plaque(els.heroFeedback, { tone: 'green', text: `saving ${file.name} into your vault…`, remaining: 60000 });
    try {
      const dataUrl = await readFileAsDataUrl(file);
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(oldId)}/attach-hero`, {
        filename: file.name,
        dataUrl,
        also_social: true,
        expected_mtime: state.fileState.mtime,
        expected_content_hash: state.fileState.content_hash,
      });
      if (token !== opening || !state) return;
      state = Editor.adoptHero(state, result);
      if (result && result.essay) {
        essay.hero_url = result.essay.hero_url || '';
        announceRow(oldId, result.essay.row);
      }
      followId(oldId);
      drawHero();
      refreshState();
      Feedback.plaque(els.heroFeedback, { tone: 'green', text: 'the image is in your vault and the note points to it.' });
      runReadiness();
    } catch (error) {
      if (token !== opening || !state) return;
      if (error && error.kind === 'file-changed') {
        Feedback.clear(els.heroFeedback);
        showConflict();
      } else {
        const text = error && error.kind === 'network'
          ? 'airdate is not answering, so the image was not saved. try again.'
          : `the image was not saved. ${String((error && error.message) || 'try again.').toLowerCase()}`;
        Feedback.plaque(els.heroFeedback, { tone: 'red', text });
      }
    } finally {
      els.hero.removeAttribute('aria-busy');
    }
  }

  function bindHero() {
    els.heroFile.addEventListener('change', () => {
      const file = els.heroFile.files && els.heroFile.files[0];
      els.heroFile.value = '';
      if (file) attachHero(file);
    });
    const hasFiles = (event) => Array.from((event.dataTransfer && event.dataTransfer.types) || []).includes('Files');
    for (const type of ['dragenter', 'dragover']) {
      els.hero.addEventListener(type, (event) => {
        if (!hasFiles(event)) return;
        event.preventDefault();
        els.hero.classList.add('is-dragging');
      });
    }
    els.hero.addEventListener('dragleave', (event) => {
      if (!els.hero.contains(event.relatedTarget)) els.hero.classList.remove('is-dragging');
    });
    els.hero.addEventListener('drop', (event) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      els.hero.classList.remove('is-dragging');
      const file = event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) attachHero(file);
    });
  }

  // ---- substack: the connection line ----------------------------------------

  function drawConnection() {
    const line = Editor.connectionLine(substack, refused);
    els.conn.className = `ed-conn is-${line.tone === 'connected' ? 'connected' : line.tone === 'checking' ? 'checking' : 'off'}`;
    els.connName.textContent = line.name;
    els.connSub.textContent = line.sub;
  }

  async function loadSubstack() {
    const token = ++statusToken;
    const openToken = opening;
    substack = undefined;
    drawConnection();
    drawGates();
    let next = null;
    try {
      next = await Api.getJson('/api/substack/status');
    } catch (error) {
      next = null;
    }
    if (token !== statusToken || openToken !== opening) return substack;
    substack = next && typeof next === 'object' ? next : null;
    drawConnection();
    drawGates();
    return substack;
  }

  // ---- readiness: the list --------------------------------------------------

  function runChecks() {
    return Promise.all([loadSubstack(), runReadiness()]);
  }

  // What a preflight is told: what a save would write now.
  function preflightPayload() {
    const payload = { publish: Editor.publishValues(state) };
    if (state.body !== state.bodyBaseline) payload.body = state.body;
    return payload;
  }

  async function runReadiness() {
    if (!state) return;
    const token = ++checkToken;
    const openToken = opening;
    readiness = { ...readiness, state: 'loading' };
    els.check.setAttribute('aria-busy', 'true');
    drawReadinessHead();
    drawGates();
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(state.id)}/preflight`, preflightPayload());
      if (token !== checkToken || openToken !== opening || !state) return;
      applyPreflight(result);
    } catch (error) {
      if (token !== checkToken || openToken !== opening || !state) return;
      readiness = { state: 'error', rows: [], source: false, connection: [], at: new Date() };
      drawReadiness();
      const text = error && error.kind === 'network'
        ? 'airdate is not answering, so readiness was not checked. try again.'
        : `readiness was not checked. ${String((error && error.message) || 'try again.').toLowerCase()}`;
      els.readinessHead.className = 'ed-readiness-head is-bad';
      els.readinessHead.innerHTML = `${WARN}<span></span>`;
      els.readinessHead.querySelector('span').textContent = text;
    } finally {
      if (token === checkToken) els.check.removeAttribute('aria-busy');
      drawGates();
    }
  }

  function applyPreflight(result) {
    const list = Editor.readinessList(result, state.values, visibility, state.body);
    readiness = { state: 'done', rows: list.rows, source: list.source, connection: list.connection, at: new Date() };
    madeDraft = null;
    drawReadiness();
    drawGates();
  }

  function drawReadinessHead() {
    const head = els.readinessHead;
    if (readiness.state === 'loading') {
      head.className = 'ed-readiness-head';
      head.textContent = 'checking…';
      return;
    }
    if (readiness.state !== 'done') return;
    const count = readiness.rows.length;
    head.className = `ed-readiness-head ${count ? 'is-bad' : 'is-good'}`;
    head.innerHTML = `${count ? WARN : CHECK}<span></span>`;
    head.querySelector('span').textContent = Editor.readinessHeading(count, readiness.at);
  }

  function rowMarkup(row, index) {
    const words = `<span class="ed-ready-dot" aria-hidden="true"></span><span class="ed-ready-text">${esc(row.text)}</span>`;
    if (row.kind === 'source') {
      return `<li><button type="button" class="ed-ready-row" data-make-draft="1">${words}`
        + `<span class="ed-ready-go">${esc(row.action)}${GO}</span></button></li>`;
    }
    const target = row.target ? $(row.target) : null;
    if (!target || !isShown(target)) return `<li><p class="ed-ready-row is-static">${words}</p></li>`;
    const line = Number.isInteger(row.line) ? ` data-line="${row.line}"` : '';
    return `<li><a class="ed-ready-row" href="#${esc(row.target)}" data-target="${esc(row.target)}" data-row="${index}"${line}>${words}`
      + `<span class="ed-ready-go">${esc(row.action || 'go to it')}${GO}</span></a></li>`;
  }

  function isShown(el) {
    return Boolean(el && el.isConnected && !el.closest('[hidden]'));
  }

  function drawReadiness() {
    drawReadinessHead();
    els.readinessList.innerHTML = readiness.rows.map(rowMarkup).join('');
    els.readinessList.hidden = !readiness.rows.length;
    drawProblems();
  }

  // The same sentence under the field, with an amber border. Plain text:
  // the list is the live region, so nothing is read out twice.
  function drawProblems() {
    for (const el of els.root.querySelectorAll('.ed-problem')) el.remove();
    for (const el of els.root.querySelectorAll('.has-problem')) el.classList.remove('has-problem');
    for (const row of readiness.rows) {
      if (!row.target || row.target === 'ed-save') continue;
      const target = $(row.target);
      const holder = target && target.closest('.ed-field, .ed-fieldset, .ed-script');
      if (!holder || !isShown(holder)) continue;
      holder.classList.add('has-problem');
      const note = document.createElement('p');
      note.className = 'ed-problem';
      note.textContent = row.text;
      holder.appendChild(note);
    }
  }

  function reducedMotion() {
    return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  // A row's link: scroll to the field, focus it, and for a line of the
  // script put the caret there. Never the address bar: the room keeps its own
  // state in the hash.
  function goToRow(link) {
    const target = $(link.dataset.target);
    if (!target) return;
    const holder = target.closest('.ed-field, .ed-fieldset, .ed-script') || target;
    holder.scrollIntoView({ block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth' });
    target.focus({ preventScroll: true });
    if (link.dataset.line && target === els.script && state) {
      const range = Editor.lineRange(state.body, Number(link.dataset.line));
      els.script.setSelectionRange(range.start, range.end);
    }
  }

  // A source note is never sent. Its row makes the linked draft, which is a
  // new note: this one is not touched.
  async function makeLinkedDraft(button) {
    if (!state) return;
    const token = opening;
    button.setAttribute('aria-busy', 'true');
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(state.id)}/create-draft`, {});
      if (token !== opening || !state) return;
      madeDraft = { id: String(result.draft_essay_id || ''), title: String((result.draft && result.draft.title) || 'the draft') };
      const item = button.closest('li');
      item.innerHTML = `<p class="ed-ready-row is-static is-good"><span class="ed-ready-text">made a linked draft: ${esc(madeDraft.title)}. </span>`
        + `<a class="ed-ready-open" href="${esc(Editor.editorUrl(madeDraft.id))}" data-open-draft="${esc(madeDraft.id)}">open it</a></p>`;
      const row = result.draft && result.draft.row;
      if (row) document.dispatchEvent(new CustomEvent('room:essay', { detail: { oldId: '', essay: row, source: 'editor' } }));
      item.querySelector('a').focus();
    } catch (error) {
      if (token !== opening) return;
      const text = error && error.kind === 'network'
        ? 'airdate is not answering, so no draft was made. try again.'
        : `no draft was made. ${String((error && error.message) || 'try again.').toLowerCase()}`;
      Feedback.plaque(els.readinessFeedback, { tone: 'red', text });
    } finally {
      button.removeAttribute('aria-busy');
    }
  }

  // ---- send: the button and its line ------------------------------------------

  function currentGates() {
    return Editor.sendGates({
      substack,
      status: phaseEssay().status,
      readiness: { state: readiness.state, count: Editor.blockingCount(readiness.rows), source: readiness.source },
      connection: readiness.connection,
    });
  }

  function drawGates() {
    if (!state || !els.send) return;
    const gates = currentGates();
    els.send.disabled = sending || !gates.open;
    els.send.classList.toggle('is-busy', sending);
    if (sending) els.send.setAttribute('aria-busy', 'true');
    else els.send.removeAttribute('aria-busy');
    els.sendLabel.textContent = sending ? 'sending to substack' : 'send to substack';
    const why = !sending && !gates.open ? gates.line : '';
    els.sendWhyText.textContent = why;
    els.sendWhy.hidden = !why;
    const resultShown = !els.sendResult.hidden;
    els.sendCaption.hidden = Boolean(why) || resultShown;
    els.send.setAttribute('aria-describedby', why ? 'ed-send-why' : resultShown ? 'ed-send-result' : 'ed-send-caption');
  }

  function drawDraftLink() {
    const url = String((state && state.frontmatter && state.frontmatter.substack_draft_url) || '').trim();
    const show = /^https:\/\//i.test(url) && els.sendResult.hidden;
    els.draftLink.hidden = !show;
    if (show) els.draftLinkA.href = url;
  }

  function clearResult() {
    els.sendResult.hidden = true;
    els.sendResult.className = 'ed-send-result';
    els.sendResult.removeAttribute('role');
    els.sendResult.innerHTML = '';
  }

  function resetSend() {
    sending = false;
    refused = false;
    madeDraft = null;
    readiness = { state: 'loading', rows: [], source: false, connection: [], at: null };
    clearResult();
    els.readinessHead.textContent = '';
    els.readinessList.innerHTML = '';
    for (const el of els.root.querySelectorAll('.ed-problem')) el.remove();
    for (const el of els.root.querySelectorAll('.has-problem')) el.classList.remove('has-problem');
    setMore(false);
    hidePreview();
    drawGates();
    drawDraftLink();
  }

  function showSuccess(result) {
    const done = Editor.sendSuccess(result);
    els.sendResult.className = 'ed-send-result is-good';
    els.sendResult.setAttribute('role', 'status');
    let html = `<p class="ed-send-result-say">${CHECK}<span>${esc(done.text)}</span></p>`;
    if (done.url) html += `<p class="ed-send-result-actions"><a class="ed-send-open" href="${esc(done.url)}" target="_blank" rel="noopener">open the draft in substack${OUT}</a></p>`;
    if (done.warnings.length) {
      const noun = done.warnings.length === 1 ? 'thing substack did not take' : 'things substack did not take';
      html += `<p class="ed-send-result-more">${done.warnings.length} ${noun}: ${done.warnings.map(esc).join(' · ')}</p>`;
    }
    els.sendResult.innerHTML = html;
    els.sendResult.hidden = false;
  }

  function actionMarkup(action, failure) {
    if (action === 'connect') return '<button type="button" class="ed-fail-button" data-send-action="connect">sign in through obsidian</button>';
    if (action === 'retry') return '<button type="button" class="ed-fail-button is-primary" data-send-action="retry">try again</button>';
    if (action === 'forget') return '<button type="button" class="ed-fail-button" data-send-action="forget">forget the link and send again</button>';
    if (action === 'open-substack') {
      const url = Editor.substackCheckUrl(substack, state && state.frontmatter);
      return url ? `<a class="ed-fail-button" href="${esc(url)}" target="_blank" rel="noopener">open substack${OUT}</a>` : '';
    }
    return '';
  }

  function showFailure(failure) {
    els.sendResult.className = 'ed-send-result is-bad';
    els.sendResult.setAttribute('role', 'alert');
    els.sendResult.dataset.stale = failure.stale || '';
    const actions = failure.actions.map((action) => actionMarkup(action, failure)).join('');
    els.sendResult.innerHTML = `<p class="ed-send-result-say">${WARN}<span>${esc(failure.sentence)}</span></p>`
      + (actions ? `<div class="ed-send-result-actions">${actions}</div>` : '')
      + '<p class="ed-send-result-more" data-send-note hidden></p>';
    els.sendResult.hidden = false;
  }

  function sendNote(text) {
    const note = els.sendResult.querySelector('[data-send-note]');
    if (!note) return;
    note.textContent = text;
    note.hidden = !text;
  }

  // Signs in again through the Obsidian connector. True only when the
  // session is back: a sign-in window that merely opened is not a connection.
  async function reconnect() {
    try {
      const result = await Api.postJson('/api/substack/connect', {});
      return { ok: Boolean(result && result.ok && !result.pending), pending: Boolean(result && result.pending) };
    } catch (error) {
      return { ok: false, pending: false, error };
    }
  }

  // The whole press: save first (the server does it as part of the send),
  // adopt what that save left whatever becomes of the send, and try once more
  // only after a refused session the writer has signed back in to.
  async function send() {
    if (!state || sending || saving) return;
    commitTagInput();
    if (conflictShown) {
      els.conflictSay.focus();
      return;
    }
    if (!currentGates().open) return;
    const token = opening;
    const hadFocus = document.activeElement === els.send;
    sending = true;
    clearResult();
    drawGates();
    drawDraftLink();
    refreshState();
    let result = null;
    let attempts = 0;
    let signIn = null;
    try {
      for (;;) {
        attempts += 1;
        const sent = Editor.snapshot(state);
        const payload = Editor.savePayload(state);
        const oldId = state.id;
        const request = {
          updates: payload.updates,
          expected_mtime: payload.expected_mtime,
          expected_content_hash: payload.expected_content_hash,
        };
        if ('body' in payload) request.body = payload.body;
        result = await Api.postJson(`/api/essays/${encodeURIComponent(oldId)}/send`, request);
        if (token !== opening || !state) {
          leftBehind(result);
          return;
        }
        const saved = result && result.saved_state && result.saved_state.mtime != null;
        state = Editor.adoptSend(state, sent, payload, result);
        if (saved) savedAt = new Date();
        followId(oldId);
        if (result && !result.ok && result.error_kind === 'auth' && attempts === 1) {
          signIn = await reconnect();
          if (token !== opening || !state) return;
          if (Editor.shouldRetrySend(result, attempts, signIn.ok)) continue;
        }
        break;
      }
      await finishSend(result, signIn);
    } catch (error) {
      if (token !== opening || !state) {
        leftBehind({ error_kind: error && error.kind === 'network' ? 'network' : 'http', message: error && error.message });
        return;
      }
      if (error && error.kind === 'file-changed') {
        showConflict();
      } else {
        const kind = error && ['network', 'setup', 'refused'].includes(error.kind) ? error.kind : 'http';
        const outcome = error && error.kind === 'not-found'
          ? { error_kind: 'refused', message: 'the note is no longer in your essays folder' }
          : { error_kind: kind, message: error && error.message };
        showFailure(Editor.sendFailure(outcome, substack, 0));
      }
    } finally {
      if (token === opening && state) {
        sending = false;
        drawHeader();
        drawRail();
        drawReadOnly();
        refreshState();
        // A disabled button drops focus; hand it to what answered.
        if (hadFocus && (document.activeElement === document.body || !document.activeElement)) {
          if (!els.sendResult.hidden) els.sendResult.focus();
          else if (!els.send.disabled) els.send.focus();
        }
      }
    }
  }

  async function finishSend(result, signIn) {
    if (result && result.ok) {
      refused = false;
      drawConnection();
      showSuccess(result);
      refreshRow();
      return;
    }
    const kind = String((result && result.error_kind) || 'transport');
    if (kind === 'blocked' && result.preflight) applyPreflight(result.preflight);
    if (kind === 'auth') {
      refused = true;
      drawConnection();
    }
    // A failure before the connector is worded from the connection as it is now.
    if (kind === 'transport' && !(result && result.transport)) await loadSubstack();
    showFailure(Editor.sendFailure(result, substack, readiness.rows.length));
    if (kind === 'auth' && signIn) {
      if (signIn.pending) sendNote('the substack sign-in is open in obsidian. finish it there, then try again.');
      else if (signIn.error) sendNote('obsidian did not open the substack sign-in. sign in through obsidian, then try again.');
    }
  }

  // The fresh card for the pool and the board: a sent essay reads differently.
  async function refreshRow() {
    if (!state) return;
    const id = state.id;
    const token = opening;
    try {
      const detail = await Api.getJson(`/api/essays/${encodeURIComponent(id)}`);
      if (token !== opening || !state) return;
      if (detail && detail.row) announceRow(id, detail.row);
    } catch (error) {
      // The card catches up on the next index read.
    }
  }

  // The editor closed before the reply: the sentence goes on the slip.
  function leftBehind(result) {
    if (result && result.ok) {
      Feedback.slip({ tone: 'green', text: 'the draft is in substack. airdate did not publish it.' });
      return;
    }
    Feedback.slip({ tone: 'red', text: Editor.sendFailure(result || {}, substack, 0).sentence });
  }

  // ---- send: forgetting a draft link Substack no longer has -------------------
  //
  // Asked in the page. Clearing the link is the writer saying the draft was
  // deleted; if it was published instead, a new draft would copy a live post.

  function askForget(stale) {
    els.sendResult.className = 'ed-send-result is-bad';
    els.sendResult.setAttribute('role', 'alert');
    els.sendResult.innerHTML = `<p class="ed-send-result-say">${WARN}<span>forget draft ${esc(stale)} and send this as a new draft? `
      + 'do it only if you deleted that draft in substack. if it was published, a new draft would copy a post that is already live.</span></p>'
      + '<div class="ed-send-result-actions">'
      + '<button type="button" class="ed-fail-button is-primary" data-send-action="forget-yes">forget it and send</button>'
      + '<button type="button" class="ed-fail-button" data-send-action="forget-no">keep the link</button>'
      + '</div><p class="ed-send-result-more" data-send-note hidden></p>';
    els.sendResult.dataset.stale = stale;
    els.sendResult.hidden = false;
    els.sendResult.querySelector('[data-send-action="forget-no"]').focus();
  }

  async function forgetAndSend() {
    if (!state) return;
    const token = opening;
    const button = els.sendResult.querySelector('[data-send-action="forget-yes"]');
    if (button) button.setAttribute('aria-busy', 'true');
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(state.id)}/forget-draft-link`, {
        expected_mtime: state.fileState.mtime,
        expected_content_hash: state.fileState.content_hash,
      });
      if (token !== opening || !state) return;
      const oldId = state.id;
      state = Editor.adoptForget(state, result);
      followId(oldId);
      drawDraftLink();
      await send();
    } catch (error) {
      if (token !== opening || !state) return;
      if (error && error.kind === 'file-changed') {
        clearResult();
        drawGates();
        showConflict();
        return;
      }
      sendNote(error && error.kind === 'network'
        ? 'airdate is not answering, so the link is still on the note. try again.'
        : 'the link is still on the note. try again.');
    } finally {
      if (button) button.removeAttribute('aria-busy');
    }
  }

  async function onSendAction(event) {
    const button = event.target.closest('[data-send-action]');
    if (!button || !state) return;
    const action = button.dataset.sendAction;
    if (action === 'retry') {
      send();
    } else if (action === 'connect') {
      button.setAttribute('aria-busy', 'true');
      const signIn = await reconnect();
      button.removeAttribute('aria-busy');
      if (signIn.ok) {
        refused = false;
        drawConnection();
        sendNote('substack is signed in again. try again.');
      } else if (signIn.pending) {
        sendNote('the substack sign-in is open in obsidian. finish it there, then try again.');
      } else {
        sendNote('obsidian did not open the substack sign-in. check that obsidian is open with the airdate connector on.');
      }
    } else if (action === 'forget') {
      askForget(els.sendResult.dataset.stale || '');
    } else if (action === 'forget-no') {
      clearResult();
      drawGates();
      drawDraftLink();
      els.send.focus();
    } else if (action === 'forget-yes') {
      forgetAndSend();
    }
  }

  // ---- more: the thumbnail prompt and the markdown preview --------------------

  function setMore(open) {
    els.moreButton.setAttribute('aria-expanded', String(open));
    els.morePanel.hidden = !open;
  }

  function hidePreview() {
    els.previewBox.hidden = true;
    els.previewText.textContent = '';
  }

  async function copyThumbnailPrompt() {
    if (!state) return;
    const token = opening;
    els.thumbPrompt.setAttribute('aria-busy', 'true');
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(state.id)}/thumbnail-prompt`, {
        publish: Editor.publishValues(state),
      });
      if (token !== opening) return;
      const prompt = String((result && result.prompt) || '').trim();
      if (!prompt) throw new Error('airdate made an empty prompt.');
      try {
        if (!navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') throw new Error('no clipboard');
        await navigator.clipboard.writeText(prompt);
        // The ChatGPT bridge (Product Bible #8): copy the prompt, open ChatGPT,
        // and the writer drags the image back onto the hero. Kept by Sean on
        // 2026-09-30 over the design's copy-only "for your image tool". It
        // opens only after the copy worked, as the old page did.
        window.open('https://chatgpt.com', '_blank', 'noopener');
        Feedback.plaque(els.moreFeedback, { tone: 'green', text: 'the prompt is on your clipboard and chatgpt is open. make the image there, then drop it on the hero.' });
      } catch (error) {
        showPreview(prompt, 'the thumbnail prompt');
        els.previewText.focus();
        Feedback.plaque(els.moreFeedback, { tone: 'amber', text: 'the browser would not copy it. the prompt is below, so copy it with the keyboard.' });
      }
    } catch (error) {
      if (token !== opening) return;
      const text = error && error.kind === 'network'
        ? 'airdate is not answering, so there is no prompt. try again.'
        : `there is no prompt. ${String((error && error.message) || 'try again.').toLowerCase()}`;
      Feedback.plaque(els.moreFeedback, { tone: 'red', text });
    } finally {
      els.thumbPrompt.removeAttribute('aria-busy');
    }
  }

  function showPreview(text, label) {
    els.previewText.textContent = text;
    els.previewText.setAttribute('aria-label', label);
    els.previewBox.hidden = false;
  }

  async function previewMarkdown() {
    if (!state) return;
    const token = opening;
    els.preview.setAttribute('aria-busy', 'true');
    try {
      const payload = Editor.savePayload(state);
      const request = { updates: payload.updates };
      if ('body' in payload) request.body = payload.body;
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(state.id)}/preview`, request);
      if (token !== opening) return;
      Feedback.clear(els.moreFeedback);
      showPreview(String((result && result.preview_markdown) || ''), 'markdown preview');
      els.previewText.focus();
    } catch (error) {
      if (token !== opening) return;
      const text = error && error.kind === 'network'
        ? 'airdate is not answering, so there is no preview. try again.'
        : `there is no preview. ${String((error && error.message) || 'try again.').toLowerCase()}`;
      Feedback.plaque(els.moreFeedback, { tone: 'red', text });
    } finally {
      els.preview.removeAttribute('aria-busy');
    }
  }

  function bindSend() {
    els.send.addEventListener('click', send);
    els.sendResult.addEventListener('click', onSendAction);
    els.check.addEventListener('click', runChecks);
    els.readinessList.addEventListener('click', (event) => {
      const make = event.target.closest('[data-make-draft]');
      if (make) {
        makeLinkedDraft(make);
        return;
      }
      const openDraft = event.target.closest('[data-open-draft]');
      if (openDraft) {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        const id = openDraft.dataset.openDraft;
        if (state && Editor.writerEdited(state)) {
          Feedback.plaque(els.readinessFeedback, { tone: 'amber', text: 'save this essay or close it first, then open the draft.' });
          return;
        }
        close();
        open(id, { push: true });
        return;
      }
      const link = event.target.closest('a[data-target]');
      if (!link) return;
      event.preventDefault();
      goToRow(link);
    });
    els.moreButton.addEventListener('click', () => setMore(els.moreButton.getAttribute('aria-expanded') !== 'true'));
    els.thumbPrompt.addEventListener('click', copyThumbnailPrompt);
    els.preview.addEventListener('click', previewMarkdown);
    els.previewHide.addEventListener('click', () => {
      hidePreview();
      els.preview.focus();
    });
  }

  // ---- wiring ---------------------------------------------------------------

  function bind() {
    els.form.addEventListener('input', onField);
    els.form.addEventListener('change', onField);
    els.form.addEventListener('submit', (event) => {
      event.preventDefault();
      save();
    });

    els.close.addEventListener('click', requestClose);
    els.guardKeep.addEventListener('click', () => {
      const back = hideGuard();
      if (back && back.isConnected) back.focus();
      else els.title.focus();
    });
    els.guardDiscard.addEventListener('click', close);
    els.guardSave.addEventListener('click', async () => {
      hideGuard();
      const ok = await save();
      if (ok && state && !Editor.isDirty(state)) close();
    });
    els.reload.addEventListener('click', reloadFromObsidian);
    els.copy.addEventListener('click', copyScript);

    // On document, not the dialog: a click on a bare part of the page puts
    // focus on <body>, and Escape and save must still reach the editor.
    document.addEventListener('keydown', (event) => {
      if (!isOpen()) return;
      if ((event.metaKey || event.ctrlKey) && !event.altKey && (event.key === 's' || event.key === 'S')) {
        event.preventDefault();
        save();
        return;
      }
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault();
      if (!els.guard.hidden) {
        els.guardKeep.click();
        return;
      }
      requestClose();
    });

    for (const button of els.root.querySelectorAll('.ed-disclosure-button')) {
      button.addEventListener('click', () => setDisclosure(button, button.getAttribute('aria-expanded') !== 'true'));
    }

    els.chips.addEventListener('click', (event) => {
      const button = event.target.closest('.ed-chip-remove');
      if (!button || !state) return;
      const tag = button.dataset.tag;
      state.values.tags = Editor.removeTag(state.values.tags, tag);
      drawChips();
      refreshState();
      say(els.tagSay, `removed ${tag}.`);
      els.tagInput.focus();
    });
    els.tagInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ',') {
        if (!els.tagInput.value.trim()) {
          if (event.key === ',') event.preventDefault();
          return;
        }
        event.preventDefault();
        commitTagInput();
      }
    });
    els.tagInput.addEventListener('blur', commitTagInput);
    els.preset.addEventListener('change', () => {
      if (!state || els.preset.value === '') return;
      const preset = (config.tag_presets || [])[Number(els.preset.value)];
      els.preset.value = '';
      if (!preset) return;
      const before = state.values.tags.length;
      state.values.tags = Editor.addTags(state.values.tags, preset.tags || []);
      drawChips();
      refreshState();
      const added = state.values.tags.length - before;
      const name = String(preset.name || '').toLowerCase();
      say(els.tagSay, added ? `added ${added} ${added === 1 ? 'tag' : 'tags'} from ${name}.` : `every tag in ${name} is already on.`);
    });

    for (const button of els.pads) {
      button.addEventListener('click', () => {
        if (!state) return;
        state.values.note_pad = button.dataset.pad;
        drawNote();
        refreshState();
      });
    }
    for (const button of els.colors) {
      button.addEventListener('click', () => {
        if (!state) return;
        state.values.note_color = button.dataset.color;
        drawNote();
        refreshState();
      });
    }

    bindHero();
    bindResize();
    bindSend();

    // A plain click on a card title opens the editor in place. A modified
    // click or a middle click is the browser's: a new tab loads the room with
    // ?essay= and opens it there.
    document.addEventListener('click', (event) => {
      const link = event.target.closest && event.target.closest('a.card-link');
      if (!link || event.defaultPrevented) return;
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      let url;
      try {
        url = new URL(link.href, window.location.href);
      } catch (error) {
        return;
      }
      if (url.origin !== window.location.origin || url.pathname !== '/airdate/room') return;
      const id = url.searchParams.get('essay');
      if (!id) return;
      event.preventDefault();
      open(id, { push: true, returnFocus: link });
    });

    window.addEventListener('popstate', () => {
      if (ignorePop) {
        ignorePop = false;
        return;
      }
      const id = Editor.essayFromSearch(window.location.search);
      if (isOpen() && state && id !== state.id) {
        // The browser's back button while the editor is open.
        pushed = false;
        if (Editor.writerEdited(state)) {
          window.history.pushState({ roomEditor: state.id }, '', Editor.editorUrl(state.id));
          pushed = true;
          showGuard();
        } else {
          close();
        }
      } else if (!isOpen() && id) {
        open(id, { push: false });
      }
    });

    window.addEventListener('beforeunload', (event) => {
      if (isOpen() && state && Editor.writerEdited(state)) {
        event.preventDefault();
        event.returnValue = '';
      }
    });
  }

  function start() {
    els.root = $('editor');
    if (!els.root || !Editor || !Cards || !Api || !Feedback) return;
    els.form = $('ed-form');
    els.body = els.root.querySelector('.ed-body');
    els.close = $('ed-close');
    els.scrap = $('ed-scrap');
    els.title = $('ed-title');
    els.title.tabIndex = -1;
    els.status = $('ed-status');
    els.state = $('ed-state');
    els.obsidian = $('ed-obsidian');
    els.save = $('ed-save');
    els.feedback = $('ed-feedback');
    els.conflict = $('ed-conflict');
    els.conflictSay = $('ed-conflict-say');
    els.conflictSay.tabIndex = -1;
    els.conflictFeedback = $('ed-conflict-feedback');
    els.reload = $('ed-reload');
    els.copy = $('ed-copy');
    els.guard = $('ed-guard');
    els.guardSave = $('ed-guard-save');
    els.guardDiscard = $('ed-guard-discard');
    els.guardKeep = $('ed-guard-keep');
    els.script = $('ed-f-body');
    els.count = $('ed-count');
    els.resize = $('ed-resize');
    els.resizeFeedback = $('ed-resize-feedback');
    els.totem = $('ed-f-totem');
    els.hero = $('ed-hero');
    els.heroFile = $('ed-hero-file');
    els.heroEmpty = $('ed-hero-empty');
    els.heroFilled = $('ed-hero-filled');
    els.heroImg = $('ed-hero-img');
    els.heroName = $('ed-hero-name');
    els.heroFeedback = $('ed-hero-feedback');
    els.chips = $('ed-chips');
    els.tagInput = $('ed-tag-input');
    els.tagSay = $('ed-tag-say');
    els.presets = $('ed-presets');
    els.preset = $('ed-preset');
    els.published = $('ed-published');
    els.live = $('ed-live');
    els.hiddenLine = $('ed-hidden-line');
    els.hiddenText = $('ed-hidden-text');
    els.conn = $('ed-conn');
    els.connName = $('ed-conn-name');
    els.connSub = $('ed-conn-sub');
    els.airValue = $('ed-air-value');
    els.airHint = $('ed-air-hint');
    els.note = $('ed-note');
    els.notePin = $('ed-note-pin');
    els.noteTitle = $('ed-note-title');
    els.noteDate = $('ed-note-date');
    els.send = $('ed-send');
    els.sendLabel = $('ed-send-label');
    els.sendWhy = $('ed-send-why');
    els.sendWhyText = $('ed-send-why-text');
    els.sendCaption = $('ed-send-caption');
    els.sendResult = $('ed-send-result');
    els.draftLink = $('ed-draft-link');
    els.draftLinkA = $('ed-draft-link-a');
    els.check = $('ed-check');
    els.readinessHead = $('ed-readiness-head');
    els.readinessList = $('ed-readiness-list');
    els.readinessFeedback = $('ed-readiness-feedback');
    els.moreButton = $('ed-more-button');
    els.morePanel = $('ed-more-panel');
    els.thumbPrompt = $('ed-thumb-prompt');
    els.preview = $('ed-preview');
    els.moreFeedback = $('ed-more-feedback');
    els.previewBox = $('ed-preview-box');
    els.previewText = $('ed-preview-text');
    els.previewHide = $('ed-preview-hide');
    els.pads = Array.from(els.root.querySelectorAll('.ed-pad'));
    els.colors = Array.from(els.root.querySelectorAll('.ed-color'));
    bind();
    window.RoomEditorView = { open, requestClose, isOpen };
    const id = Editor.essayFromSearch(window.location.search);
    if (id) open(id, { push: false });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}());
