// The board on the page: renders the cork from board.js, and owns every round
// trip that changes it: a drop, a set from placing mode, an unschedule, going
// live, "it did not air", and the undo the slip carries.
//
// It loads /api/essays?scope=all, because a live essay is on the shelf rather
// than in the default list and its Monday still shows "on the shelf". The
// pool and the board keep each other current through one event on document:
// "room:essay" carries { oldId, essay, source } for any row that changed.
(function startBoard() {
  const Dates = window.RoomDates;
  const Board = window.RoomBoard;
  const Api = window.RoomApi;
  const Feedback = window.RoomFeedback;
  if (!Dates || !Board || !Api || !Feedback) return;

  const LEAVE_MS = 240;
  const HOLD_RING_MS = 2000;

  const state = {
    essays: [],
    config: {},
    loaded: false,
    failed: '',
    today: Dates.today(),
    offset: 0,
    index: new Map(),
    shown: new Map(),
    settling: new Set(),
    errors: new Map(),
    values: new Map(),
    busy: new Set(),
    placing: null,
    dragging: null,
    // The card being dragged, and whether it was dropped on a day (the drop's
    // own schedule() brings the card back; a drag that ends elsewhere does).
    lastDrag: '',
    dropped: false,
    // Plaques survive a re-render: monday -> { tone, text, until, onGone }.
    plaques: new Map(),
  };

  const els = {};

  function $(id) { return document.getElementById(id); }

  function reducedMotion() {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function wait(ms) {
    return new Promise((resolve) => { window.setTimeout(resolve, ms); });
  }

  // The board lives in the sidebar and holds three weeks: last week, this
  // week and next week (the window is last week plus two from this one). The
  // pager moves them. config board.weeks_shown is still accepted but no
  // longer read.
  function weeksShown() {
    return 2;
  }

  // The writer's publish day: config.publish_day ("monday".."sunday") or ''
  // when it is unset, which hides the board entirely.
  function publishDay() {
    const day = state.config && state.config.publish_day;
    return typeof day === 'string' ? day : '';
  }

  function boardEnabled() {
    return Boolean(publishDay());
  }

  // The configured publish day as a weekday number (Dates.weekAnchorOf's
  // convention), defaulting to monday so a missing or unrecognized value
  // never breaks the board's own arithmetic.
  function anchorWeekday() {
    const n = Dates.weekdayNumber(publishDay());
    return n >= 0 ? n : 1;
  }

  function weekdayName() {
    return publishDay() || 'monday';
  }

  function byId(id) {
    return state.essays.find((essay) => String(essay.id) === String(id)) || null;
  }

  function slotEl(monday) {
    return els.slots.querySelector(`.slot[data-monday="${CSS.escape(monday)}"]`);
  }

  function zoneEl(monday) {
    const slot = slotEl(monday);
    return slot ? slot.querySelector('.slot-zone') : null;
  }

  function footEl(monday) {
    const slot = slotEl(monday);
    return slot ? slot.querySelector('.slot-foot') : null;
  }

  function mondayOfEssay(essay) {
    const air = Board.airDayOf(essay);
    return air ? Dates.weekAnchorOf(air, anchorWeekday()) : '';
  }

  // ---- keeping the pool and the board in step -------------------------------

  function replaceEssay(oldId, row, announce) {
    const at = state.essays.findIndex((essay) => String(essay.id) === String(oldId));
    if (row) {
      if (at >= 0) state.essays[at] = row;
      else state.essays.push(row);
    } else if (at >= 0) {
      state.essays.splice(at, 1);
    }
    for (const map of [state.errors, state.values]) {
      if (row && String(row.id) !== String(oldId) && map.has(String(oldId))) {
        map.set(String(row.id), map.get(String(oldId)));
      }
      if (!row || String(row.id) !== String(oldId)) map.delete(String(oldId));
    }
    if (announce !== false) {
      document.dispatchEvent(new CustomEvent('room:essay', {
        detail: { oldId: String(oldId), essay: row, source: 'board' },
      }));
    }
  }

  document.addEventListener('room:essay', (event) => {
    const detail = event.detail || {};
    if (detail.source === 'board' || !state.loaded) return;
    replaceEssay(detail.oldId, detail.essay, false);
    render();
  });

  // ---- rendering -----------------------------------------------------------

  function renderMessage(text, role) {
    els.slots.innerHTML = `<li class="board-message"${role ? ` role="${role}"` : ''}>${escapeText(text)}</li>`;
  }

  function escapeText(text) {
    const span = document.createElement('span');
    span.textContent = String(text || '');
    return span.innerHTML;
  }

  // The sign, the slot list's label and the legend name the configured
  // weekday. "none" hides the board outright: nothing airs on a day that is
  // not set.
  function renderChrome() {
    const enabled = boardEnabled();
    if (els.board) {
      els.board.hidden = !enabled;
      els.board.style.display = enabled ? '' : 'none';
    }
    if (!enabled) return;
    const plural = `${weekdayName()}s`;
    if (els.sign) {
      els.sign.textContent = `air dates · ${plural} · one a week`;
      els.sign.setAttribute('aria-label', `air dates: ${plural}. one essay a week.`);
    }
    if (els.slots) els.slots.setAttribute('aria-label', plural);
    if (els.legendMove) els.legendMove.textContent = `move between open ${plural}`;
  }

  function render() {
    if (!state.loaded) return;
    renderChrome();
    if (!boardEnabled()) return;
    state.index = Board.weekIndex(state.essays, anchorWeekday());
    const mondays = Board.windowMondays(state.today, weeksShown(), state.offset, state.index, anchorWeekday());
    const ctx = {
      today: state.today,
      index: state.index,
      placing: state.placing ? { candidate: state.placing.candidate } : null,
      shown: state.shown,
      settling: state.settling,
    };
    const markupCtx = {
      lifted: state.placing ? state.placing.essay : null,
      placing: Boolean(state.placing),
      errors: state.errors,
      values: state.values,
    };
    els.slots.innerHTML = mondays
      .map((monday) => Board.slotMarkup(Board.slotModel(monday, ctx), markupCtx))
      .join('');
    // A live region: only a real change of weeks is read out.
    const range = Board.rangeLabel(mondays);
    if (els.range.textContent !== range) els.range.textContent = range;
    els.board.classList.toggle('is-placing', Boolean(state.placing));
    for (const id of state.busy) {
      for (const note of els.slots.querySelectorAll(`.board-note[data-essay-id="${CSS.escape(id)}"]`)) {
        note.setAttribute('aria-busy', 'true');
      }
    }
    redrawPlaques();
  }

  // A re-render draws the plaques again without reading them out a second time.
  function redrawPlaques() {
    const now = Date.now();
    for (const [monday, item] of state.plaques) {
      const foot = footEl(monday);
      if (!foot) continue;
      const remaining = item.tone === 'green' ? item.until - now : Infinity;
      if (remaining <= 0) continue;
      Feedback.plaque(foot, {
        tone: item.tone,
        text: item.text,
        announce: false,
        remaining: item.tone === 'green' ? remaining : undefined,
        onGone: () => gone(monday, item),
      });
    }
  }

  function gone(monday, item) {
    if (state.plaques.get(monday) !== item) return;
    state.plaques.delete(monday);
    if (typeof item.onGone === 'function') item.onGone();
  }

  function onScreen(el) {
    if (!el || !el.isConnected) return false;
    const rect = el.getBoundingClientRect();
    return rect.bottom > 0 && rect.top < window.innerHeight && rect.width > 0;
  }

  // The plaque under the slot when it is there to be seen; the slip, with its
  // undo, when it is not.
  function report(monday, tone, text, options) {
    const opts = options || {};
    const foot = footEl(monday);
    if (onScreen(foot)) {
      const item = { tone, text, until: Date.now() + Feedback.FADE_AFTER_MS, onGone: opts.onGone };
      state.plaques.set(monday, item);
      Feedback.plaque(foot, { tone, text, onGone: () => gone(monday, item) });
      return;
    }
    Feedback.slip({ tone, text, undo: opts.undo, onGone: opts.onGone });
  }

  // The week's other note, if it still holds one after a note left.
  function remaining(monday) {
    const entries = Board.weekIndex(state.essays, anchorWeekday()).get(monday) || [];
    return entries.length ? entries[0].essay : null;
  }

  function clearPlaque(monday) {
    state.plaques.delete(monday);
    Feedback.clear(footEl(monday));
  }

  // ---- motion --------------------------------------------------------------

  function land(monday) {
    const zone = zoneEl(monday);
    if (!zone) return;
    if (reducedMotion()) {
      // No drop. The ring holds as an outline for two seconds instead.
      zone.classList.add('is-ring-held');
      window.setTimeout(() => zone.classList.remove('is-ring-held'), HOLD_RING_MS);
      return;
    }
    const note = zone.querySelector('.board-note');
    if (note) {
      note.classList.add('is-landing');
      note.addEventListener('animationend', () => note.classList.remove('is-landing'), { once: true });
    }
    const ring = document.createElement('span');
    ring.className = 'slot-ring';
    ring.setAttribute('aria-hidden', 'true');
    zone.appendChild(ring);
    ring.addEventListener('animationend', () => ring.remove(), { once: true });
  }

  function pulseCard(id) {
    if (reducedMotion()) return;
    const card = document.querySelector(`#pool .card[data-essay-id="${CSS.escape(String(id))}"]`);
    if (!card) return;
    card.classList.add('is-pulsing');
    card.addEventListener('animationend', () => card.classList.remove('is-pulsing'), { once: true });
  }

  // ---- the round trips -------------------------------------------------------

  // Put an essay on a Monday. Returns true when it landed.
  async function schedule(id, monday, options) {
    try {
      return await scheduleNote(id, monday, options);
    } finally {
      // However it ended, the card's note is no longer up: the card comes back
      // (a no-op when it already has, or is not in the pool).
      if (window.RoomPool) window.RoomPool.untuck(id);
    }
  }

  async function scheduleNote(id, monday, options) {
    const opts = options || {};
    const essay = byId(id);
    if (!essay || state.busy.has(String(id))) return false;
    clearPlaque(monday);
    if (Board.isPast(monday, state.today)) {
      report(monday, 'amber', Board.say.past(monday, weekdayName()));
      return false;
    }
    const taken = (state.index.get(monday) || []).find((entry) => String(entry.essay.id) !== String(id));
    if (taken) {
      report(monday, 'amber', Board.say.taken(monday, taken.essay.title));
      return false;
    }
    if (essay.status !== 'Writers Likey') {
      report(monday, 'amber', Board.say.cannotLift(essay.status));
      return false;
    }
    state.busy.add(String(id));
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(id)}/ready-for-air`, { scheduled_at: monday });
      const row = (result && result.row) || { ...essay, status: 'Ready for Air', scheduled_at: monday };
      state.busy.delete(String(id));
      replaceEssay(id, row);
      state.offset = Board.offsetShowing(monday, state.today, weeksShown(), state.offset, anchorWeekday());
      render();
      land(monday);
      // The card is back before it pulses, so the pulse is seen.
      if (window.RoomPool) window.RoomPool.untuck(row.id);
      report(monday, 'green', Board.say.scheduled(monday), {
        undo: () => undoSchedule(row.id, monday, result && result.content_hash),
      });
      const unscheduleButton = slotEl(monday)?.querySelector('[data-action="unschedule"]');
      if (unscheduleButton && opts.focus !== false) unscheduleButton.focus();
      pulseCard(row.id);
      return true;
    } catch (error) {
      state.busy.delete(String(id));
      if (error && error.kind === 'file-changed') await reload();
      render();
      if (error && error.kind === 'refused') report(monday, 'amber', error.message);
      else report(monday, 'red', Board.say.failed('schedule', error));
      return false;
    }
  }

  async function unschedule(id) {
    const essay = byId(id);
    if (!essay || state.busy.has(String(id))) return;
    const monday = mondayOfEssay(essay);
    const prior = String(essay.scheduled_at || '');
    clearPlaque(monday);
    state.busy.add(String(id));
    render();
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(id)}/unschedule`, {});
      state.busy.delete(String(id));
      const row = (result && result.row) || { ...essay, status: 'Writers Likey', scheduled_at: '' };
      replaceEssay(id, row);
      render();
      zoneEl(monday)?.focus();
      const other = remaining(monday);
      report(monday, 'green', other ? Board.say.stillThere(monday, other.title) : Board.say.unscheduled(monday), {
        undo: () => undoUnschedule(row.id, prior, monday, result && result.content_hash),
      });
    } catch (error) {
      state.busy.delete(String(id));
      if (error && error.kind === 'file-changed') await reload();
      render();
      if (error && error.kind === 'refused') report(monday, 'amber', error.message);
      else report(monday, 'red', Board.say.failed('unschedule', error));
    }
  }

  async function goLive(id) {
    const essay = byId(id);
    if (!essay || state.busy.has(String(id))) return;
    const monday = mondayOfEssay(essay);
    const input = els.slots.querySelector(`input[data-essay-id="${CSS.escape(String(id))}"]`);
    const value = String(input ? input.value : state.values.get(String(id)) || '').trim();
    state.values.set(String(id), value);
    const refusal = Board.liveLinkRefusal(value, state.config.publication);
    if (refusal) {
      state.errors.set(String(id), refusal);
      render();
      els.slots.querySelector(`input[data-essay-id="${CSS.escape(String(id))}"]`)?.focus();
      return;
    }
    state.errors.delete(String(id));
    clearPlaque(monday);
    state.busy.add(String(id));
    render();
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(id)}/publish`, { substack_url: value });
      state.busy.delete(String(id));
      const row = (result && result.row) || { ...essay, status: 'Live', substack_url: value };
      const note = slotEl(monday)?.querySelector('.board-note');
      if (note && !reducedMotion()) {
        note.classList.add('is-leaving');
        await wait(LEAVE_MS);
      }
      state.values.delete(String(id));
      replaceEssay(id, row);
      // The slot keeps full strength while the green plaque is up, then dims.
      // A week that still holds another note shows that one, undimmed.
      state.settling.add(monday);
      state.shown.delete(monday);
      render();
      zoneEl(monday)?.focus();
      report(monday, 'green', Board.say.live(essay.title), {
        onGone: () => {
          state.settling.delete(monday);
          const slot = slotEl(monday);
          if (slot && slot.classList.contains('kind-shelf')) slot.classList.add('is-dim');
        },
      });
    } catch (error) {
      state.busy.delete(String(id));
      if (error && error.status === 400) {
        // The server's own sentence about the link, on the note under the field.
        state.errors.set(String(id), error.message);
        render();
        els.slots.querySelector(`input[data-essay-id="${CSS.escape(String(id))}"]`)?.focus();
        return;
      }
      if (error && error.kind === 'file-changed') await reload();
      render();
      if (error && error.kind === 'refused') report(monday, 'amber', error.message);
      else report(monday, 'red', Board.say.failed('live', error));
    }
  }

  async function didNotAir(id) {
    const essay = byId(id);
    if (!essay || state.busy.has(String(id))) return;
    const monday = mondayOfEssay(essay);
    clearPlaque(monday);
    state.busy.add(String(id));
    render();
    try {
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(id)}/did-not-air`, {});
      state.busy.delete(String(id));
      const row = (result && result.row) || { ...essay, status: 'Writers Likey', scheduled_at: '' };
      state.values.delete(String(id));
      state.errors.delete(String(id));
      replaceEssay(id, row);
      render();
      zoneEl(monday)?.focus();
      const other = remaining(monday);
      report(monday, 'green', other ? Board.say.stillThere(monday, other.title) : Board.say.didNotAir(monday));
    } catch (error) {
      state.busy.delete(String(id));
      if (error && error.kind === 'file-changed') await reload();
      render();
      if (error && error.kind === 'refused') report(monday, 'amber', error.message);
      else report(monday, 'red', Board.say.failed('didNotAir', error));
    }
  }

  // Undo a schedule: unschedule, naming the file the schedule left behind, so
  // a stale or doubled undo is a conflict and never an overwrite.
  async function undoSchedule(id, monday, hash) {
    try {
      const payload = hash ? { expected_content_hash: hash } : {};
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(id)}/unschedule`, payload);
      replaceEssay(id, result && result.row ? result.row : { ...byId(id), status: 'Writers Likey', scheduled_at: '' });
      render();
      report(monday, 'green', Board.say.undoneSchedule(monday));
    } catch (error) {
      if (error && error.kind === 'file-changed') {
        await reload();
        render();
        report(monday, 'red', Board.say.undoStale());
        return;
      }
      report(monday, 'red', Board.say.failed('undo', error));
    }
  }

  // Undo an unschedule: put it back on the same air date, if that week is
  // still open and not past.
  async function undoUnschedule(id, prior, monday, hash) {
    if (Board.isPast(monday, state.today)) {
      report(monday, 'amber', Board.say.undoPast(monday));
      return;
    }
    state.index = Board.weekIndex(state.essays, anchorWeekday());
    if (!Board.isOpen(monday, state.today, state.index)) {
      report(monday, 'amber', Board.say.undoTaken(monday));
      return;
    }
    try {
      const payload = { scheduled_at: prior || monday };
      if (hash) payload.expected_content_hash = hash;
      const result = await Api.postJson(`/api/essays/${encodeURIComponent(id)}/ready-for-air`, payload);
      replaceEssay(id, result && result.row ? result.row : { ...byId(id), status: 'Ready for Air', scheduled_at: prior });
      state.offset = Board.offsetShowing(monday, state.today, weeksShown(), state.offset, anchorWeekday());
      render();
      report(monday, 'green', Board.say.undoneUnschedule(monday));
    } catch (error) {
      if (error && error.kind === 'file-changed') {
        await reload();
        render();
        report(monday, 'red', Board.say.undoStale());
        return;
      }
      if (error && error.kind === 'refused') report(monday, 'amber', error.message);
      else report(monday, 'red', Board.say.failed('undo', error));
    }
  }

  // ---- drag and drop -------------------------------------------------------

  function clearDragging() {
    state.dragging = null;
    els.board.classList.remove('is-dragging');
    for (const el of els.slots.querySelectorAll('.slot.is-over')) el.classList.remove('is-over');
  }

  function bindDrag() {
    document.addEventListener('dragstart', (event) => {
      const handle = event.target.closest && event.target.closest('.card-handle');
      if (!handle) return;
      const card = handle.closest('.card');
      const essay = card ? byId(card.dataset.essayId) : null;
      // A writers room card can be dragged too, but only to rainy day (the
      // pool takes that drop): the board's days take a writers likey card.
      const placeable = Boolean(essay) && essay.status === 'Writers Likey';
      if (!essay || !state.loaded || (!placeable && essay.status !== 'Writers Room')) {
        event.preventDefault();
        return;
      }
      if (state.placing && window.RoomPlacing) window.RoomPlacing.cancel({ quiet: true });
      state.dragging = placeable ? { id: String(essay.id) } : null;
      state.lastDrag = String(essay.id);
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('essayId', String(essay.id));
      // What travels is the note peeling off the script, not the card.
      const note = card.querySelector('.card-postit');
      if (note) event.dataTransfer.setDragImage(note, 42, 38);
      if (placeable) els.board.classList.add('is-dragging');
      // The browser carries the note now; the card leaves the pool's layout and
      // the cards below slide up. Not in the same tick: hiding the element
      // being dragged would end the drag before it began.
      const id = String(essay.id);
      state.dropped = false;
      window.setTimeout(() => {
        if (state.lastDrag === id && !state.dropped && window.RoomPool) window.RoomPool.tuck(id, { collapse: false });
      }, 0);
    });
    document.addEventListener('dragend', () => {
      const id = state.lastDrag;
      clearDragging();
      // Let go over nothing (or over a day that takes no drop): the note goes
      // back to its card. A drop is schedule()'s to finish.
      if (!state.dropped && id && window.RoomPool) window.RoomPool.untuck(id);
      state.dropped = false;
    });

    els.slots.addEventListener('dragover', (event) => {
      if (!state.dragging) return;
      const slot = event.target.closest('.slot');
      // A past Monday takes no drop at all: dragging over it does nothing.
      if (!slot || !slot.classList.contains('takes-drop')) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      for (const el of els.slots.querySelectorAll('.slot.is-over')) {
        if (el !== slot) el.classList.remove('is-over');
      }
      slot.classList.add('is-over');
    });
    els.slots.addEventListener('dragleave', (event) => {
      const slot = event.target.closest('.slot');
      if (slot && !slot.contains(event.relatedTarget)) slot.classList.remove('is-over');
    });
    els.slots.addEventListener('drop', (event) => {
      const slot = event.target.closest('.slot');
      const dragging = state.dragging;
      if (!slot || !dragging) return;
      event.preventDefault();
      state.dropped = true;
      clearDragging();
      schedule(dragging.id, slot.dataset.monday, { origin: 'drag' });
    });
  }

  // ---- clicks and typing on the board ----------------------------------------

  function bindBoard() {
    els.slots.addEventListener('click', (event) => {
      const button = event.target.closest('[data-action]');
      if (button) {
        const id = button.dataset.essayId;
        const action = button.dataset.action;
        if (action === 'unschedule') unschedule(id);
        else if (action === 'live') goLive(id);
        else if (action === 'did-not-air') didNotAir(id);
        else if (action === 'show-other') {
          const monday = button.dataset.monday;
          state.shown.set(monday, (Number(button.dataset.pick) || 0) + 1);
          render();
          slotEl(monday)?.querySelector('[data-action="show-other"]')?.focus();
        }
        return;
      }
      // While placing, a click on an open Monday sets the note there.
      const slot = event.target.closest('.slot');
      if (slot && state.placing && window.RoomPlacing) window.RoomPlacing.setAt(slot.dataset.monday);
    });
    els.slots.addEventListener('input', (event) => {
      const input = event.target.closest('input[data-essay-id]');
      if (input) state.values.set(input.dataset.essayId, input.value);
    });
    // Enter in the link field is the same as the live button.
    els.slots.addEventListener('keydown', (event) => {
      const input = event.target.closest('input[data-essay-id]');
      if (input && event.key === 'Enter') {
        event.preventDefault();
        goLive(input.dataset.essayId);
      }
    });
    for (const button of els.pagers) {
      button.addEventListener('click', () => {
        state.offset += Number(button.dataset.page) || 0;
        render();
      });
    }
  }

  // ---- loading ---------------------------------------------------------------

  async function fetchAll() {
    const [status, catalog] = await Promise.all([
      Api.getJson('/api/app/status'),
      Api.getJson('/api/essays?scope=all'),
    ]);
    state.config = (status && status.config) || {};
    state.essays = (Array.isArray(catalog && catalog.essays) ? catalog.essays : [])
      .filter((essay) => essay.status !== 'Archived');
  }

  // After a file-changed conflict the board and the pool both take the note
  // as it is on disk now.
  async function reload() {
    try {
      await fetchAll();
    } catch (error) {
      // Keep what is on screen; the sentence already says to try again.
    }
    document.dispatchEvent(new CustomEvent('room:reload'));
  }

  async function load() {
    els.slots.setAttribute('aria-busy', 'true');
    renderMessage('loading the board…', 'status');
    try {
      await fetchAll();
      state.loaded = true;
      render();
    } catch (error) {
      if (error && error.kind === 'setup') renderMessage('finish setup first, then the board fills in.', 'alert');
      else renderMessage(Board.say.failed('load', error), 'alert');
    } finally {
      els.slots.removeAttribute('aria-busy');
    }
  }

  // ---- what placing mode uses --------------------------------------------------

  function setPlacing(placing) {
    state.placing = placing;
    render();
  }

  function announce(text) {
    if (!els.say) return;
    els.say.textContent = '';
    window.setTimeout(() => { els.say.textContent = String(text || ''); }, 60);
  }

  function showLifted(title) {
    if (!els.placingLabel) return;
    els.placingLabel.textContent = title ? `placing ${String(title).toLowerCase()}` : '';
  }

  // Page so a Monday is on the board, then return its drop zone.
  function reveal(monday) {
    const next = Board.offsetShowing(monday, state.today, weeksShown(), state.offset, anchorWeekday());
    if (next !== state.offset) {
      state.offset = next;
      render();
    }
    return zoneEl(monday);
  }

  function start() {
    els.board = $('board');
    els.slots = $('board-slots');
    els.sign = $('board-sign');
    els.range = $('board-range');
    els.say = $('board-legend-say');
    els.placingLabel = $('board-legend-placing');
    els.legendMove = $('board-legend-move');
    els.pagers = Array.from(document.querySelectorAll('[data-page]'));
    if (!els.board || !els.slots) return;
    bindBoard();
    bindDrag();
    Dates.watchDay((today) => {
      state.today = today;
      render();
    });
    load();
  }

  window.RoomBoardView = {
    ready: () => state.loaded,
    essay: byId,
    today: () => state.today,
    index: () => Board.weekIndex(state.essays, anchorWeekday()),
    anchor: anchorWeekday,
    weekdayName,
    placing: () => state.placing,
    setPlacing,
    offset: () => state.offset,
    setOffset: (offset) => {
      state.offset = Number(offset) || 0;
      render();
    },
    reveal,
    zone: zoneEl,
    schedule,
    ghost: () => (els.slots ? els.slots.querySelector('.slot-ghost .board-note') : null),
    announce,
    showLifted,
    report,
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}());
