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
  // 'archived' is not a STAGE: archived essays live outside the lifecycle and
  // outside the default /api/essays scope, so choosing it swaps the source list
  // rather than filtering the one already loaded.
  const STATE_FILTER_VALUES = ['all', ...Object.keys(STAGES), 'needs-attention', 'archived'];
  const SCOPED_STATES = { archived: 'archived' };

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
    if (value === 'archived') return essay?.status === 'Archived';
    return STAGES[value] ? essay?.status === STAGES[value] : true;
  }

  // The API scope a filter value needs, or '' when the loaded list already has
  // what it is asking for.
  function scopeForState(value) {
    return SCOPED_STATES[value] || '';
  }

  return { STATE_FILTER_VALUES, SCOPED_STATES, needsAttention, matchesState, scopeForState };
});
