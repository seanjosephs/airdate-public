// The board's keyboard keys, in a dialog in the middle of the screen. The
// sidebar has no room to keep them on show; the legend under the board still
// appears (and speaks) while a note is lifted. Focus returns to the button.
(function startKeysHelp() {
  function start() {
    const open = document.getElementById('keys-open');
    const dialog = document.getElementById('keys-dialog');
    if (!open || !dialog || typeof dialog.showModal !== 'function') return;
    open.addEventListener('click', () => dialog.showModal());
    // A press on the dim area around the dialog closes it too.
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener('close', () => open.focus());
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}());
