// Placing mode: the keyboard route onto the board (build spec §6.5, the
// Placing board). A first-class route, not a fallback.
//
//   A (a writers likey card focused), or a click on its handle, lifts the note:
//   the card shrinks into its post-it, the post-it flies to the board, and the
//   cards below slide up into the space (flight.js).
//   Left and right move between open publish days only; taken and past ones are
//   skipped and dimmed. At either end the board pages a week. No wrap.
//   Enter or S sets it: the same round trip as a drop.
//   Escape or Tab puts it back (the post-it flies home and the card opens a
//   place for itself again), and focus returns to the handle.
//
// Focus moves to the candidate slot itself, so its two-tone ring is the focus
// ring and not a decoration. The legend strip under the board is role=status
// and carries every announcement. "A" arrives through the pool's roving-focus
// hook (keys.js onKey), which never fires while the writer types in search.
(function startPlacing() {
  const Board = window.RoomBoard;
  const Feedback = window.RoomFeedback;

  let current = null; // { id, essay, candidate, offset }
  let moving = false;

  function view() { return window.RoomBoardView; }
  function pool() { return window.RoomPool; }

  function cardSay(id, tone, text) {
    const host = pool() && pool().feedbackHost(id);
    if (host) Feedback.plaque(host, { tone, text });
  }

  function focusHandle(id) {
    const card = pool() && pool().card(id);
    const handle = card && (card.querySelector('.card-handle') || card.querySelector('.card-link'));
    if (handle) handle.focus();
  }

  // Move the candidate to a Monday and put focus on it.
  function moveTo(monday, sentence) {
    current.candidate = monday;
    moving = true;
    try {
      view().setPlacing({ essay: current.essay, candidate: monday });
      const zone = view().reveal(monday);
      if (zone) zone.focus();
    } finally {
      moving = false;
    }
    view().announce(sentence);
  }

  function lift(id) {
    const board = view();
    if (!board || !board.ready() || !pool()) return false;
    const essay = board.essay(id);
    if (!essay) return false;
    if (essay.status !== 'Writers Likey') {
      cardSay(id, 'amber', Board.say.cannotLift(essay.status));
      return true;
    }
    if (current) cancel({ quiet: true });
    const candidate = Board.firstOpen(board.today(), board.index(), board.anchor());
    if (!candidate) {
      cardSay(id, 'amber', Board.say.noOpen(board.weekdayName()));
      return true;
    }
    // Where the board was paged, so putting the note back puts the board back.
    current = { id: String(id), essay, candidate, offset: board.offset() };
    const lifted = pool().tuck(String(id));
    board.showLifted(essay.title);
    moveTo(candidate, Board.say.lifting(essay.title, candidate));
    flyUp(lifted);
    return true;
  }

  // The card's post-it flies to the Monday it is up for. The note that stays
  // on the board (the ghost) is held back until the post-it has landed on it.
  function flyUp(lifted) {
    const Flight = window.RoomFlight;
    const ghost = view().ghost();
    if (!Flight || !lifted || !lifted.note || !ghost) return;
    ghost.style.opacity = '0';
    const release = () => {
      ghost.style.transition = 'opacity 200ms ease';
      ghost.style.opacity = '';
    };
    Flight.fly(lifted.note, lifted.rect, ghost.getBoundingClientRect(), { onLanding: release })
      .then(release);
  }

  function finish() {
    if (!current) return null;
    const ended = current;
    current = null;
    moving = true;
    try {
      if (view()) {
        view().setPlacing(null);
        view().showLifted('');
      }
    } finally {
      moving = false;
    }
    return ended;
  }

  // Put the note back on its card. options: { quiet, focusHandle }
  function cancel(options) {
    const opts = options || {};
    // Where the note is on the board, before the board lets go of it: the
    // post-it flies home from there.
    const ghost = current && view().ghost();
    const from = ghost ? ghost.getBoundingClientRect() : null;
    const ended = finish();
    if (!ended) return;
    view().setOffset(ended.offset);
    view().announce('');
    pool().untuck(ended.id, { from });
    if (opts.focusHandle) focusHandle(ended.id);
    if (!opts.quiet) cardSay(ended.id, 'green', Board.say.putBack());
  }

  async function set() {
    const ended = finish();
    if (!ended) return;
    // The last step's "<day> is open." is not true once the note is set; the
    // slot's own plaque says where it landed.
    view().announce('');
    // schedule() brings the card back on its own, set or not.
    const landed = await view().schedule(ended.id, ended.candidate, { origin: 'placing' });
    if (!landed) focusHandle(ended.id);
  }

  function step(direction) {
    const board = view();
    const next = Board.stepOpen(current.candidate, direction, board.today(), board.index());
    if (!next) {
      board.announce(direction < 0 ? Board.say.noEarlier(current.candidate, board.weekdayName()) : Board.say.noOpen(board.weekdayName()));
      return;
    }
    moveTo(next, Board.say.moved(next));
  }

  // A click on a Monday while placing: set it there if it is open.
  function setAt(monday) {
    if (!current) return;
    const board = view();
    const index = board.index();
    if (Board.isOpen(monday, board.today(), index)) {
      current.candidate = monday;
      set();
      return;
    }
    const taken = (index.get(monday) || [])[0];
    board.report(monday, 'amber', taken ? Board.say.taken(monday, taken.essay.title) : Board.say.past(monday, board.weekdayName()));
    board.zone(current.candidate)?.focus();
  }

  function onKeydown(event) {
    if (!current || moving) return;
    const zone = event.target.closest && event.target.closest('#board-slots .slot-zone');
    if (!zone) return;
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const key = event.key;
    if (key === 'ArrowRight' || key === 'ArrowLeft') {
      event.preventDefault();
      step(key === 'ArrowRight' ? 1 : -1);
    } else if (key === 'Enter' || key === 's' || key === 'S') {
      event.preventDefault();
      set();
    } else if (key === 'Escape' || key === 'Tab') {
      event.preventDefault();
      cancel({ focusHandle: true });
    }
  }

  // Focus leaving the board for anywhere else puts the note back.
  function onFocusout(event) {
    if (!current || moving) return;
    if (!event.target.closest || !event.target.closest('#board-slots .slot-zone')) return;
    const next = event.relatedTarget;
    if (next && next.closest && next.closest('#board-slots .slot-zone')) return;
    cancel({ quiet: false });
  }

  function start() {
    const board = view();
    const rooms = pool();
    if (!board || !rooms || !Board || !Feedback) return;
    rooms.onKey((event, item) => {
      if (event.key !== 'a' && event.key !== 'A') return false;
      return lift(item.dataset.essayId);
    });
    const poolEl = document.getElementById('pool');
    poolEl.addEventListener('click', (event) => {
      const handle = event.target.closest('[data-action="place"]');
      if (!handle) return;
      event.preventDefault();
      const card = handle.closest('.card');
      const id = card && card.dataset.essayId;
      if (!id) return;
      lift(id);
    });
    document.addEventListener('keydown', onKeydown, true);
    document.addEventListener('focusout', onFocusout);
  }

  window.RoomPlacing = { lift, cancel, setAt, active: () => Boolean(current) };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}());
