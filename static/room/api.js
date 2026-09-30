// The room's fetch wrappers.
//
// Every failure comes back as a RoomApiError with a `kind`, so the interface
// can say the right sentence without reading status codes:
//   refused       airdate will not do this; `message` is the server's sentence
//   file-changed  the note changed in obsidian after the room loaded it
//   setup         setup is not finished
//   not-found     the essay is gone
//   http          anything else the server answered with an error
//   network       the server did not answer at all
(function installRoomApi(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RoomApi = api;
})(typeof globalThis === 'object' ? globalThis : this, function createRoomApi() {
  class RoomApiError extends Error {
    constructor(kind, message, status, payload) {
      super(message);
      this.name = 'RoomApiError';
      this.kind = kind;
      this.status = status || 0;
      this.payload = payload || null;
      // The refusal marker, e.g. "writers-room" or "ready-for-air".
      this.refused = payload && payload.refused ? String(payload.refused) : '';
    }
  }

  // What kind of failure a response body describes. Pure, so it can be tested.
  function classify(status, payload) {
    const body = payload && typeof payload === 'object' ? payload : {};
    if (status === 409 && body.refused) return 'refused';
    if (status === 409 && body.reason === 'file_changed') return 'file-changed';
    if (status === 409 && body.error_kind === 'setup_required') return 'setup';
    if (status === 404) return 'not-found';
    return 'http';
  }

  async function request(url, options) {
    let response;
    try {
      response = await fetch(url, { credentials: 'same-origin', ...options });
    } catch (error) {
      throw new RoomApiError('network', 'airdate is not answering.', 0, null);
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
    if (!response.ok) {
      const kind = classify(response.status, payload);
      const message = String((payload && (payload.error || payload.message)) || `airdate answered ${response.status}.`);
      throw new RoomApiError(kind, message, response.status, payload);
    }
    return payload;
  }

  function getJson(url) {
    return request(url, { method: 'GET', headers: { accept: 'application/json' } });
  }

  function postJson(url, body) {
    return request(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'application/json' },
      body: JSON.stringify(body || {}),
    });
  }

  return { RoomApiError, classify, getJson, postJson };
});
