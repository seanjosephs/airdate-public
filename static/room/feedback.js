// What airdate says after the writer does something (build spec §7.2).
//
// This replaces the old page's showRailSoonStatus, which wrote to an element
// that never existed, so its sentences reached nobody. Two places only:
//
//   the plaque  under the thing just touched (a slot, a note, a card). First
//               choice, always, while that thing is on screen.
//   the slip    one cream strip pinned bottom-centre, above everything, used
//               only when the thing acted on is gone. It can carry an undo.
//
// Green = it worked: role=status, fades after 4s. Amber = airdate will not do
// that, red = it tried and failed: role=alert, and they stay until the next
// action on the same thing or until dismissed. No corner toast, no modal, no
// sentence that only reaches the console. Slices 4 and 6 reuse the slip.
(function installRoomFeedback(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RoomFeedback = api;
})(typeof globalThis === 'object' ? globalThis : this, function createRoomFeedback() {
  const FADE_AFTER_MS = 4000;
  const FADE_MS = 300;
  const TONES = new Set(['green', 'amber', 'red']);

  const CHECK = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
  const WARN = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 4l9 16H3zM12 10v4M12 17v.5"/></svg>';
  const CLOSE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18"/></svg>';

  function toneOf(tone) {
    return TONES.has(tone) ? tone : 'green';
  }

  // Green is polite; amber and red interrupt, because the writer needs them.
  function roleFor(tone) {
    return toneOf(tone) === 'green' ? 'status' : 'alert';
  }

  function reducedMotion() {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  // A live region announces a change, not an arrival, so the sentence goes in
  // a beat after the region is in the page.
  function speak(target, text, announce) {
    if (!announce) {
      target.textContent = text;
      return;
    }
    window.setTimeout(() => { target.textContent = text; }, 60);
  }

  function remove(el, fade) {
    if (!el || !el.isConnected) return;
    if (!fade || reducedMotion()) {
      el.remove();
      return;
    }
    el.classList.add('is-fading');
    window.setTimeout(() => el.remove(), FADE_MS);
  }

  // ---- the plaque ----------------------------------------------------------

  const timers = new WeakMap();

  function clear(host) {
    if (!host) return;
    for (const el of host.querySelectorAll(':scope > .fb-plaque')) {
      window.clearTimeout(timers.get(el));
      el.remove();
    }
    host.classList.remove('has-feedback');
  }

  // Put a sentence under `host`. options: { tone, text, announce (default
  // true; false re-draws a plaque that was already read out), onGone }.
  // Returns the plaque element.
  function plaque(host, options) {
    if (!host) return null;
    const tone = toneOf(options.tone);
    clear(host);
    const el = document.createElement('p');
    el.className = `fb-plaque fb-${tone}`;
    if (options.announce !== false) el.setAttribute('role', roleFor(tone));
    el.innerHTML = `${tone === 'green' ? CHECK : WARN}<span></span>`;
    host.appendChild(el);
    host.classList.add('has-feedback');
    speak(el.querySelector('span'), String(options.text || ''), options.announce !== false);
    if (tone === 'green') {
      const wait = Number.isFinite(options.remaining) ? Math.max(0, options.remaining) : FADE_AFTER_MS;
      timers.set(el, window.setTimeout(() => {
        const fading = !reducedMotion();
        remove(el, fading);
        window.setTimeout(() => {
          if (!host.querySelector(':scope > .fb-plaque')) host.classList.remove('has-feedback');
          if (typeof options.onGone === 'function') options.onGone();
        }, fading ? FADE_MS : 0);
      }, wait));
    }
    return el;
  }

  // ---- the slip ------------------------------------------------------------

  let slipHost = null;
  let current = null;

  function host() {
    if (slipHost && slipHost.isConnected) return slipHost;
    slipHost = document.createElement('div');
    slipHost.className = 'slip-host';
    document.body.appendChild(slipHost);
    return slipHost;
  }

  function closeSlip() {
    if (!current) return;
    const { el, timer, onGone } = current;
    current = null;
    window.clearTimeout(timer);
    remove(el, true);
    if (typeof onGone === 'function') onGone();
  }

  // options: { tone, text, undo: async () => void, onGone }
  //
  // A green slip with nothing to undo fades after 4s. A slip that carries an
  // undo stays until it is dismissed, replaced or used: an undo that vanished
  // while the writer tabbed to it would be no undo at all.
  function slip(options) {
    closeSlip();
    const tone = toneOf(options.tone);
    const el = document.createElement('div');
    el.className = `slip slip-${tone}`;
    el.setAttribute('role', roleFor(tone));
    el.innerHTML = '<span class="slip-dot" aria-hidden="true"></span><span class="slip-text"></span>'
      + (typeof options.undo === 'function' ? '<button type="button" class="slip-undo">undo</button>' : '')
      + `<button type="button" class="slip-close" aria-label="dismiss">${CLOSE}</button>`;
    host().appendChild(el);
    speak(el.querySelector('.slip-text'), String(options.text || ''), true);
    const entry = { el, timer: 0, onGone: options.onGone };
    current = entry;
    const undo = el.querySelector('.slip-undo');
    if (undo) {
      undo.addEventListener('click', () => {
        if (current !== entry) return;
        closeSlip();
        options.undo();
      });
    }
    el.querySelector('.slip-close').addEventListener('click', closeSlip);
    el.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        closeSlip();
      }
    });
    if (tone === 'green' && !undo) {
      entry.timer = window.setTimeout(() => { if (current === entry) closeSlip(); }, FADE_AFTER_MS);
    }
    return { close: () => { if (current === entry) closeSlip(); }, element: el };
  }

  return { plaque, clear, slip, closeSlip, roleFor, FADE_AFTER_MS };
});
