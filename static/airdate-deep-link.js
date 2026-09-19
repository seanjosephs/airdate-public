(function installAirdateDeepLink(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AirdateDeepLink = api;
})(typeof globalThis === 'object' ? globalThis : this, function createAirdateDeepLink() {
  function resolveEssayDeepLink(search, essays) {
    const params = new URLSearchParams(search || '');
    if (!params.has('essay')) return { kind: 'none' };

    const essayId = String(params.get('essay') || '').trim();
    if (!essayId) {
      return {
        kind: 'blank',
        notice: 'The AirDate link did not include a durable essay ID. The catalog is open so you can choose an essay.',
      };
    }

    const match = (Array.isArray(essays) ? essays : []).find(
      (essay) => String(essay?.id || '') === essayId,
    );
    if (match) return { kind: 'open', essayId: String(match.id) };

    return {
      kind: 'invalid',
      essayId,
      notice: `No essay matches durable ID “${essayId}”. The catalog is open so you can choose another essay.`,
    };
  }

  async function loadEssayDeepLinkCatalog(search, activeEssays, loadAll) {
    const params = new URLSearchParams(search || '');
    if (!params.has('essay')) return Array.isArray(activeEssays) ? activeEssays : [];

    const payload = await loadAll();
    if (Array.isArray(payload)) return payload;
    return Array.isArray(payload?.essays) ? payload.essays : [];
  }

  async function handleEssayDeepLink(search, essays, adapters = {}) {
    const result = resolveEssayDeepLink(search, essays);
    if (result.kind === 'none') return result;

    adapters.openCatalog?.();
    if (result.kind !== 'open') {
      adapters.showNotice?.(result.notice, result.kind === 'blank' ? 'warn' : 'bad');
      return result;
    }

    adapters.clearNotice?.();
    try {
      await adapters.openEditor?.(result.essayId);
      return result;
    } catch (error) {
      const failed = {
        kind: 'error',
        essayId: result.essayId,
        notice: `Airdate found “${result.essayId}” but could not open its editor: ${error.message}`,
      };
      adapters.showNotice?.(failed.notice, 'bad');
      return failed;
    }
  }

  return { handleEssayDeepLink, loadEssayDeepLinkCatalog, resolveEssayDeepLink };
});
