// Motion for the grabber: the essay card turns into its post-it, the post-it
// flies to its Monday on the board, and the cards left behind slide up to fill
// the gap. Putting a note back runs the same moves in reverse.
//
//   flip(container, change)   run change(), then glide every card that moved
//                             from where it was to where it is now.
//   collapse(card, rect)      the card shrinks into its post-it and goes.
//   arrive(card)              the card comes back, soft, in its new place.
//   fly(node, from, to)       a post-it travels between two rectangles.
//
// Everything here is the Web Animations API on the elements as they are: no
// state of its own, and nothing the room needs in order to work. With reduced
// motion asked for, or no animate() at all, each call changes things at once
// and resolves.
(function installRoomFlight(root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RoomFlight = api;
})(typeof globalThis === 'object' ? globalThis : this, function createRoomFlight(root) {
  const EASE = 'cubic-bezier(0.22, 0.8, 0.24, 1)';
  const SLIDE_MS = 380;
  const COLLAPSE_MS = 280;
  const ARRIVE_MS = 260;
  const FLIGHT_MS = 640;
  // How far a post-it rises on its way, in pixels, and how much bigger it
  // looks at the top of the arc.
  const LIFT = 36;
  const LIFT_SCALE = 1.18;

  function reducedMotion() {
    try {
      return Boolean(root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (error) {
      return false;
    }
  }

  function canAnimate(el) {
    return Boolean(el && typeof el.animate === 'function') && !reducedMotion();
  }

  // Resolves when the animation ends. A window the browser has put in the
  // background does not run animations, and a promise waiting on one would
  // hold a card out of the pool, so a timer backs it up.
  function finished(animation, ms) {
    return new Promise((resolve) => {
      let done = false;
      const end = () => {
        if (done) return;
        done = true;
        resolve();
      };
      animation.addEventListener('finish', end);
      animation.addEventListener('cancel', end);
      root.setTimeout(end, (Number(ms) || 400) + 400);
    });
  }

  // Where a card has to move from, as a translate, to look as though it never
  // moved. Null when it moved less than a pixel: not worth an animation.
  function slideFrom(before, after) {
    if (!before || !after) return null;
    const dx = before.left - after.left;
    const dy = before.top - after.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return null;
    return { dx: Math.round(dx * 10) / 10, dy: Math.round(dy * 10) / 10 };
  }

  // The three poses of a flight, as transforms of a node whose own box is
  // `natural` and which sits at the top-left of the page: where it starts,
  // the top of the arc, and where it lands. Corners, not centres, so the
  // node scales from its top-left.
  function flightPath(from, to, natural) {
    const width = Number(natural && natural.width) || 1;
    const height = Number(natural && natural.height) || 1;
    const start = { x: from.left, y: from.top, sx: from.width / width, sy: from.height / height };
    const end = { x: to.left, y: to.top, sx: to.width / width, sy: to.height / height };
    const midSx = (start.sx + end.sx) / 2 * LIFT_SCALE;
    const midSy = (start.sy + end.sy) / 2 * LIFT_SCALE;
    // The arc's top is centred over the middle of the straight line, so a
    // bigger note grows around its middle rather than drifting down-right.
    const cx = (start.x + start.sx * width / 2 + end.x + end.sx * width / 2) / 2;
    const cy = (start.y + start.sy * height / 2 + end.y + end.sy * height / 2) / 2 - LIFT;
    const mid = { x: cx - midSx * width / 2, y: cy - midSy * height / 2, sx: midSx, sy: midSy };
    return { start, mid, end };
  }

  function pose(p, turn) {
    return `translate(${p.x}px, ${p.y}px) rotate(${turn}deg) scale(${p.sx}, ${p.sy})`;
  }

  // Run change(), then slide every card in the container that moved.
  function flip(container, change, options) {
    const opts = options || {};
    const cards = () => (container ? Array.from(container.querySelectorAll('.card')) : []);
    const before = new Map();
    if (canAnimate(container)) {
      for (const card of cards()) before.set(card, card.getBoundingClientRect());
    }
    const result = change();
    if (!before.size) return result;
    for (const card of cards()) {
      const was = before.get(card);
      if (!was) continue;
      const move = slideFrom(was, card.getBoundingClientRect());
      if (!move) continue;
      card.animate(
        [{ transform: `translate(${move.dx}px, ${move.dy}px)` }, { transform: 'none' }],
        { duration: opts.duration || SLIDE_MS, easing: EASE },
      );
    }
    return result;
  }

  // The card shrinks into the spot its post-it was in, and fades. Resolves
  // when it is gone; the caller hides it then.
  function collapse(card, rect) {
    if (!canAnimate(card)) return Promise.resolve();
    const box = card.getBoundingClientRect();
    const originX = rect ? rect.left + rect.width / 2 - box.left : box.width / 2;
    const originY = rect ? rect.top + rect.height / 2 - box.top : box.height / 2;
    card.style.transformOrigin = `${originX}px ${originY}px`;
    const animation = card.animate(
      [
        { transform: 'scale(1)', opacity: 1, offset: 0 },
        { transform: 'scale(0.55)', opacity: 0.85, offset: 0.4 },
        { transform: 'scale(0.08)', opacity: 0, offset: 1 },
      ],
      { duration: COLLAPSE_MS, easing: 'ease-in', fill: 'forwards' },
    );
    return finished(animation, COLLAPSE_MS).then(() => {
      animation.cancel();
      card.style.transformOrigin = '';
    });
  }

  // The card comes back: it was hidden at opacity 0 for the move, and now it
  // settles in.
  function arrive(card) {
    if (!card) return Promise.resolve();
    if (!canAnimate(card)) {
      card.style.opacity = '';
      return Promise.resolve();
    }
    const animation = card.animate(
      [
        { opacity: 0, transform: 'scale(0.92)' },
        { opacity: 1, transform: 'scale(1)' },
      ],
      { duration: ARRIVE_MS, easing: EASE },
    );
    card.style.opacity = '';
    return finished(animation, ARRIVE_MS);
  }

  // A post-it travels from one rectangle to another. `source` is the node to
  // copy (a card's post-it button, say): the copy rides above everything and
  // never takes the pointer. Resolves when it lands and is gone.
  function fly(source, from, to, options) {
    const opts = options || {};
    if (!source || !from || !to || !root.document) return Promise.resolve();
    const doc = root.document;
    const node = doc.createElement('div');
    node.className = 'flying-note';
    node.setAttribute('aria-hidden', 'true');
    const copy = source.cloneNode(true);
    copy.removeAttribute('id');
    copy.removeAttribute('data-action');
    copy.removeAttribute('aria-label');
    copy.setAttribute('tabindex', '-1');
    copy.classList.add('flying-note-face');
    node.appendChild(copy);
    doc.body.appendChild(node);
    const natural = { width: copy.offsetWidth || 84, height: copy.offsetHeight || 76 };
    node.style.width = `${natural.width}px`;
    node.style.height = `${natural.height}px`;
    if (!canAnimate(node)) {
      node.remove();
      return Promise.resolve();
    }
    const path = flightPath(from, to, natural);
    const turnFrom = opts.turnFrom === undefined ? -7 : opts.turnFrom;
    const turnTo = opts.turnTo === undefined ? 0 : opts.turnTo;
    const animation = node.animate(
      [
        { transform: pose(path.start, turnFrom), opacity: 1, offset: 0 },
        { transform: pose(path.mid, (turnFrom + turnTo) / 2), opacity: 1, offset: 0.45 },
        { transform: pose(path.end, turnTo), opacity: 1, offset: 0.82 },
        { transform: pose(path.end, turnTo), opacity: 0, offset: 1 },
      ],
      { duration: opts.duration || FLIGHT_MS, easing: EASE, fill: 'forwards' },
    );
    if (typeof opts.onLanding === 'function') {
      // Just before it fades the note it became takes over.
      const timer = root.setTimeout(opts.onLanding, Math.round((opts.duration || FLIGHT_MS) * 0.7));
      animation.addEventListener('cancel', () => root.clearTimeout(timer));
    }
    return finished(animation, opts.duration || FLIGHT_MS).then(() => {
      animation.cancel();
      node.remove();
    });
  }

  return {
    flip,
    collapse,
    arrive,
    fly,
    slideFrom,
    flightPath,
    reducedMotion,
    SLIDE_MS,
    COLLAPSE_MS,
    ARRIVE_MS,
    FLIGHT_MS,
  };
});
