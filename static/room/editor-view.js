// The essay editor page: drawing it, reading what the writer does, and the
// round trips (build spec §8, slice 4a).
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

  const CHIP_X = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  const DOT = '<span class="ed-state-dot" aria-hidden="true"></span>';
  const CHECK = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

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
      loadSubstack(token);
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
    if (!state || saving) return false;
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

  // ---- substack -------------------------------------------------------------

  function hostOf(url) {
    return String(url || '').replace(/^https?:\/\//i, '').replace(/\/+$/, '');
  }

  async function loadSubstack(token) {
    els.conn.className = 'ed-conn is-checking';
    els.connName.textContent = 'checking substack…';
    els.connSub.textContent = '';
    try {
      const status = await Api.getJson('/api/substack/status');
      if (token !== opening) return;
      const connector = (status && status.connector) || {};
      if (status && status.connected) {
        els.conn.className = 'ed-conn is-connected';
        els.connName.textContent = 'substack connected';
        els.connSub.textContent = status.publication_configured ? hostOf(status.publication) : 'add your substack address in settings.';
        return;
      }
      els.conn.className = 'ed-conn is-off';
      els.connName.textContent = 'substack not connected';
      if (!connector.paired) els.connSub.textContent = 'pair the airdate connector in obsidian.';
      else if (!connector.available) els.connSub.textContent = 'open obsidian with the airdate connector on.';
      else els.connSub.textContent = 'connect substack through obsidian.';
    } catch (error) {
      if (token !== opening) return;
      els.conn.className = 'ed-conn is-off';
      els.connName.textContent = 'substack status unknown';
      els.connSub.textContent = 'airdate could not check. it looks again when you next open an essay.';
    }
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
