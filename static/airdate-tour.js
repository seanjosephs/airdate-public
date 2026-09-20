// The on-screen tour: a highlighted element and a short callout, one stop at a
// time. Seen/dismissed lives in this browser's localStorage; nothing is sent
// to the server. A stop whose element is not on screen is skipped.
//
// The tour is modal. The dimming is drawn by the ring's box-shadow, which is
// pointer-transparent, so modality is enforced here instead: keydown is trapped
// for the keyboard and the pointer events below are swallowed outside the
// callout. Without that, a click could flip a card open over the very element
// the next stop is trying to ring.
(function installAirdateTour(root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AirdateTour = api;
})(typeof globalThis === 'object' ? globalThis : this, function createAirdateTour(root) {
  const TOUR_KEY = 'airdate.tour';
  // Swallowed outside the callout while a tour runs. Wheel and scroll are
  // deliberately absent: the page may still scroll and the ring follows it.
  const BLOCKED_POINTER_EVENTS = ['pointerdown', 'mousedown', 'mouseup', 'click', 'dblclick', 'dragstart'];
  const STOPS = [
    { key: 'essays', anchor: '.all-ideas-head, #all-ideas-count', title: 'essays',
      text: 'This is your catalog. Everything in your essays folder shows here.' },
    { key: 'card', anchor: '#all-ideas-cards .garden-card', title: 'a card',
      text: 'Each essay is a card. It shows where the essay stands, its totem slot, and the buttons that move it along.' },
    { key: 'lifecycle', anchor: '#all-ideas-cards .garden-card .gc-lifecycle', title: 'the lifecycle',
      text: 'Writers Room, Writers Likey, Ready for Air, Live, Published. The buttons move an essay forward: mark it ready to schedule, give it an air date, send it, then mark it published.' },
    { key: 'calendar', anchor: '#month-rail', title: 'calendar',
      text: 'One slot per week on your publish day. Drag a Writers Likey essay onto a week to schedule it.' },
    { key: 'filing', anchor: '#inbox-lane', title: 'needs filing',
      text: 'Essays sitting outside a topic folder land here. "File this" suggests a folder and moves the note once you confirm.' },
    { key: 'filters', anchor: '.all-ideas-controls', title: 'filters',
      text: 'Search across everything, or narrow the catalog by totem or state.' },
    { key: 'editor', anchor: '#all-ideas-cards .garden-card .gc-title', title: 'the editor',
      text: 'Click a card to open its essay. Fill the required fields, copy a thumbnail prompt, and send to Substack. Sending creates a draft, never a published post.' },
    { key: 'shelf', anchor: '#nav-shelf', title: 'the shelf',
      text: 'Published essays move here, with their live links. Essays is what you are working on; the shelf is what is already out.' },
    { key: 'settings', anchor: '#nav-settings', title: 'settings',
      text: 'Everything from setup can be changed here. You can replay this tour from settings too.' },
  ];

  // The stops that will run, given a test for "this stop's element is on screen".
  function availableStops(isVisible) {
    return STOPS.filter((stop) => isVisible(stop));
  }

  function storage() {
    try { return root.localStorage || null; } catch { return null; }
  }
  function tourState() { return storage()?.getItem(TOUR_KEY) || ''; }
  function setTourState(value) {
    if (value) storage()?.setItem(TOUR_KEY, value); else storage()?.removeItem(TOUR_KEY);
  }

  function anchorFor(stop) {
    const doc = root.document;
    for (const selector of stop.anchor.split(',')) {
      const node = doc.querySelector(selector.trim());
      if (node && !node.hidden && node.getClientRects().length) return node;
    }
    return null;
  }

  let active = null;

  function end(result) {
    if (!active) return;
    const { ring, callout, onKey, onPointer, reposition, returnFocus } = active;
    active = null;
    ring.remove();
    callout.remove();
    root.document.removeEventListener('keydown', onKey, true);
    for (const type of BLOCKED_POINTER_EVENTS) root.document.removeEventListener(type, onPointer, true);
    root.removeEventListener('resize', reposition);
    root.removeEventListener('scroll', reposition, true);
    setTourState(result);
    returnFocus?.focus?.();
  }

  function start() {
    end('dismissed');
    const doc = root.document;
    const stops = availableStops((stop) => Boolean(anchorFor(stop)));
    if (!stops.length) return false;
    let index = 0;
    const ring = doc.createElement('div');
    ring.className = 'tour-ring';
    const callout = doc.createElement('div');
    callout.className = 'tour-callout';
    callout.setAttribute('role', 'dialog');
    callout.setAttribute('aria-modal', 'true');
    callout.setAttribute('aria-labelledby', 'tour-title');
    callout.innerHTML = `<p class="tour-count" id="tour-count"></p>
      <h3 class="tour-title" id="tour-title" tabindex="-1"></h3>
      <p class="tour-text" id="tour-text"></p>
      <div class="tour-actions">
        <button type="button" class="settings-button tour-skip">skip tour</button>
        <span class="tour-nav"><button type="button" class="settings-button tour-back">back</button>
        <button type="button" class="settings-button tour-next">next</button></span>
      </div>`;
    doc.body.append(ring, callout);

    function reposition() {
      const anchor = anchorFor(stops[index]);
      if (!anchor) return;
      const box = anchor.getBoundingClientRect();
      const pad = 6;
      Object.assign(ring.style, {
        top: `${box.top - pad}px`, left: `${box.left - pad}px`,
        width: `${box.width + pad * 2}px`, height: `${box.height + pad * 2}px`,
      });
      const width = callout.offsetWidth;
      const height = callout.offsetHeight;
      // Prefer below the anchor, fall back to above it, but never leave the
      // viewport: scrolling an anchor off the top used to carry the callout
      // with it, because only the fallback branch was clamped.
      const below = box.bottom + pad + 12;
      const preferred = below + height <= root.innerHeight - 8 ? below : box.top - pad - 12 - height;
      const top = Math.max(8, Math.min(preferred, Math.max(8, root.innerHeight - height - 8)));
      const left = Math.max(8, Math.min(box.left, root.innerWidth - width - 8));
      // Beside a tall element (the rail), sit to its right instead of over it.
      const beside = box.height > root.innerHeight * 0.5 && box.right + 12 + width <= root.innerWidth - 8;
      callout.style.top = `${beside ? Math.max(8, box.top) : top}px`;
      callout.style.left = `${beside ? box.right + pad + 12 : left}px`;
    }

    function show(next) {
      index = Math.max(0, Math.min(stops.length - 1, next));
      const stop = stops[index];
      anchorFor(stop)?.scrollIntoView({ block: 'center', inline: 'nearest' });
      callout.querySelector('#tour-count').textContent = `${index + 1} of ${stops.length}`;
      callout.querySelector('#tour-title').textContent = stop.title;
      callout.querySelector('#tour-text').textContent = stop.text;
      callout.querySelector('.tour-back').classList.toggle('hidden', index === 0);
      callout.querySelector('.tour-next').textContent = index === stops.length - 1 ? 'done' : 'next';
      callout.dataset.stop = stop.key;
      reposition();
      callout.querySelector('.tour-next').focus();
    }

    function onKey(event) {
      if (event.key === 'Escape') { event.preventDefault(); end('dismissed'); return; }
      if (event.key !== 'Tab') return;
      // Keep focus inside the callout while the rest of the page is dimmed.
      const focusable = [...callout.querySelectorAll('button:not(.hidden)')];
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && doc.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && doc.activeElement === last) { event.preventDefault(); first.focus(); }
      else if (!callout.contains(doc.activeElement)) { event.preventDefault(); first.focus(); }
    }

    // Anything the pointer does outside the callout is not the user's next step
    // in the tour, so it does not reach the page. Escape and "skip tour" remain
    // the ways out, and both are on screen.
    function onPointer(event) {
      if (callout.contains(event.target)) return;
      event.preventDefault();
      event.stopPropagation();
    }

    callout.querySelector('.tour-skip').addEventListener('click', () => end('dismissed'));
    callout.querySelector('.tour-back').addEventListener('click', () => show(index - 1));
    callout.querySelector('.tour-next').addEventListener('click', () => {
      if (index === stops.length - 1) end('seen'); else show(index + 1);
    });
    active = { ring, callout, onKey, onPointer, reposition, returnFocus: doc.activeElement };
    doc.addEventListener('keydown', onKey, true);
    for (const type of BLOCKED_POINTER_EVENTS) doc.addEventListener(type, onPointer, true);
    root.addEventListener('resize', reposition);
    root.addEventListener('scroll', reposition, true);
    show(0);
    return true;
  }

  // "Replay tour" in settings: forget the stored state, then run it again.
  function replay() {
    setTourState('');
    return start();
  }

  return { STOPS, TOUR_KEY, availableStops, tourState, start, replay, end };
});
