// The on-screen tour: a highlighted element and a short callout, one stop at a
// time. A stop whose element is not on screen is skipped.
//
// Two tours run on this module. The home tour is start() with no options:
// its nine STOPS, seen/dismissed in this browser's localStorage, nothing sent
// to the server (tests/test_airdate_tour.py pins it). The essay editor's tour
// passes options (EDITOR_STOPS, no browser key, a wider ring, a sticky beside
// its anchor, arrow keys, focus back on the anchor) and records itself on the
// server through its own onEnd. Every option defaults to the home tour.
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

  // The essay editor's five steps (TourSteps artboard). `side` is where the
  // sticky sits: header anchors below, rail anchors to the left, the script
  // to the right. `focus` is what takes focus when the tour ends on that step.
  const EDITOR_STOPS = [
    { key: 'stamp', anchor: '#editor #ed-scrap', side: 'below', title: 'this stamp is where it stands.',
      text: 'writers room today. airdate stamps it when you star it, when you schedule it, and when it airs. you never set it by hand.' },
    { key: 'script', anchor: '#editor .ed-script-frame', side: 'right', focus: '#ed-f-body', title: 'write here.',
      text: 'this is the script. everything else on the page is packaging. drag the bottom edge to make it taller, and airdate will remember.' },
    { key: 'save', anchor: '#editor #ed-head-end', side: 'below', focus: '#ed-save', title: 'nothing saves itself.',
      text: 'airdate writes to your obsidian note only when you press save. the orange dot up here means something is unsaved.' },
    { key: 'send', anchor: '#editor #ed-go', side: 'left', focus: '#ed-check', title: 'check before you send.',
      text: 'check readiness lists what substack would reject, one line per problem, each one a link to the field. send makes a draft in substack. never a post.' },
    { key: 'board', anchor: '#editor .ed-board', side: 'left', focus: '#editor .ed-board .ed-pad', title: 'pick its note.',
      text: 'this is how the essay looks on the board. pick once, it sticks. the gear up top hides the sections you do not use.' },
  ];

  // Every option is the home tour's own behaviour, so start() with nothing
  // passed is the home tour exactly as it was.
  const DEFAULTS = {
    stops: STOPS,
    key: TOUR_KEY,            // localStorage key for seen/dismissed; null keeps nothing
    pad: 6,                   // the ring's distance outside the anchor
    place: null,              // null: the home tour's arithmetic; or a function like placeBeside
    returnToAnchor: false,    // false: focus goes back where it was before the tour
    arrows: false,            // left and right move between stops
    onEnd: null,              // (result, stop) after the tour ends
    textFor: null,            // (stop) => a sentence for this stop, or undefined for stop.text
    className: '',            // added to the ring and the callout
    buttonClass: 'settings-button',
    skipLabel: 'skip tour',
    countLabel: (n, total) => `${n} of ${total}`,
  };
  const SIDES = ['below', 'above', 'right', 'left'];
  const OPPOSITE = { below: 'above', above: 'below', right: 'left', left: 'right' };

  // The stops that will run, given a test for "this stop's element is on screen".
  function availableStops(isVisible, stops = STOPS) {
    return stops.filter((stop) => isVisible(stop));
  }

  function storage() {
    try { return root.localStorage || null; } catch { return null; }
  }
  function tourState(key = TOUR_KEY) { return storage()?.getItem(key) || ''; }
  function setTourState(value, key = TOUR_KEY) {
    if (value) storage()?.setItem(key, value); else storage()?.removeItem(key);
  }

  function anchorFor(stop) {
    const doc = root.document;
    for (const selector of stop.anchor.split(',')) {
      const node = doc.querySelector(selector.trim());
      if (node && !node.hidden && node.getClientRects().length) return node;
    }
    return null;
  }

  // Beside the anchor and never over it: `gap` clear of the ring on the
  // stop's own side, then the opposite side, then the other two, whichever
  // first fits inside the window. When none does, the asked-for side,
  // pulled inside the window.
  function placeBeside({ box, width, height, pad, stop, viewport, gap = 24 }) {
    const margin = 8;
    const ring = { top: box.top - pad, left: box.left - pad, right: box.right + pad, bottom: box.bottom + pad };
    const clampX = (x) => Math.max(margin, Math.min(x, viewport.width - width - margin));
    const clampY = (y) => Math.max(margin, Math.min(y, viewport.height - height - margin));
    const spots = {
      below: { top: ring.bottom + gap, left: clampX(box.left) },
      above: { top: ring.top - gap - height, left: clampX(box.left) },
      right: { top: clampY(box.top), left: ring.right + gap },
      left: { top: clampY(box.top), left: ring.left - gap - width },
    };
    const fits = (spot) => spot.top >= margin && spot.left >= margin
      && spot.top + height <= viewport.height - margin && spot.left + width <= viewport.width - margin;
    const asked = SIDES.includes(stop && stop.side) ? stop.side : 'below';
    const order = [asked, OPPOSITE[asked], ...SIDES.filter((side) => side !== asked && side !== OPPOSITE[asked])];
    const side = order.find((name) => fits(spots[name]));
    if (side) return { top: spots[side].top, left: spots[side].left, side };
    return { top: clampY(spots[asked].top), left: clampX(spots[asked].left), side: asked };
  }

  // What takes focus when a tour ends on this stop: its own `focus` control,
  // else the anchor when it can take focus, else the first control inside it,
  // else the anchor itself, made focusable by script only.
  function focusTargetFor(stop) {
    const doc = root.document;
    const anchor = anchorFor(stop);
    if (stop.focus) {
      const named = doc.querySelector(stop.focus);
      if (named && !named.disabled && named.getClientRects?.().length) return named;
    }
    if (!anchor) return null;
    const controls = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]';
    if (anchor.matches?.(controls)) return anchor;
    const inner = anchor.querySelector?.(controls.split(', ').map((s) => `${s}:not([tabindex="-1"])`).join(', '));
    if (inner) return inner;
    if (!anchor.hasAttribute?.('tabindex')) anchor.setAttribute('tabindex', '-1');
    return anchor;
  }

  let active = null;

  function end(result) {
    if (!active) return;
    const { ring, callout, onKey, onPointer, reposition, returnFocus, opts, current } = active;
    active = null;
    ring.remove();
    callout.remove();
    root.document.removeEventListener('keydown', onKey, true);
    for (const type of BLOCKED_POINTER_EVENTS) root.document.removeEventListener(type, onPointer, true);
    root.removeEventListener('resize', reposition);
    root.removeEventListener('scroll', reposition, true);
    if (opts.key) setTourState(result, opts.key);
    const stop = current();
    const target = opts.returnToAnchor ? (focusTargetFor(stop) || returnFocus) : returnFocus;
    target?.focus?.();
    if (typeof opts.onEnd === 'function') opts.onEnd(result, stop);
  }

  function isActive() {
    return Boolean(active);
  }

  function start(options) {
    end('dismissed');
    const opts = { ...DEFAULTS, ...(options || {}) };
    const doc = root.document;
    const stops = availableStops((stop) => Boolean(anchorFor(stop)), opts.stops);
    if (!stops.length) return false;
    let index = 0;
    const extra = opts.className ? ` ${opts.className}` : '';
    const btn = opts.buttonClass;
    const ring = doc.createElement('div');
    ring.className = `tour-ring${extra}`;
    const callout = doc.createElement('div');
    callout.className = `tour-callout${extra}`;
    callout.setAttribute('role', 'dialog');
    callout.setAttribute('aria-modal', 'true');
    callout.setAttribute('aria-labelledby', 'tour-title');
    callout.innerHTML = `<p class="tour-count" id="tour-count"></p>
      <h3 class="tour-title" id="tour-title" tabindex="-1"></h3>
      <p class="tour-text" id="tour-text"></p>
      <div class="tour-actions">
        <button type="button" class="${btn} tour-skip">${opts.skipLabel}</button>
        <span class="tour-nav"><button type="button" class="${btn} tour-back">back</button>
        <button type="button" class="${btn} tour-next">next</button></span>
      </div>`;
    doc.body.append(ring, callout);

    function reposition() {
      const anchor = anchorFor(stops[index]);
      if (!anchor) return;
      const box = anchor.getBoundingClientRect();
      const pad = opts.pad;
      Object.assign(ring.style, {
        top: `${box.top - pad}px`, left: `${box.left - pad}px`,
        width: `${box.width + pad * 2}px`, height: `${box.height + pad * 2}px`,
      });
      const width = callout.offsetWidth;
      const height = callout.offsetHeight;
      if (typeof opts.place === 'function') {
        const viewport = { width: root.innerWidth, height: root.innerHeight };
        const spot = opts.place({ box, width, height, pad, stop: stops[index], viewport });
        callout.style.top = `${spot.top}px`;
        callout.style.left = `${spot.left}px`;
        callout.dataset.side = spot.side || '';
        return;
      }
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
      callout.querySelector('#tour-count').textContent = opts.countLabel(index + 1, stops.length);
      callout.querySelector('#tour-title').textContent = stop.title;
      const text = typeof opts.textFor === 'function' ? opts.textFor(stop) : undefined;
      callout.querySelector('#tour-text').textContent = typeof text === 'string' ? text : stop.text;
      callout.querySelector('.tour-back').classList.toggle('hidden', index === 0);
      callout.querySelector('.tour-next').textContent = index === stops.length - 1 ? 'done' : 'next';
      callout.dataset.stop = stop.key;
      reposition();
      callout.querySelector('.tour-next').focus();
    }

    function onKey(event) {
      if (event.key === 'Escape') { event.preventDefault(); end('dismissed'); return; }
      if (opts.arrows && (event.key === 'ArrowRight' || event.key === 'ArrowLeft')) {
        event.preventDefault();
        show(index + (event.key === 'ArrowRight' ? 1 : -1));
        return;
      }
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
    active = { ring, callout, onKey, onPointer, reposition, returnFocus: doc.activeElement, opts, current: () => stops[index] };
    doc.addEventListener('keydown', onKey, true);
    for (const type of BLOCKED_POINTER_EVENTS) doc.addEventListener(type, onPointer, true);
    root.addEventListener('resize', reposition);
    root.addEventListener('scroll', reposition, true);
    show(0);
    return true;
  }

  // "Replay tour" in settings: forget the stored state, then run it again.
  function replay(options) {
    const key = options && 'key' in options ? options.key : TOUR_KEY;
    if (key) setTourState('', key);
    return start(options);
  }

  return { STOPS, EDITOR_STOPS, TOUR_KEY, availableStops, tourState, start, replay, end, isActive, placeBeside };
});
