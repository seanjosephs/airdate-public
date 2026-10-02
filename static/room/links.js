// The writer's own links, drawn in the sidebar under the room's nav.
//
// They come from config.json ("links": [{ "label", "url" }]) by way of
// /api/app/status. There is no settings page for them: a hand edit and a
// restart is how they change. Only http and https addresses are drawn, and
// each opens in a new tab so the room, and anything unsaved in it, stays put.
(function installRoomLinks(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RoomLinks = api;
})(typeof globalThis === 'object' ? globalThis : this, function createRoomLinks() {
  // An arrow leaving the page, so a link that opens another tab looks it.
  const EXTERNAL = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M7 17L17 7M9 7h8v8"/></svg>';

  // Escapes with the card helper, loaded before this file.
  function escapeHtml(value) {
    return globalThis.AirdateCards.escapeHtml(value);
  }

  // config.json asks only that a link start with http:// or https://, so a
  // space in the path is the writer's to keep: the browser encodes it. An
  // address the browser could not parse is dropped.
  function isWebAddress(url) {
    if (!/^https?:\/\//i.test(url)) return false;
    try {
      return Boolean(new URL(url));
    } catch (error) {
      return false;
    }
  }

  // config.json is the writer's own file, but a javascript: address in a link
  // is not worth the thought it would cost: only web addresses are drawn.
  function usable(links) {
    return (Array.isArray(links) ? links : [])
      .map((link) => ({
        label: link && typeof link.label === 'string' ? link.label.trim() : '',
        url: link && typeof link.url === 'string' ? link.url.trim() : '',
      }))
      .filter((link) => link.label && isWebAddress(link.url));
  }

  function markup(links) {
    return usable(links).map((link) => `<a href="${escapeHtml(link.url)}" target="_blank" rel="noopener noreferrer">`
      + `<span>${escapeHtml(link.label)}</span>${EXTERNAL}<span class="visually-hidden"> (opens in a new tab)</span></a>`).join('');
  }

  // The sidebar works without its links, so a failed status leaves the nav
  // hidden and says nothing: the pool is where a broken load is reported.
  async function start() {
    const nav = document.getElementById('room-links');
    const Api = globalThis.RoomApi;
    if (!nav || !Api) return;
    try {
      const status = await Api.getJson('/api/app/status');
      const html = markup(status && status.config && status.config.links);
      nav.innerHTML = html;
      nav.hidden = !html;
    } catch (error) {
      nav.hidden = true;
    }
  }

  if (typeof document === 'object' && document && typeof document.getElementById === 'function') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
  }

  return { usable, markup };
});
