// The catalog's state filter: the lifecycle stages plus one "needs attention".
(function installAirdateFilters(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AirdateFilters = api;
})(typeof globalThis === 'object' ? globalThis : this, function createAirdateFilters() {
  const STAGES = {
    'writers-room': 'Writers Room',
    'writers-likey': 'Writers Likey',
    'ready-for-air': 'Ready for Air',
    live: 'Live',
  };
  const STATE_FILTER_VALUES = ['all', ...Object.keys(STAGES), 'needs-attention'];

  // Needs filing, missing metadata, or missing only its hero image. A long
  // source is never sent, so its readiness does not count.
  function needsAttention(essay) {
    if (!essay) return false;
    if (essay.needs_intake) return true;
    if (essay.source_role === 'source') return false;
    const readiness = essay.publish_readiness && typeof essay.publish_readiness === 'object' ? essay.publish_readiness : {};
    return readiness.status === 'metadata' || Boolean(readiness.ready_except_image);
  }

  function matchesState(essay, value) {
    if (!value || value === 'all') return true;
    if (value === 'needs-attention') return needsAttention(essay);
    return STAGES[value] ? essay?.status === STAGES[value] : true;
  }

  return { STATE_FILTER_VALUES, needsAttention, matchesState };
});
