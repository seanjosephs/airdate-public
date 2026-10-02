// Roving focus for the pool (build spec §6.5).
//
// One card is active at a time. Only its controls are in the tab order, so
// Tab moves through the page's regions instead of through every card, and the
// arrow keys move between cards. Enter on the title follows the link, which
// is the browser's own behaviour; nothing here intercepts it.
//
// Letter keys go to handlers registered with onKey, and only while focus is
// on a card: never while the writer is typing in the search box. Slice 3 adds
// placing mode ("a" lifts the note) through that hook.
(function installRoomKeys(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RoomKeys = api;
})(typeof globalThis === 'object' ? globalThis : this, function createRoomKeys() {
  const CONTROLS = 'a[href], button, input, select, textarea, [tabindex]';

  function isTyping(target) {
    if (!target || typeof target.closest !== 'function') return false;
    if (target.isContentEditable) return true;
    const field = target.closest('input, textarea, select');
    if (!field) return false;
    // A checkbox or radio takes no letters, so it does not count as typing.
    const type = String(field.type || '').toLowerCase();
    return !(field.tagName === 'INPUT' && ['checkbox', 'radio', 'button', 'submit', 'reset'].includes(type));
  }

  // Which neighbour an arrow key goes to. `columns` is an array of arrays of
  // items; `tops` gives an item's vertical position. Pure, for testing.
  function neighbour(columns, current, key, topOf) {
    const cols = columns.filter((column) => column.length);
    let col = -1;
    let row = -1;
    cols.forEach((column, c) => {
      const r = column.indexOf(current);
      if (r >= 0) { col = c; row = r; }
    });
    if (col < 0) return cols.length ? cols[0][0] : null;
    if (key === 'ArrowDown') return cols[col][row + 1] || null;
    if (key === 'ArrowUp') return row > 0 ? cols[col][row - 1] : null;
    if (key === 'Home') return cols[0][0];
    if (key === 'End') {
      const last = cols[cols.length - 1];
      return last[last.length - 1];
    }
    const step = key === 'ArrowRight' ? 1 : key === 'ArrowLeft' ? -1 : 0;
    if (!step) return null;
    const target = cols[col + step];
    if (!target) return null;
    const top = topOf(current);
    let best = target[0];
    let bestDistance = Infinity;
    for (const item of target) {
      const distance = Math.abs(topOf(item) - top);
      if (distance < bestDistance) { best = item; bestDistance = distance; }
    }
    return best;
  }

  function createRoving(options) {
    const container = options.container;
    const itemSelector = options.itemSelector || '.card';
    const primarySelector = options.primarySelector || 'a[href]';
    const columnSelector = options.columnSelector || '';
    const keyHandlers = [];
    let active = null;

    function items() {
      return Array.from(container.querySelectorAll(itemSelector));
    }

    function columns() {
      if (!columnSelector) return [items()];
      return Array.from(container.querySelectorAll(columnSelector))
        .map((column) => Array.from(column.querySelectorAll(itemSelector)));
    }

    function controlsOf(item) {
      return Array.from(item.querySelectorAll(CONTROLS));
    }

    function applyTabOrder() {
      for (const item of items()) {
        const on = item === active;
        item.classList.toggle('is-active', on);
        for (const control of controlsOf(item)) {
          if (on) control.removeAttribute('tabindex');
          else control.setAttribute('tabindex', '-1');
        }
      }
    }

    function setActive(item, focus) {
      if (!item) return;
      active = item;
      applyTabOrder();
      if (focus) {
        const primary = item.querySelector(primarySelector) || controlsOf(item)[0];
        if (primary) primary.focus();
      }
    }

    // Call after every render. Keeps the active card if it is still there,
    // or a card with the same key (pool.js re-renders a card after a star).
    function refresh(preferredKey) {
      const all = items();
      let next = null;
      if (preferredKey) next = all.find((item) => item.dataset.essayId === preferredKey) || null;
      if (!next && active && all.includes(active)) next = active;
      if (!next && active) next = all.find((item) => item.dataset.essayId === active.dataset.essayId) || null;
      active = next || all[0] || null;
      applyTabOrder();
    }

    function onKey(handler) {
      keyHandlers.push(handler);
    }

    container.addEventListener('focusin', (event) => {
      const item = event.target.closest(itemSelector);
      if (item && item !== active) {
        active = item;
        applyTabOrder();
      }
    });

    container.addEventListener('keydown', (event) => {
      if (event.defaultPrevented || isTyping(event.target)) return;
      const item = event.target.closest(itemSelector);
      if (!item) return;
      const { key } = event;
      if (['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(key)) {
        if (event.altKey || event.ctrlKey || event.metaKey) return;
        const next = neighbour(columns(), item, key, (el) => el.getBoundingClientRect().top);
        event.preventDefault();
        if (next && next !== item) setActive(next, true);
        return;
      }
      if (key.length === 1 && !event.altKey && !event.ctrlKey && !event.metaKey) {
        for (const handler of keyHandlers) {
          if (handler(event, item) === true) {
            event.preventDefault();
            return;
          }
        }
      }
    });

    return { refresh, setActive, onKey, active: () => active };
  }

  // Which card takes focus when card `id` leaves a view: the next one in the
  // order the view shows, or the previous one when it was last. Pure.
  function afterLeaving(order, id) {
    const ids = order.map(String);
    const at = ids.indexOf(String(id));
    if (at < 0) return null;
    return ids[at + 1] || (at > 0 ? ids[at - 1] : null) || null;
  }

  // A card the writer acted on has left the view (parked, brought back to
  // the room), and focus went with it to <body>. Hand it on: the slip's undo
  // when the slip has one, else the neighbouring card's title, else the
  // view's heading. Focus the writer has already moved elsewhere stays put.
  function focusAfterLeaving(options) {
    const doc = globalThis.document;
    const now = doc.activeElement;
    if (now && now !== doc.body && now.isConnected !== false) return null;
    const slip = options.slip && options.slip.element;
    let target = slip ? slip.querySelector('.slip-undo') : null;
    if (!target && options.neighbourId) {
      const card = options.card(options.neighbourId);
      target = card ? card.querySelector('.card-link') : null;
    }
    if (!target && options.heading) {
      if (!options.heading.hasAttribute('tabindex')) options.heading.setAttribute('tabindex', '-1');
      target = options.heading;
    }
    if (target) target.focus();
    return target;
  }

  return { createRoving, neighbour, isTyping, afterLeaving, focusAfterLeaving };
});
