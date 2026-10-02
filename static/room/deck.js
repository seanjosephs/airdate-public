// The deck: the scheduling board and the essay search and filters, locked at
// the top of the page so they stay in view while the pool scrolls under them.
//
// The board is sticky at the top; the pool's head (search, filters, sorts) is
// sticky just under it. The board is tall, so on a short window it would take
// the whole screen. zoomFor() works out how far the cork part of the board
// shrinks so the two together keep to a share of the window; a tall window
// keeps the board at full size. The zoom depends only on the window and on
// what the board holds, never on how far the page has scrolled, so nothing
// jumps while you scroll.
//
// CSS reads three variables set on <html>: --board-zoom (the cork's zoom),
// --deck-board-h (the board's height, which is where the pool head sticks)
// and --deck-h (both together, which scroll-padding keeps focus clear of).
(function installRoomDeck(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RoomDeck = api;
})(typeof globalThis === 'object' ? globalThis : this, function createRoomDeck() {
  // The most of the window the board and the filters may hold together.
  const SHARE = 0.5;
  // The cork never shrinks past this: smaller and the notes cannot be read.
  const FLOOR = 0.5;

  // natural: the cork's height at full size. others: everything else in the
  // deck (the board's head and legend, the pool's head, the gaps).
  function zoomFor(windowHeight, natural, others) {
    const height = Number(windowHeight);
    const cork = Number(natural);
    if (!(height > 0) || !(cork > 0)) return 1;
    const room = height * SHARE - (Number(others) || 0);
    const zoom = room / cork;
    if (!(zoom < 1)) return 1;
    return Math.max(FLOOR, Math.round(zoom * 100) / 100);
  }

  function start() {
    const html = document.documentElement;
    const board = document.getElementById('board');
    const cork = board && board.querySelector('.board-cork');
    const poolHead = document.querySelector('#pool-section .pool-head');
    if (!board || !cork || !poolHead) return;

    let queued = false;
    function measure() {
      queued = false;
      // Another view (the editor, the shelf, settings) has the page: no deck.
      if (!board.offsetParent) {
        html.style.setProperty('--deck-h', '0px');
        return;
      }
      html.style.setProperty('--board-zoom', '1');
      const natural = cork.getBoundingClientRect().height;
      const others = board.getBoundingClientRect().height - natural + poolHead.getBoundingClientRect().height;
      const zoom = zoomFor(window.innerHeight, natural, others);
      html.style.setProperty('--board-zoom', String(zoom));
      const boardHeight = Math.round(board.getBoundingClientRect().height);
      html.style.setProperty('--deck-board-h', `${boardHeight}px`);
      html.style.setProperty('--deck-h', `${boardHeight + Math.round(poolHead.getBoundingClientRect().height)}px`);
    }
    function queue() {
      if (queued) return;
      queued = true;
      window.requestAnimationFrame(measure);
    }

    window.addEventListener('resize', queue);
    window.addEventListener('hashchange', queue);
    // What the deck holds changes as the board draws and as filters wrap.
    // The board's own size is left unwatched: the zoom changes it.
    const watched = [document.getElementById('board-slots'), document.getElementById('board-legend'), poolHead];
    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(queue);
      for (const el of watched) if (el) observer.observe(el);
    }
    if (typeof MutationObserver === 'function') {
      const mutations = new MutationObserver(queue);
      for (const el of watched) if (el) mutations.observe(el, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'] });
    }
    document.addEventListener('room:essay', queue);
    queue();
  }

  if (typeof document === 'object' && document && document.addEventListener) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
  }

  return { zoomFor, SHARE, FLOOR };
});
