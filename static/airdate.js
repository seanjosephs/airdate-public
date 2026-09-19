// The writer's totems, tag presets, cadence and links arrive from
// /api/app/status (config.json on the server); nothing here is hardcoded to a
// particular writer. applyUiConfig() fills these before the first render.
let TOTEMS = {};
let TOPIC_COLORS = { _default: '#8092b0' };
let EDITOR_TOPIC_TAGS = {};
let EDITOR_ALL_TAGS = [];
const APP_CONFIG = {
  publication: '',
  publishDay: null,
  totemsEnabled: false,
  totemSlots: [],
  categoryMode: 'folders',
  links: [],
};
const WEEKDAY_INDEX = { sunday: 0, monday: 1, tuesday: 2, wednesday: 3, thursday: 4, friday: 5, saturday: 6 };

const CONTACT_KEY = 'air_date_creative_contact_v3';
const RAINY_KEY = 'air_date_rainy_day_v1';
const PLANTED_KEY = 'air_date_planted_v1';
const PLANTED_AT_KEY = 'air_date_planted_at_v1';
const GARDEN_WORK_KEY = 'air_date_garden_work_v1';
const AI_FILTER_KEY = 'air_date_all_ideas_filters_v1';
const VIEW_KEY = 'air_date_view_v1';
const VIEWS = ['all-ideas', 'shelf', 'settings'];
const STATUS_FILTER_VALUES = ['all', 'planted', 'needs-filing', 'writers-room', 'writers-likey', 'ready-for-air', 'live', 'publish-ready', 'needs-hero', 'metadata-gaps'];
const ROUTE_VIEWS = {};
const DEFAULT_AI_FILTERS = { search: '', topic: 'all', totem: 'all', status: 'all', sort: 'touched' };
const ROUTE_VIEW = viewForCurrentRoute();
const STORED_VIEW = loadJson(VIEW_KEY, 'all-ideas');

if (document.body) document.body.dataset.appRoute = ROUTE_VIEW || 'airdate';

const state = {
  essays: [],
  shelfEssays: [],
  intakeSuggestions: {},
  selectedId: null,
  view: ROUTE_VIEW || (VIEWS.includes(STORED_VIEW) ? STORED_VIEW : 'all-ideas'),
  aiFilters: (() => {
    const stored = { ...DEFAULT_AI_FILTERS, ...loadJson(AI_FILTER_KEY, DEFAULT_AI_FILTERS) };
    // Old vocabulary values (released, dormant, active, ready) no longer exist.
    if (!STATUS_FILTER_VALUES.includes(stored.status)) stored.status = 'all';
    return stored;
  })(),
  rainyDay: loadJson(RAINY_KEY, []),
  planted: loadJson(PLANTED_KEY, []),
  plantedAt: loadJson(PLANTED_AT_KEY, {}),
  gardenWork: loadJson(GARDEN_WORK_KEY, {}),
  contact: loadContactState(),
  monthRailOffset: 0,
  editorBaseline: null,
  editorFileState: null,
  editorReturnFocus: null,
};

// Migration: ids planted before tenure tracking get stamped now.
// Their order in state.planted still carries who came first.
{
  let stamped = false;
  for (const id of state.planted) {
    if (!state.plantedAt[String(id)]) {
      state.plantedAt[String(id)] = new Date().toISOString();
      stamped = true;
    }
  }
  if (stamped) saveJson(PLANTED_AT_KEY, state.plantedAt);
}

const gardenCountsEl = document.getElementById('garden-counts');
const overlayEl = document.getElementById('editor-overlay');
const editorShellEl = document.getElementById('editor-shell');
const editorForm = document.getElementById('essay-editor-form');
const editorOutput = document.getElementById('editor-output');
const editorTitle = document.getElementById('editor-title');
const editorStatusEl = document.getElementById('editor-status');
const editorContextEl = document.getElementById('editor-context');
const editorActiveTagsEl = document.getElementById('editor-active-tags');
const editorTopicPresetEl = document.getElementById('editor-topic-preset');
const editorTagDatalistEl = document.getElementById('editor-tag-options');
const railSoonStatusEl = document.getElementById('rail-soon-status');
const settingsStatusEl = document.getElementById('settings-action-status');
const deepLinkNoticeEl = document.getElementById('deep-link-notice');

const EDITOR_CHECKBOX_FIELDS = ['publish_on_web', 'send_email', 'free_preview'];
const EDITOR_ARRAY_FIELDS = ['tags', 'test_email_recipients'];
const EDITOR_SAVE_REQUIRED_FIELDS = ['title'];
const EDITOR_SEND_REQUIRED_FIELDS = ['title', 'subtitle', 'email_subject', 'email_preview_text'];

const ICONS = {
  obsidian: `<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M12 2 4 9l3 13h10l3-13z"/><path d="M12 2 7 22"/><path d="M12 2 17 22"/><path d="M4 9h16"/></svg>`,
  substack: `<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="currentColor"><rect x="4" y="4" width="16" height="2.6"/><rect x="4" y="8.6" width="16" height="2.6"/><path d="M4 13.2h16v8.6l-8-4.6-8 4.6z"/></svg>`,
  crystal: `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><circle cx="12" cy="13" r="6"/><path d="M9 11.5c0.8-1.2 2.4-2 4-2"/><path d="M6 19h12"/><path d="M10 7l2-3 2 3"/></svg>`,
  umbrella: `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 18 0z"/><path d="M12 12v6a2 2 0 0 0 4 0"/><path d="M12 3v3"/></svg>`,
  feather: `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M20 4c-7 1-12 6-13 13l-3 3"/><path d="M14 4l6 6"/><path d="M9 14h6"/></svg>`,
  sprout: `<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M12 21v-9"/><path d="M12 12C12 8 9 6 4 6c0 4 3 6 8 6z"/><path d="M12 13c0-3.3 2.7-5 7-5 0 3.3-2.7 5-7 5z"/></svg>`,
  uproot: `<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M12 3v10"/><path d="M8 7l4-4 4 4"/><path d="M5 13h14l-1.4 6.2a2 2 0 0 1-2 1.8H8.4a2 2 0 0 1-2-1.8z"/></svg>`,
  draft: `<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h5"/><path d="M9 14h6"/><path d="M12 11v6"/></svg>`,
  image: `<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="m6.8 17 4.2-4 3 2.8 2.1-2 3.1 3.2"/></svg>`,
};

function applyUiConfig(config) {
  const cfg = config || {};
  APP_CONFIG.publication = String(cfg.publication || '');
  APP_CONFIG.publishDay = WEEKDAY_INDEX[cfg.publish_day] !== undefined ? cfg.publish_day : null;
  APP_CONFIG.categoryMode = cfg.category_mode === 'off' ? 'off' : 'folders';
  APP_CONFIG.links = Array.isArray(cfg.links) ? cfg.links : [];
  const totems = cfg.totems || {};
  APP_CONFIG.totemsEnabled = Boolean(totems.enabled);
  APP_CONFIG.totemSlots = Array.isArray(totems.slots) ? totems.slots : [];
  TOTEMS = {};
  for (const item of (APP_CONFIG.totemsEnabled && Array.isArray(totems.items) ? totems.items : [])) {
    TOTEMS[item.key] = {
      label: item.label || item.key,
      role: item.role || '',
      asset: item.image || '',
      color: item.color || TOPIC_COLORS._default,
    };
  }
  TOPIC_COLORS = { _default: '#8092b0' };
  EDITOR_TOPIC_TAGS = {};
  for (const preset of (Array.isArray(cfg.tag_presets) ? cfg.tag_presets : [])) {
    if (!preset || !preset.name) continue;
    EDITOR_TOPIC_TAGS[preset.name] = Array.isArray(preset.tags) ? preset.tags : [];
    if (preset.color) TOPIC_COLORS[preset.name] = preset.color;
  }
  EDITOR_ALL_TAGS = [...new Set(Object.values(EDITOR_TOPIC_TAGS).flat())].sort();
  document.body.dataset.totems = APP_CONFIG.totemsEnabled ? 'on' : 'off';
  document.body.dataset.presets = Object.keys(EDITOR_TOPIC_TAGS).length ? 'on' : 'off';
  renderRailLinks();
  renderMonthRailChrome();
  populateEditorTotemSelect();
  populateEditorTagControls();
  populateAllIdeasFilters();
}

function populateEditorTotemSelect() {
  const field = document.getElementById('editor-totem-field');
  const select = field?.querySelector('select[name="totem"]');
  if (!field || !select) return;
  field.classList.toggle('hidden', !APP_CONFIG.totemsEnabled);
  select.innerHTML = '<option value="">(unchanged)</option>'
    + Object.entries(TOTEMS).map(([key, t]) => `<option value="${escapeHtml(key)}">${escapeHtml(t.label.toLowerCase())}</option>`).join('');
}

function totemFor(essay) {
  return TOTEMS[essay?.totem] || null;
}

// Card styling for an essay's totem; no totem falls back to the topic color
// and no portrait.
function totemStyle(totem, fallbackColor) {
  const color = totem?.color || fallbackColor || TOPIC_COLORS._default;
  return `--accent:${color};${totem?.asset ? ` --totem-art:url('${totem.asset}');` : ''}`;
}

function totemPortraitMarkup(totem) {
  return totem?.asset ? `<div class="gc-portrait" aria-hidden="true"><img src="${escapeHtml(totem.asset)}" alt=""></div>` : '';
}

function renderRailLinks() {
  const el = document.getElementById('rail-links');
  if (!el) return;
  el.innerHTML = APP_CONFIG.links
    .filter((link) => /^https?:\/\//.test(String(link.url || '')))
    .map((link) => `<a href="${escapeHtml(link.url)}" target="_blank" rel="noopener">${escapeHtml(link.label)} →</a>`)
    .join('');
}

function renderMonthRailChrome() {
  const rail = document.getElementById('month-rail');
  if (rail) rail.classList.toggle('hidden', !APP_CONFIG.publishDay);
  const note = document.getElementById('month-rail-note');
  if (note && APP_CONFIG.publishDay) {
    note.textContent = `one slot per week · drag an essay to schedule it (publishes ${APP_CONFIG.publishDay})`;
  }
}

function viewForCurrentRoute() {
  const path = window.location.pathname.replace(/\/+$/, '') || '/';
  const view = ROUTE_VIEWS[path] || '';
  return VIEWS.includes(view) ? view : '';
}

function loadJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function saveJson(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}



function todayIso() {
  return new Date().toISOString().slice(0, 10);
}



function loadContactState() {
  const fallback = { date: todayIso(), uniqueEssayIds: [] };
  const current = loadJson(CONTACT_KEY, fallback);
  if (current.date !== todayIso()) return fallback;
  return {
    date: current.date,
    uniqueEssayIds: Array.isArray(current.uniqueEssayIds) ? current.uniqueEssayIds : [],
  };
}

function saveAiFilters() {
  saveJson(AI_FILTER_KEY, state.aiFilters);
}

function resetAiFilters() {
  state.aiFilters = { ...DEFAULT_AI_FILTERS };
  saveAiFilters();
  const search = document.getElementById('ai-search');
  if (search) search.value = '';
  for (const [id, value] of [['ai-topic', 'all'], ['ai-totem', 'all'], ['ai-status', 'all'], ['ai-sort', 'touched']]) {
    const el = document.getElementById(id);
    if (el) el.value = value;
  }
}

function switchView(view, options = {}) {
  if (!VIEWS.includes(view)) return;
  state.view = view;
  if (options.persist !== false) saveJson(VIEW_KEY, view);
  const workspace = document.querySelector('.garden-workspace');
  if (workspace) workspace.dataset.view = view;
  document.querySelectorAll('.rail-tile[data-view]').forEach((tile) => {
    tile.classList.toggle('active', tile.dataset.view === view);
  });
  renderActiveView();
}

function populateAllIdeasFilters() {
  const topicSel = document.getElementById('ai-topic');
  topicSel?.closest('.select-box')?.classList.toggle('hidden', !Object.keys(EDITOR_TOPIC_TAGS).length);
  document.getElementById('ai-totem')?.closest('.select-box')?.classList.toggle('hidden', !APP_CONFIG.totemsEnabled);
  if (!Object.keys(EDITOR_TOPIC_TAGS).length) state.aiFilters.topic = 'all';
  if (!APP_CONFIG.totemsEnabled || (state.aiFilters.totem !== 'all' && !TOTEMS[state.aiFilters.totem])) state.aiFilters.totem = 'all';
  if (topicSel) {
    const topics = Object.keys(TOPIC_COLORS).filter((t) => t !== '_default');
    topicSel.innerHTML = '<option value="all">all topics</option>'
      + topics.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t.toLowerCase())}</option>`).join('');
    topicSel.value = state.aiFilters.topic;
  }
  const totemSel = document.getElementById('ai-totem');
  if (totemSel) {
    totemSel.innerHTML = '<option value="all">all totems</option>'
      + Object.entries(TOTEMS).map(([key, t]) => `<option value="${escapeHtml(key)}">${escapeHtml(t.label.toLowerCase())}</option>`).join('');
    totemSel.value = state.aiFilters.totem;
  }
  const sortSel = document.getElementById('ai-sort');
  if (sortSel) sortSel.value = state.aiFilters.sort;
  const statusSel = document.getElementById('ai-status');
  if (statusSel) statusSel.value = state.aiFilters.status || 'all';
}

async function getJson(path) {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.message || payload.error || 'Request failed');
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

async function postJson(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.message || payload.error || 'Request failed');
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function setSettingsStatus(message, kind = '') {
  if (!settingsStatusEl) return;
  settingsStatusEl.textContent = message || '';
  settingsStatusEl.dataset.kind = kind;
}

function setSettingValue(id, value, kind = '') {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = value == null || value === '' ? 'not set' : String(value);
  el.dataset.kind = kind;
}

function substackSessionLabel(substack = {}) {
  return substack.connected
    ? (substack.session_source || 'connected').replace(/_/g, ' ')
    : 'not connected';
}

// One connection truth, one sentence: "connected through Obsidian since {date}".
function connectionStateLabel(substack = {}) {
  if (!substack.connected) return 'not connected';
  const since = String(substack.connector?.connected_at || '').trim();
  if (!since) return 'connected through Obsidian';
  const parsed = new Date(since);
  const display = Number.isNaN(parsed.getTime())
    ? since
    : parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  return `connected through Obsidian since ${display}`;
}

function renderSubstackConnection(substack = {}) {
  const kind = substack.connected ? 'good' : 'warn';
  const label = substackSessionLabel(substack);
  const connectionLabel = connectionStateLabel(substack);
  setSettingValue('settings-substack-session', label, kind);
  setSettingValue('settings-substack-cookie', connectionLabel, kind);
  setSettingValue('editor-substack-session', connectionLabel, kind);
  for (const id of ['settings-refresh-substack', 'editor-refresh-substack']) {
    const button = document.getElementById(id);
    if (!button) continue;
    button.disabled = !!substack.connected;
    button.textContent = substack.connected ? 'Substack connected' : 'connection required';
    button.classList.toggle('is-connected', !!substack.connected);
    button.classList.toggle('is-required', !substack.connected);
  }
}

async function refreshSubstackConnectionStatus() {
  const status = await getJson('/api/substack/status');
  renderSubstackConnection(status || {});
  return status;
}

function formatSettingsTime(value) {
  if (!value) return 'not scanned yet';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function currentSettingsUrl() {
  const configured = document.getElementById('settings-app-url')?.textContent?.trim();
  if (configured && configured !== 'checking...' && configured !== 'not set') return configured;
  return `${window.location.origin}/airdate`;
}

async function copyText(value) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.append(textarea);
  textarea.select();
  document.execCommand('copy');
  textarea.remove();
}




function touchEssay(essayId, action) {
  if (state.contact.date !== todayIso()) {
    state.contact = { date: todayIso(), uniqueEssayIds: [] };
  }
  if (!state.contact.uniqueEssayIds.includes(essayId)) {
    state.contact.uniqueEssayIds.push(essayId);
    saveJson(CONTACT_KEY, state.contact);
    fetch(`/api/essays/${essayId}/contact`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ action }),
    }).catch(() => {});
  }
}


function isPlanted(essayId) {
  return state.planted.includes(String(essayId));
}

function plantEssay(essayId) {
  const id = String(essayId);
  if (!state.planted.includes(id)) {
    state.planted.push(id);
    state.plantedAt[id] = new Date().toISOString();
    saveJson(PLANTED_AT_KEY, state.plantedAt);
  }
  // Planting pulls an idea out of resting season.
  state.rainyDay = state.rainyDay.filter((rid) => String(rid) !== id);
  saveJson(PLANTED_KEY, state.planted);
  saveJson(RAINY_KEY, state.rainyDay);
  touchEssay(id, 'plant');
  renderAll();
}

function demoteFromGarden(essayId) {
  // Leaving the garden resets the piece's standing: tenure and writing
  // accesses start over if it ever gets planted again.
  const id = String(essayId);
  state.planted = state.planted.filter((pid) => String(pid) !== id);
  delete state.plantedAt[id];
  delete state.gardenWork[id];
  saveJson(PLANTED_KEY, state.planted);
  saveJson(PLANTED_AT_KEY, state.plantedAt);
  saveJson(GARDEN_WORK_KEY, state.gardenWork);
}

function unplantEssay(essayId) {
  demoteFromGarden(essayId);
  renderAll();
}

function recordGardenAccess(essayId) {
  // A writing access: opening a planted piece in the editor. Counts at
  // most once per day per piece, and feeds its size in the garden.
  const id = String(essayId);
  if (!isPlanted(id)) return;
  const entry = state.gardenWork[id] || { count: 0, last: '' };
  if (entry.last === todayIso()) return;
  entry.count += 1;
  entry.last = todayIso();
  state.gardenWork[id] = entry;
  saveJson(GARDEN_WORK_KEY, state.gardenWork);
}

function searchHay(essay) {
  if (essay.search_blob) return essay.search_blob;
  return [
    essay.title,
    essay.subtitle,
    essay.summary,
    (essay.tags || []).join(' '),
    (essay.themes || []).join(' '),
    essay.totem,
  ].join(' ').toLowerCase();
}

function matchesSearch(essay, query) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return true;
  return q.split(/\s+/).every((term) => searchHay(essay).includes(term));
}

function formatWords(n) {
  const w = Number(n) || 0;
  if (w >= 1000) {
    return `${(w / 1000).toFixed(1).replace(/\.0$/, '')}k words`;
  }
  return `${w} words`;
}

function parseDaysAgo(s) {
  const m = /(\d+)d ago/.exec(String(s || ''));
  return m ? parseInt(m[1], 10) : 999;
}


function freshnessFor(essay) {
  const days = parseDaysAgo(essay.last_touched_human);
  if (days < 1) return 'newborn';
  if (days < 8) return 'active';
  if (days < 31) return 'settling';
  if (days < 91) return 'dormant';
  return 'weathered';
}

function topicForEssay(essay) {
  const tags = (essay.tags || []).map((t) => String(t).toLowerCase().trim()).filter(Boolean);
  if (!tags.length) return '_default';
  let best = '_default';
  let bestHits = 0;
  for (const [topic, list] of Object.entries(EDITOR_TOPIC_TAGS)) {
    const lowerList = list.map((t) => t.toLowerCase());
    const hits = tags.filter((t) => lowerList.includes(t)).length;
    if (hits > bestHits) {
      bestHits = hits;
      best = topic;
    }
  }
  return best;
}

function topicColor(topic) {
  return TOPIC_COLORS[topic] || TOPIC_COLORS._default;
}

function sourceRoleLabel(essay) {
  if (essay.source_role === 'source') return essay.is_long_source ? 'long source' : 'source';
  if (essay.source_role === 'draft') return 'draft';
  return 'standalone';
}

function relationshipText(essay) {
  if (essay.source_role === 'source') {
    const count = Number(essay.linked_draft_count || 0);
    return count ? `${count} linked draft${count === 1 ? '' : 's'}` : 'no linked drafts';
  }
  if (essay.source_role === 'draft' && essay.draft_of) return `from ${essay.draft_of}`;
  return '';
}

function roleBadgesMarkup(essay, className = 'card-badge') {
  const badges = [`<span class="${className}" data-role="${essay.source_role || 'standalone'}">${sourceRoleLabel(essay)}</span>`];
  if (essay.source_role === 'source' && Number(essay.linked_draft_count || 0) > 0) {
    badges.push(`<span class="${className}" data-role="linked">${essay.linked_draft_count} draft${Number(essay.linked_draft_count) === 1 ? '' : 's'}</span>`);
  }
  return badges.join('');
}

function readinessForEssay(essay) {
  return essay.publish_readiness && typeof essay.publish_readiness === 'object'
    ? essay.publish_readiness
    : {};
}

function readinessBadgeMarkup(essay, className = 'card-badge') {
  const readiness = readinessForEssay(essay);
  if (!readiness.status || essay.source_role === 'source') return '';
  return `<span class="${className}" data-readiness="${readiness.status}">${readiness.label || 'check readiness'}</span>`;
}

function readinessDetailText(essay) {
  const readiness = readinessForEssay(essay);
  if (essay.source_role === 'source') return '';
  if (readiness.status === 'ready') return 'all Substack metadata is filled.';
  if (readiness.status === 'image') return 'metadata complete; add hero image.';
  const missing = Array.isArray(readiness.missing_metadata) ? readiness.missing_metadata : [];
  if (missing.length) return `missing: ${missing.slice(0, 3).join(', ')}${missing.length > 3 ? '…' : ''}`;
  return '';
}

function needsHeroImage(essay) {
  const readiness = readinessForEssay(essay);
  return essay.source_role !== 'source' && readiness.image_complete === false;
}

function attachHeroButtonMarkup(essay, className = 'card-icon') {
  if (!needsHeroImage(essay)) return '';
  return `<button class="${className} attach-hero-icon" type="button" data-attach-hero-id="${essay.id}" title="Attach hero image" aria-label="Attach hero image">${ICONS.image}</button>`;
}


async function createLinkedDraftFromSource(essayId, options = {}) {
  if (!essayId) return null;
  setEditorStatus('creating linked draft...', 'pending');
  const bodyField = editorForm?.elements?.body;
  const selectedText = options.selectedText
    ?? (state.selectedId === essayId && bodyField
      ? bodyField.value.slice(bodyField.selectionStart || 0, bodyField.selectionEnd || 0)
      : '');
  const source = state.essays.find((essay) => String(essay.id) === String(essayId));
  const title = source?.title ? `${source.title} - Draft` : '';
  const payload = await postJson(`/api/essays/${essayId}/create-draft`, { title, selected_text: selectedText });
  await loadEssays();
  if (payload?.draft_essay_id) await openEditor(payload.draft_essay_id);
  setEditorStatus('linked draft created in Obsidian.', 'good');
  return payload;
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function pickHeroImageFile() {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/gif,image/jpeg,image/png,image/webp';
    input.style.position = 'fixed';
    input.style.left = '-9999px';
    document.body.append(input);
    let settled = false;
    const done = (file = null) => {
      if (settled) return;
      settled = true;
      input.remove();
      resolve(file);
    };
    input.addEventListener('change', () => done(input.files?.[0] || null), { once: true });
    input.addEventListener('cancel', () => done(null), { once: true });
    input.click();
  });
}

function syncEditorHeroAttach(result) {
  if (String(state.selectedId || '') !== String(result?.essay_id || '')) return;
  const updates = result.updates || {};
  for (const field of ['hero_image', 'social_image', 'thumbnail_alt']) {
    if (updates[field] && editorForm.elements[field]) {
      editorForm.elements[field].value = updates[field];
      if (state.editorBaseline) state.editorBaseline[field] = updates[field];
    }
  }
  if (result.saved) {
    state.editorFileState = {
      mtime: result.saved.mtime,
      content_hash: result.saved.content_hash,
    };
  }
  markEditorField('hero_image', false);
  markEditorField('social_image', false);
}

async function attachHeroImage(essayId, file, options = {}) {
  const setStatus = options.setStatus || setEditorStatus;
  if (!essayId || !file) return null;
  setStatus(`attaching ${file.name}...`, 'pending');
  try {
    const payload = {
      filename: file.name,
      dataUrl: await readFileAsDataUrl(file),
      also_social: true,
    };
    if (String(state.selectedId || '') === String(essayId) && !overlayEl.classList.contains('hidden')) {
      Object.assign(payload, expectedFileStatePayload());
    }
    const result = await postJson(`/api/essays/${essayId}/attach-hero`, payload);
    // Must stay BEFORE the remap: syncEditorHeroAttach matches on the request id.
    syncEditorHeroAttach(result);
    // This save can mint the durable id, changing the essay's id. Remap with the
    // id the server reported rather than the stale closure argument, or the dead
    // hash gets re-seeded into local state after the catalog already moved on.
    const canonicalId = String(result?.saved?.new_id || essayId);
    if (result?.saved?.old_id && String(result.saved.old_id) !== canonicalId) {
      remapEssayId(result.saved.old_id, canonicalId);
    }
    await loadEssays();
    touchEssay(canonicalId, 'attach_hero');
    const message = 'hero image attached and saved to Obsidian.';
    setStatus(message, 'good');
    showRailSoonStatus(message);
    return result;
  } catch (err) {
    if (err.status === 409 && !overlayEl.classList.contains('hidden')) {
      showEditorConflict(err);
    } else {
      setStatus(`hero attach failed: ${err.message}`, 'bad');
      showRailSoonStatus(`hero attach failed: ${err.message}`);
    }
    return null;
  }
}

async function pickAndAttachHero(essayId, setStatus = setEditorStatus) {
  const file = await pickHeroImageFile();
  if (!file) return null;
  return attachHeroImage(essayId, file, { setStatus });
}

function wireCardInteractions(container) {
  container.querySelectorAll('[data-essay-id][draggable="true"]').forEach((card) => {
    const essayId = card.dataset.essayId;
    card.addEventListener('dragstart', (event) => {
      event.dataTransfer.setData('essayId', essayId);
      card.classList.add('dragging');
      // A small drag ghost so the drop target stays visible under the cursor
      // instead of being covered by a full-size card.
      const ghost = document.createElement('div');
      ghost.className = 'drag-ghost';
      ghost.textContent = card.querySelector('.gc-title')?.textContent || 'essay';
      document.body.appendChild(ghost);
      event.dataTransfer.setDragImage(ghost, 12, 12);
      setTimeout(() => ghost.remove(), 0);
    });
    card.addEventListener('dragend', () => card.classList.remove('dragging'));
  });
  container.querySelectorAll('[data-open-id]').forEach((button) => {
    button.addEventListener('click', (event) => { event.stopPropagation(); openEditor(button.dataset.openId); });
  });
  container.querySelectorAll('[data-create-draft-id]').forEach((button) => {
    button.addEventListener('click', async (event) => {
      event.stopPropagation();
      try {
        await createLinkedDraftFromSource(button.dataset.createDraftId);
      } catch (err) {
        setEditorStatus(`draft creation failed: ${err.message}`, 'bad');
      }
    });
  });
  container.querySelectorAll('[data-attach-hero-id]').forEach((button) => {
    button.addEventListener('click', async (event) => {
      event.stopPropagation();
      await pickAndAttachHero(button.dataset.attachHeroId, (message) => showRailSoonStatus(message));
    });
  });
  container.querySelectorAll('[data-plant-id]').forEach((button) => {
    button.addEventListener('click', (event) => { event.stopPropagation(); plantEssay(button.dataset.plantId); });
  });
  container.querySelectorAll('[data-unplant-id]').forEach((button) => {
    button.addEventListener('click', (event) => { event.stopPropagation(); unplantEssay(button.dataset.unplantId); });
  });
  container.querySelectorAll('[data-rainy-id]').forEach((button) => {
    button.addEventListener('click', (event) => { event.stopPropagation(); moveToRainy(button.dataset.rainyId); });
  });
  // Links inside clickable cards (e.g. Obsidian) must not also open the editor
  container.querySelectorAll('.gc-actions a, .shelf-link').forEach((link) => {
    link.addEventListener('click', (event) => event.stopPropagation());
  });
  wireLifecycleControls(container);
}

// The lifecycle is action-driven, not a hand-set dropdown. Every essay that
// exists is Writers Room by default; the Ready for Air button schedules it and
// unlocks send; send flips it to Live; the publish/archive buttons take it the
// rest of the way. The status is shown as a read-only chip.
// Send to Substack is only reachable once an essay is scheduled.
const SEND_ENABLED_STATUSES = ['Ready for Air', 'Live'];

function lifecycleControlsMarkup(essay, cls = 'card-lifecycle') {
  const current = essay.status || 'Writers Room';
  const btns = [];
  if (current === 'Writers Room') {
    // Writing done → move to the schedulable pool (no date yet).
    btns.push(`<button class="lifecycle-btn likey-btn" type="button" data-likey-id="${essay.id}" title="Mark done and ready to schedule; moves it to Writers Likey">ready to schedule</button>`);
  } else if (current === 'Writers Likey') {
    // In the pool: give it a date (or drag it onto a calendar week).
    btns.push(`<button class="lifecycle-btn rfa-btn" type="button" data-rfa-id="${essay.id}" title="Set an air date; schedules it on the calendar and unlocks send">ready for air</button>`);
    btns.push(`<button class="lifecycle-btn back-btn" type="button" data-room-id="${essay.id}" title="Back to the writers room">back to room</button>`);
  } else if (current === 'Ready for Air') {
    const when = essay.scheduled_at ? ` <span class="rfa-when">${escapeHtml(formatPublishedDate(essay.scheduled_at) || essay.scheduled_at)}</span>` : '';
    btns.push(`<button class="lifecycle-btn rfa-btn" type="button" data-rfa-id="${essay.id}" title="Change the air date">reschedule${when}</button>`);
    btns.push(`<button class="lifecycle-btn back-btn" type="button" data-unschedule-id="${essay.id}" title="Clear the air date and move it back to Writers Likey">unschedule</button>`);
  } else if (current === 'Live') {
    // Publish is the record-a-Substack-publish action; it only makes sense once
    // the draft has actually gone out (Live).
    btns.push(`<button class="lifecycle-btn publish-btn" type="button" data-publish-id="${essay.id}" title="Mark published on Substack">publish</button>`);
  }
  if (current !== 'Archived') {
    btns.push(`<button class="lifecycle-btn archive-btn" type="button" data-archive-id="${essay.id}" title="Move to Archive">archive</button>`);
  }
  return `<div class="${cls}" data-status="${escapeHtml(current)}">
    <span class="card-status-chip" data-status="${escapeHtml(current)}">${escapeHtml(current.toLowerCase())}</span>
    ${btns.join('')}
  </div>`;
}

async function readyForAirFlow(essayId) {
  const essay = state.essays.find((e) => String(e.id) === String(essayId));
  const fallback = essay?.scheduled_at ? String(essay.scheduled_at).slice(0, 10) : todayIso();
  const when = window.prompt(
    `Air date for “${essay?.title || 'this essay'}” (YYYY-MM-DD). This schedules it on the calendar and unlocks send:`,
    fallback,
  );
  if (when === null) return;
  const date = when.trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    showRailSoonStatus('air date needs to be YYYY-MM-DD.');
    return;
  }
  try {
    const result = await postJson(`/api/essays/${essayId}/ready-for-air`, { scheduled_at: date });
    await handleLifecycleResult(result, `ready for air, scheduled ${date}`);
  } catch (err) {
    showRailSoonStatus(`ready for air failed: ${err.message}`);
    await loadEssays();
  }
}

function remapEssayId(oldId, newId) {
  // Essay ids derive from the file path, so a publish/archive/intake move
  // mints a new id. Every piece of local state keyed by id has to follow, or
  // it silently orphans (gardenWork/contact used to be missed here).
  const from = String(oldId);
  const to = String(newId);
  if (!from || !to || from === to) return;
  state.planted = state.planted.map((id) => (String(id) === from ? to : id));
  state.rainyDay = state.rainyDay.map((id) => (String(id) === from ? to : id));
  if (state.plantedAt[from]) {
    state.plantedAt[to] = state.plantedAt[from];
    delete state.plantedAt[from];
  }
  if (state.gardenWork[from]) {
    state.gardenWork[to] = state.gardenWork[from];
    delete state.gardenWork[from];
  }
  if (Array.isArray(state.contact.uniqueEssayIds)) {
    state.contact.uniqueEssayIds = state.contact.uniqueEssayIds.map((id) => (String(id) === from ? to : id));
  }
  if (String(state.selectedId || '') === from) state.selectedId = to;
  saveJson(PLANTED_KEY, state.planted);
  saveJson(PLANTED_AT_KEY, state.plantedAt);
  saveJson(RAINY_KEY, state.rainyDay);
  saveJson(GARDEN_WORK_KEY, state.gardenWork);
  saveJson(CONTACT_KEY, state.contact);
}

async function handleLifecycleResult(result, message) {
  if (result?.old_id && result?.new_id && result.old_id !== result.new_id) {
    remapEssayId(result.old_id, result.new_id);
  }
  const note = result?.warning ? `${message}. ${result.warning}` : message;
  showRailSoonStatus(note);
  await loadEssays();
  if (state.view === 'shelf') await renderShelf({ refresh: true });
}

async function publishEssayFlow(essayId) {
  const essay = state.essays.find((e) => String(e.id) === String(essayId))
    || state.shelfEssays.find((e) => String(e.id) === String(essayId));
  const url = window.prompt(
    `Live Substack URL for “${essay?.title || 'this essay'}”. Paste the real post link:`,
    essay?.substack_url || (APP_CONFIG.publication ? `${APP_CONFIG.publication.replace(/\/+$/, '')}/p/` : 'https://'),
  );
  if (!url || !/^https?:\/\//.test(url.trim()) || url.trim().endsWith('/p/')) {
    if (url !== null) showRailSoonStatus('publish needs the full live post URL.');
    return;
  }
  try {
    const result = await postJson(`/api/essays/${essayId}/publish`, { substack_url: url.trim() });
    await handleLifecycleResult(result, 'published and moved to the shelf');
  } catch (err) {
    showRailSoonStatus(`publish failed: ${err.message}`);
  }
}

async function setEssayStatusFlow(essayId, status, message) {
  try {
    const result = await postJson(`/api/essays/${essayId}/set-status`, { status });
    await handleLifecycleResult(result, message);
  } catch (err) {
    showRailSoonStatus(`status change failed: ${err.message}`);
    await loadEssays();
  }
}

async function archiveEssayFlow(essayId) {
  const essay = state.essays.find((e) => String(e.id) === String(essayId));
  if (!window.confirm(`Archive “${essay?.title || 'this essay'}”? It moves to Archive/ and leaves the catalog.`)) return;
  try {
    const result = await postJson(`/api/essays/${essayId}/archive`, {});
    await handleLifecycleResult(result, 'archived');
  } catch (err) {
    showRailSoonStatus(`archive failed: ${err.message}`);
  }
}

function wireLifecycleControls(container) {
  container.querySelectorAll('[data-likey-id]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      setEssayStatusFlow(button.dataset.likeyId, 'Writers Likey', 'ready to schedule');
    });
  });
  container.querySelectorAll('[data-room-id]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      setEssayStatusFlow(button.dataset.roomId, 'Writers Room', 'back in the writers room');
    });
  });
  container.querySelectorAll('[data-rfa-id]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      readyForAirFlow(button.dataset.rfaId);
    });
  });
  container.querySelectorAll('[data-unschedule-id]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      unscheduleEssayFlow(button.dataset.unscheduleId);
    });
  });
  container.querySelectorAll('[data-publish-id]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      publishEssayFlow(button.dataset.publishId);
    });
  });
  container.querySelectorAll('[data-archive-id]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      archiveEssayFlow(button.dataset.archiveId);
    });
  });
}

function plantedEssays() {
  // Preserve plant order (most recently planted last → newest tended last).
  const byId = new Map(state.essays.map((e) => [String(e.id), e]));
  return state.planted.map((id) => byId.get(String(id))).filter(Boolean);
}















function gardenCardTier(essay) {
  // Size mirrors how actively the idea is being worked.
  const days = parseDaysAgo(essay.last_touched_human);
  if (days <= 3) return 'lg';
  if (days <= 21) return 'md';
  return 'sm';
}

function gardenCardMarkup(essay, opts = {}) {
  const totem = totemFor(essay);
  const topic = topicForEssay(essay);
  const accent = topicColor(topic);
  const tier = gardenCardTier(essay);
  const freshness = freshnessFor(essay);
  const keywords = (essay.tags || []).slice(0, 3).join(', ');
  const topicLabel = topic === '_default' ? 'general' : topic.toLowerCase();
  let actions = '';
  if (opts.actions || opts.gardenActions) {
    const planted = isPlanted(essay.id);
    const plantBtn = planted
      ? `<button class="gc-icon" type="button" data-unplant-id="${essay.id}" title="Remove from calendar queue" aria-label="Remove from calendar queue">${ICONS.uproot}</button>`
      : `<button class="gc-icon" type="button" data-plant-id="${essay.id}" title="Queue for calendar" aria-label="Queue for calendar">${ICONS.sprout}</button>`;
    const rainyBtn = planted
      ? `<button class="gc-icon" type="button" data-rainy-id="${essay.id}" title="Set aside" aria-label="Set aside">${ICONS.umbrella}</button>`
      : '';
    actions = `<div class="gc-actions">
      ${essay.obsidian_url ? `<a class="gc-icon" href="${essay.obsidian_url}" title="Open in Obsidian" aria-label="Open in Obsidian">${ICONS.obsidian}</a>` : ''}
      ${essay.source_role === 'source' ? `<button class="gc-icon" type="button" data-create-draft-id="${essay.id}" title="Create linked draft" aria-label="Create linked draft">${ICONS.draft}</button>` : ''}
      ${attachHeroButtonMarkup(essay, 'gc-icon')}
      ${rainyBtn}
      ${plantBtn}
    </div>`;
  }
  const relation = relationshipText(essay);
  const readiness = readinessDetailText(essay);
  const area = opts.area ? ` grid-area:${opts.area};` : '';
  const rank = opts.rank ? ` data-rank="${opts.rank}"` : '';
  return `<article class="garden-card" data-tier="${tier}" data-freshness="${freshness}"${rank}
    data-totem="${escapeHtml(essay.totem || '')}" data-topic="${escapeHtml(topic)}" data-archetype="" data-source-role="${essay.source_role || 'standalone'}" draggable="true"
    data-essay-id="${essay.id}" data-open-id="${essay.id}"
    style="${totemStyle(totem, accent)} --topic-color:${accent};${area}" title="${escapeHtml(essay.title)}">
    ${totemPortraitMarkup(totem)}
    ${actions}
    <div class="gc-content">
      <div class="gc-badges">${roleBadgesMarkup(essay, 'gc-badge')}${readinessBadgeMarkup(essay, 'gc-badge')}</div>
      <h3 class="gc-title">${escapeHtml(essay.title)}</h3>
      ${relation ? `<p class="gc-relation">${relation}</p>` : ''}
      ${readiness ? `<p class="gc-readiness">${readiness}</p>` : ''}
      ${keywords ? `<p class="gc-keywords">${keywords}</p>` : ''}
      ${opts.actions ? lifecycleControlsMarkup(essay, 'gc-lifecycle') : ''}
    </div>
    <footer class="gc-foot"><span>${topicLabel}</span><span>${formatWords(essay.word_count)}</span><span>${essay.last_touched_human || 'recently'}</span></footer>
  </article>`;
}


function renderAllIdeas() {
  const container = document.getElementById('all-ideas-cards');
  const countEl = document.getElementById('all-ideas-count');
  if (!container) return;
  const f = state.aiFilters;
  const status = f.status || 'all';
  let essays = state.essays.filter((essay) => {
    if (f.topic !== 'all' && topicForEssay(essay) !== f.topic) return false;
    if (f.totem !== 'all' && essay.totem !== f.totem) return false;
    if (status === 'planted' && !isPlanted(essay.id)) return false;
    if (status === 'needs-filing' && !essay.needs_intake) return false;
    if (status === 'writers-room' && essay.status !== 'Writers Room') return false;
    if (status === 'writers-likey' && essay.status !== 'Writers Likey') return false;
    if (status === 'ready-for-air' && essay.status !== 'Ready for Air') return false;
    if (status === 'live' && essay.status !== 'Live') return false;
    if (status === 'publish-ready' && !readinessForEssay(essay).ready_to_send) return false;
    if (status === 'needs-hero' && !readinessForEssay(essay).ready_except_image) return false;
    if (status === 'metadata-gaps' && readinessForEssay(essay).status !== 'metadata') return false;
    return matchesSearch(essay, f.search);
  });
  essays = [...essays].sort((a, b) => {
    if (f.sort === 'words') return (b.word_count || 0) - (a.word_count || 0);
    if (f.sort === 'title') return a.title.localeCompare(b.title);
    return new Date(b.last_touched || 0) - new Date(a.last_touched || 0);
  });
  if (countEl) countEl.textContent = `${essays.length} of ${state.essays.length}`;
  container.innerHTML = essays.length
    ? essays.map((essay) => gardenCardMarkup(essay, { actions: true })).join('')
    : '<div class="garden-empty">No ideas match these filters.</div>';
  wireCardInteractions(container);
}

// ── Month rail (on the essays page) ───────────────────────────────────
// A real day-grid calendar living beside the catalog. Dragging a card onto a
// day is the same durable Ready-for-Air action as the button (Bible #9): it
// POSTs the exact date to /ready-for-air, which writes scheduled_at to
// frontmatter and advances the status. Backed by real essay.scheduled_at, not
// the localStorage week-slot board.

function localIso(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

// One slot per week on the writer's publish day (config calendar.publish_day).
// A week starts on its publish day; dropping writes scheduled_at to that day.
// Chips catch any essay scheduled anywhere in the seven days from the publish
// day, so off-day dates still surface in their week. With no publish day the
// rail is hidden and Ready for Air asks for a date instead.
function monthWeekSlots(offset) {
  const publishIndex = WEEKDAY_INDEX[APP_CONFIG.publishDay] ?? 1;
  const now = new Date();
  const base = new Date(now.getFullYear(), now.getMonth() + offset, 1);
  const year = base.getFullYear();
  const month = base.getMonth();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const weeks = [];
  for (let day = 1; day <= daysInMonth; day += 1) {
    const d = new Date(year, month, day);
    if (d.getDay() === publishIndex) {
      weeks.push({
        start: d,
        end: new Date(year, month, day + 6),
      });
    }
  }
  return { weeks, title: base.toLocaleDateString(undefined, { month: 'long', year: 'numeric' }) };
}

function essaysScheduledInRange(startIso, endIso) {
  return state.essays.filter((essay) => {
    if (!essay.scheduled_at) return false;
    const iso = String(essay.scheduled_at).slice(0, 10);
    return iso >= startIso && iso <= endIso;
  });
}

function renderMonthRail() {
  const grid = document.getElementById('month-grid');
  const titleEl = document.getElementById('month-rail-title');
  if (!grid || !APP_CONFIG.publishDay) return;
  const { weeks, title } = monthWeekSlots(state.monthRailOffset);
  if (titleEl) titleEl.textContent = title.toLowerCase();
  const todayStr = todayIso();
  // The next publish day (soonest slot not yet past) gets the highlight.
  const nextPublishIso = weeks.map((w) => localIso(w.start)).find((iso) => iso >= todayStr) || null;
  grid.innerHTML = weeks.map((week) => {
    const monIso = localIso(week.start);
    const sunIso = localIso(week.end);
    const isNext = monIso === nextPublishIso;
    const isPast = monIso < todayStr;
    const chips = essaysScheduledInRange(monIso, sunIso).map((essay) => {
      const totem = totemFor(essay);
      return `<span class="week-chip" data-open-id="${essay.id}" style="${totemStyle(totem)}" title="${escapeHtml(essay.title)}">
        ${totem?.asset ? `<img src="${escapeHtml(totem.asset)}" alt="">` : ''}<span class="week-chip-title">${escapeHtml(essay.title)}</span>
        <button class="week-chip-remove" type="button" data-unschedule-id="${essay.id}" title="Unschedule" aria-label="Unschedule">×</button></span>`;
    }).join('');
    const label = week.start.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    const dayName = week.start.toLocaleDateString(undefined, { weekday: 'short' }).toLowerCase();
    const flags = `${isNext ? ' is-next' : ''}${isPast ? ' is-past' : ''}${chips ? ' has-essays' : ''}`;
    return `<div class="week-slot${flags}" data-date="${monIso}">
      <div class="week-slot-label"><span class="week-slot-day">${escapeHtml(dayName)} ${escapeHtml(label)}</span>${isNext ? '<span class="week-slot-tag">next up</span>' : ''}</div>
      <div class="week-slot-drop">${chips || '<span class="week-slot-empty">drop to schedule</span>'}</div>
    </div>`;
  }).join('');
  wireMonthRail(grid);
}

function wireMonthRail(grid) {
  grid.querySelectorAll('.week-slot[data-date]').forEach((slot) => {
    slot.addEventListener('dragover', (event) => { event.preventDefault(); slot.classList.add('drop-over'); });
    slot.addEventListener('dragleave', () => slot.classList.remove('drop-over'));
    slot.addEventListener('drop', (event) => {
      event.preventDefault();
      slot.classList.remove('drop-over');
      const essayId = event.dataTransfer.getData('essayId');
      if (essayId) scheduleEssayToDate(essayId, slot.dataset.date);
    });
  });
  grid.querySelectorAll('.week-chip-remove[data-unschedule-id]').forEach((button) => {
    button.addEventListener('click', (event) => { event.stopPropagation(); unscheduleEssayFlow(button.dataset.unscheduleId); });
  });
  grid.querySelectorAll('.week-chip[data-open-id]').forEach((chip) => {
    chip.addEventListener('click', (event) => { event.stopPropagation(); openEditor(chip.dataset.openId); });
  });
}

async function scheduleEssayToDate(essayId, isoDate) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(isoDate || '')) return;
  try {
    const result = await postJson(`/api/essays/${essayId}/ready-for-air`, { scheduled_at: isoDate });
    await handleLifecycleResult(result, `scheduled for ${formatPublishedDate(isoDate) || isoDate}`);
  } catch (err) {
    showRailSoonStatus(`scheduling failed: ${err.message}`);
    await loadEssays();
  }
}

async function unscheduleEssayFlow(essayId) {
  // Clear the air date and drop back to the schedulable pool (Writers Likey).
  try {
    const result = await postJson(`/api/essays/${essayId}/unschedule`, {});
    await handleLifecycleResult(result, 'unscheduled, back to writers likey');
  } catch (err) {
    showRailSoonStatus(`unschedule failed: ${err.message}`);
    await loadEssays();
  }
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatPublishedDate(value) {
  if (!value) return '';
  const parsed = new Date(`${String(value).slice(0, 10)}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }).toLowerCase();
}

function shelfCardMarkup(essay) {
  const totem = totemFor(essay);
  const published = formatPublishedDate(essay.published_date);
  const link = essay.substack_url
    ? `<a class="shelf-link" href="${escapeHtml(essay.substack_url)}" target="_blank" rel="noopener">read on substack ↗</a>`
    : '<span class="shelf-link shelf-link--missing">no live url yet</span>';
  return `<article class="garden-card shelf-card" data-totem="${escapeHtml(essay.totem || '')}" data-essay-id="${essay.id}" data-open-id="${essay.id}"
    style="${totemStyle(totem)}" title="${escapeHtml(essay.title)}">
    ${totemPortraitMarkup(totem)}
    <div class="gc-content">
      <div class="gc-badges"><span class="gc-badge" data-readiness="ready">published${published ? ` · ${published}` : ''}</span></div>
      <h3 class="gc-title">${escapeHtml(essay.title)}</h3>
      ${essay.summary ? `<p class="gc-relation">${escapeHtml(String(essay.summary).slice(0, 160))}</p>` : ''}
      <div class="shelf-links">
        ${link}
        ${essay.obsidian_url ? `<a class="shelf-link" href="${essay.obsidian_url}">open in obsidian</a>` : ''}
      </div>
      ${lifecycleControlsMarkup(essay, 'gc-lifecycle shelf-lifecycle')}
    </div>
    <footer class="gc-foot"><span>${escapeHtml(essay.category || 'uncategorized')}</span><span>${formatWords(essay.word_count)}</span><span>${published || ''}</span></footer>
  </article>`;
}

async function renderShelf(options = {}) {
  const container = document.getElementById('shelf-cards');
  const countEl = document.getElementById('shelf-count');
  if (!container) return;
  if (!state.shelfEssays.length || options.refresh) {
    try {
      const payload = await getJson('/api/essays?scope=shelf');
      state.shelfEssays = payload.essays || [];
    } catch (err) {
      container.innerHTML = `<div class="garden-empty">the shelf failed to load: ${escapeHtml(err.message)}</div>`;
      return;
    }
  }
  const essays = [...state.shelfEssays].sort((a, b) => String(b.published_date || '').localeCompare(String(a.published_date || '')));
  if (countEl) countEl.textContent = String(essays.length);
  container.innerHTML = essays.length
    ? essays.map((essay) => shelfCardMarkup(essay)).join('')
    : '<div class="garden-empty">nothing published yet. the shelf fills as you hit publish.</div>';
  wireCardInteractions(container);
}

function inboxItemMarkup(essay) {
  const suggestion = state.intakeSuggestions[String(essay.id)];
  const categories = suggestion?.categories || [];
  const controls = suggestion
    ? `<div class="inbox-controls">
        ${APP_CONFIG.categoryMode === 'off' ? '' : `<select class="inbox-category" data-inbox-category="${essay.id}" aria-label="Category">
          <option value="">choose a folder...</option>
          ${categories.map((cat) => `<option value="${escapeHtml(cat)}"${cat === suggestion.category ? ' selected' : ''}>${escapeHtml(cat.toLowerCase())}</option>`).join('')}
        </select>`}
        ${APP_CONFIG.totemsEnabled ? `<select class="inbox-totem" data-inbox-totem="${essay.id}" aria-label="Totem">
          <option value="">no totem</option>
          ${Object.entries(TOTEMS).map(([key, t]) => `<option value="${escapeHtml(key)}"${key === suggestion.totem ? ' selected' : ''}>${escapeHtml(t.label.toLowerCase())}</option>`).join('')}
        </select>` : ''}
        <button class="lifecycle-btn" type="button" data-intake-apply="${essay.id}">file it</button>
        ${APP_CONFIG.categoryMode === 'off' ? '' : `<span class="inbox-confidence">${suggestion.category ? `suggested (${suggestion.category_confidence})` : (categories.length ? 'no confident guess, pick one' : 'make a folder in your essays folder to file into')}</span>`}
      </div>`
    : `<button class="lifecycle-btn" type="button" data-intake-suggest="${essay.id}">sort this</button>`;
  return `<div class="inbox-item" data-essay-id="${essay.id}">
    <div class="inbox-item-main">
      <strong>${escapeHtml(essay.title)}</strong>
      <span>${formatWords(essay.word_count)} · ${essay.relative_path ? escapeHtml(essay.relative_path) : ''}</span>
    </div>
    ${controls}
  </div>`;
}

function renderInboxLane() {
  const lane = document.getElementById('inbox-lane');
  const items = document.getElementById('inbox-lane-items');
  if (!lane || !items) return;
  const intake = state.essays.filter((essay) => essay.needs_intake);
  lane.hidden = intake.length === 0;
  if (!intake.length) {
    items.innerHTML = '';
    return;
  }
  items.innerHTML = intake.map((essay) => inboxItemMarkup(essay)).join('');
  items.querySelectorAll('[data-intake-suggest]').forEach((button) => {
    button.addEventListener('click', async () => {
      const id = button.dataset.intakeSuggest;
      button.disabled = true;
      try {
        state.intakeSuggestions[String(id)] = await postJson(`/api/essays/${id}/intake-suggest`, {});
      } catch (err) {
        showRailSoonStatus(`suggestion failed: ${err.message}`);
      }
      renderInboxLane();
    });
  });
  items.querySelectorAll('[data-intake-apply]').forEach((button) => {
    button.addEventListener('click', async () => {
      const id = button.dataset.intakeApply;
      const category = items.querySelector(`[data-inbox-category="${id}"]`)?.value || '';
      const totem = items.querySelector(`[data-inbox-totem="${id}"]`)?.value || '';
      if (!category && APP_CONFIG.categoryMode !== 'off') {
        showRailSoonStatus('pick a category folder first. Categories are the folders inside your essays folder.');
        return;
      }
      button.disabled = true;
      try {
        const result = await postJson(`/api/essays/${id}/intake-apply`, { category, totem, status: 'Writers Room' });
        delete state.intakeSuggestions[String(id)];
        await handleLifecycleResult(result, category ? `filed into ${category.toLowerCase()}` : 'filed');
      } catch (err) {
        showRailSoonStatus(`intake failed: ${err.message}`);
        button.disabled = false;
      }
    });
  });
}



























async function renderSettings() {
  if (!document.getElementById('settings-view')) return;
  try {
    const status = await getJson('/api/app/status');
    const substack = status.substack || {};
    setSettingValue('settings-app-url', status.app_url || `${window.location.origin}/airdate`);
    setSettingValue('settings-port', status.port ? String(status.port) : 'not set');
    setSettingValue(
      'settings-auth',
      status.auth_required ? 'required' : 'off on loopback',
      status.auth_required ? 'warn' : 'good',
    );
    setSettingValue(
      'settings-paths-visible',
      status.local_paths_visible ? 'visible' : 'hidden',
      status.local_paths_visible ? 'good' : 'warn',
    );
    setSettingValue('settings-obsidian-dir', status.obsidian_dir);
    setSettingValue('settings-data-dir', status.data_dir);
    setSettingValue('settings-drafts-dir', status.drafts_dir);
    setSettingValue('settings-index', `${status.essay_count || 0} essays • ${formatSettingsTime(status.scanned_at)}`);
    renderSubstackConnection(substack);
    setSettingValue(
      'settings-publication',
      substack.publication_configured ? substack.publication : (substack.publication || 'not set'),
      substack.publication_configured ? 'good' : 'warn',
    );
    renderSetupPanel(status);
  } catch (err) {
    setSettingsStatus(`settings status failed: ${err.message}`, 'bad');
  }
}

function renderActiveView() {
  if (state.view === 'settings') {
    renderSettings();
  } else if (state.view === 'shelf') {
    renderShelf();
  } else {
    renderAllIdeas();
    renderInboxLane();
    renderMonthRail();
  }
}


function moveToRainy(essayId) {
  if (!state.rainyDay.includes(essayId)) state.rainyDay.push(essayId);
  // Sheltering is a demotion: the piece leaves the garden and its
  // tenure/access standing resets.
  demoteFromGarden(essayId);
  saveJson(RAINY_KEY, state.rainyDay);
  touchEssay(essayId, 'rainy_day');
  renderAll();
}


function renderAll() {
  renderActiveView();
}

function normalizeEditorValue(name, value) {
  if (EDITOR_ARRAY_FIELDS.includes(name)) {
    const values = Array.isArray(value) ? value : String(value || '').split(',');
    return values.map((item) => String(item).trim()).filter(Boolean);
  }
  if (EDITOR_CHECKBOX_FIELDS.includes(name)) {
    return value === true || value === 'true' || value === 1;
  }
  if (value == null) return '';
  return typeof value === 'string' ? value.trim() : value;
}

function editorValuesEqual(a, b) {
  if (Array.isArray(a) || Array.isArray(b)) {
    const left = Array.isArray(a) ? a : [];
    const right = Array.isArray(b) ? b : [];
    return left.length === right.length && left.every((value, index) => value === right[index]);
  }
  return a === b;
}

function formPayload(options = {}) {
  const payload = {};
  const seenRadios = new Set();
  for (const element of editorForm.elements) {
    if (!element.name) continue;
    const { name, type } = element;
    if (type === 'checkbox') {
      payload[name] = normalizeEditorValue(name, element.checked);
    } else if (type === 'radio') {
      if (seenRadios.has(name)) continue;
      seenRadios.add(name);
      const checked = editorForm.querySelector(`input[name="${name}"]:checked`);
      payload[name] = normalizeEditorValue(name, checked ? checked.value : '');
    } else if (type === 'submit' || type === 'button') {
      continue;
    } else {
      payload[name] = normalizeEditorValue(name, element.value);
    }
  }
  if (!options.changedOnly) return payload;

  const baseline = state.editorBaseline || {};
  const changed = {};
  for (const [key, value] of Object.entries(payload)) {
    if (!editorValuesEqual(value, baseline[key])) changed[key] = value;
  }
  return changed;
}

function frontmatterPayload(options = {}) {
  const payload = formPayload(options);
  delete payload.body;
  return payload;
}

function editorBody() {
  return editorForm.elements.body?.value || '';
}

function editorBodyChanged() {
  const baseline = state.editorBaseline || {};
  return !editorValuesEqual(editorBody(), baseline.body || '');
}

function expectedFileStatePayload() {
  return {
    expected_mtime: state.editorFileState?.mtime,
    expected_content_hash: state.editorFileState?.content_hash,
  };
}

function saveRequestPayload(options = {}) {
  const includeBody = options.includeBody === true || editorBodyChanged();
  const bodyPayload = includeBody ? { body: editorBody() } : {};
  const updates = frontmatterPayload({ changedOnly: true });
  // The editor displays sensible defaults for a complete draft packet. Persist
  // those values even when they happen to match HTML control defaults; otherwise
  // audience/comment permissions silently remain absent from Obsidian.
  const completeDraft = frontmatterPayload();
  for (const field of [
    'title', 'subtitle', 'summary', 'publication', 'audience',
    'comment_permissions', 'email_subject', 'email_preview_text',
    'hero_image', 'thumbnail_alt', 'seo_title', 'seo_description',
    'social_title', 'social_description', 'thumbnail_prompt',
  ]) {
    if (completeDraft[field] !== undefined && String(completeDraft[field]).trim()) updates[field] = completeDraft[field];
  }
  if (completeDraft.tags && String(completeDraft.tags).trim()) updates.tags = completeDraft.tags;
  return {
    updates,
    ...bodyPayload,
    ...expectedFileStatePayload(),
  };
}

function refreshEditorBaselineFromSave(payload) {
  state.editorBaseline = formPayload();
  state.editorFileState = {
    mtime: payload.mtime,
    content_hash: payload.content_hash,
  };
}

function showEditorConflict(error) {
  const payload = error.payload || {};
  const compact = {
    ok: false,
    reason: payload.reason || 'file_changed',
    message: payload.message || 'This essay changed in Obsidian after AirDate loaded it.',
    path: payload.path,
    current_mtime_iso: payload.current_mtime_iso,
    current_content_hash: payload.current_content_hash,
    current_title: payload.current_frontmatter?.title || '',
    current_summary: payload.current_frontmatter?.summary || '',
    current_body_preview: String(payload.current_body || '').slice(0, 500),
  };
  editorOutput.textContent = `${JSON.stringify(compact, null, 2)}\n\nClose and reopen this editor to load the current Obsidian file before saving.`;
  setEditorStatus('Obsidian changed this file. Close and reopen the editor before saving.', 'bad');
}

function clearEditorValidation() {
  editorForm.querySelectorAll('[aria-invalid="true"]').forEach((field) => {
    field.removeAttribute('aria-invalid');
  });
}

function markEditorField(name, invalid) {
  const field = editorForm.elements[name];
  const input = field instanceof RadioNodeList ? field[0] : field;
  if (!input) return;
  if (invalid) input.setAttribute('aria-invalid', 'true');
  else input.removeAttribute('aria-invalid');
}

function validateEditor(action, substackStatus = {}) {
  clearEditorValidation();
  const payload = formPayload();
  const required = action === 'send' ? EDITOR_SEND_REQUIRED_FIELDS : EDITOR_SAVE_REQUIRED_FIELDS;
  const missing = required.filter((field) => !String(payload[field] || '').trim());
  const hasPublication = String(payload.publication || substackStatus.publication || '').trim();
  if (action === 'send' && !hasPublication) missing.push('publication');

  missing.forEach((field) => markEditorField(field, true));
  if (missing.length) {
    setEditorStatus(`missing required field${missing.length > 1 ? 's' : ''}: ${missing.join(', ')}`, 'bad');
    const first = editorForm.elements[missing[0]];
    const input = first instanceof RadioNodeList ? first[0] : first;
    input?.focus?.();
    return false;
  }
  return true;
}

function markPreflightBlockers(preflight) {
  clearEditorValidation();
  for (const blocker of preflight.blockers || []) {
    if (blocker.field) markEditorField(blocker.field, true);
  }
}

function preflightStatusMessage(preflight) {
  const blockers = preflight.blockers || [];
  const warnings = preflight.warnings || [];
  if (blockers.length) {
    // Body-fidelity blockers carry a human `label`; field blockers fall back to
    // the field name. De-duped because two findings can share one line.
    const labels = [...new Set(
      blockers.map((item) => item.label || item.field || item.key).filter(Boolean),
    )].join(', ');
    return `not ready: ${labels || `${blockers.length} blocker${blockers.length === 1 ? '' : 's'}`}`;
  }
  const metadataGaps = (preflight.metadata_checks || []).filter((item) => !item.ok);
  if (metadataGaps.length) {
    return `draft-ready with ${metadataGaps.length} metadata gap${metadataGaps.length === 1 ? '' : 's'}.`;
  }
  if (warnings.length) return `ready with ${warnings.length} warning${warnings.length === 1 ? '' : 's'}.`;
  return 'ready to create a Substack draft.';
}

async function runEditorPreflight(options = {}) {
  if (!state.selectedId) return null;
  const preflight = await postJson(`/api/essays/${state.selectedId}/preflight`, {
    publish: frontmatterPayload(),
    body: editorBody(),
  });
  markPreflightBlockers(preflight);
  editorOutput.textContent = JSON.stringify(preflight, null, 2);
  if (options.setStatus !== false) {
    setEditorStatus(preflightStatusMessage(preflight), preflight.ready ? 'good' : 'bad');
  }
  return preflight;
}

function editorFieldValue(name) {
  const input = editorForm.elements[name];
  if (!input) return '';
  if (input instanceof RadioNodeList || (input.length && input[0]?.type === 'radio')) {
    const checked = editorForm.querySelector(`input[name="${name}"]:checked`);
    return checked ? checked.value : '';
  }
  return input.value || '';
}

function setEditorFieldValue(name, value) {
  const input = editorForm.elements[name];
  if (!input || value == null) return false;
  const text = Array.isArray(value) ? value.join(', ') : String(value).trim();
  if (!text) return false;
  if (input instanceof RadioNodeList || (input.length && input[0]?.type === 'radio')) {
    let changed = false;
    for (const radio of input) {
      if (radio.value === text && !radio.checked) {
        radio.checked = true;
        changed = true;
      }
    }
    return changed;
  }
  if (String(input.value || '').trim() === text) return false;
  input.value = text;
  return true;
}

function fillEmptyEditorField(name, value) {
  if (String(editorFieldValue(name) || '').trim()) return false;
  return setEditorFieldValue(name, value);
}

function updateEditorContext(essay) {
  if (!editorContextEl) return;
  const role = essay.source_role || 'standalone';
  const relation = relationshipText(essay);
  editorContextEl.dataset.role = role;
  editorContextEl.innerHTML = `
    <span class="editor-role-pill" data-role="${role}">${sourceRoleLabel(essay)}</span>
    <span>${essay.relative_path || essay.path || ''}</span>
    ${relation ? `<span>${relation}</span>` : ''}
  `;
  const createButton = document.getElementById('editor-create-draft');
  if (createButton) createButton.hidden = role !== 'source';
  const sendButton = document.getElementById('editor-send');
  const thumbnailPromptButton = document.getElementById('editor-thumbnail-prompt');
  const attachHeroButton = document.getElementById('editor-attach-hero');
  if (attachHeroButton) attachHeroButton.disabled = role === 'source';
  // The ChatGPT bridge needs no key and no lifecycle gate; only a source note
  // (which never gets a thumbnail of its own) hides it.
  if (thumbnailPromptButton) thumbnailPromptButton.hidden = role === 'source';
  const status = essay.status || 'Writers Room';
  // Draft creation enters the lifecycle only after an essay has an air date.
  const sendGated = !SEND_ENABLED_STATUSES.includes(status);
  if (sendButton) {
    sendButton.disabled = role === 'source' || sendGated;
    sendButton.title = role === 'source' ? 'create a linked draft first' : sendGated ? 'available once Ready for Air' : '';
  }
}

// The Substack draft link is durable (substack_draft_url is persisted on every
// send) and now visible: at editor open from the note, after a send from the
// transport's edit_url.
function renderEditorDraftLink(url) {
  const link = document.getElementById('editor-draft-link');
  if (!link) return;
  const value = String(url || '').trim();
  if (/^https?:\/\//.test(value)) {
    link.href = value;
    link.hidden = false;
  } else {
    link.removeAttribute('href');
    link.hidden = true;
  }
}

function fillForm(essay) {
  const frontmatter = essay.frontmatter || {};
  for (const [key, value] of Object.entries(frontmatter)) {
    const input = editorForm.elements[key];
    if (!input) continue;
    if (input instanceof RadioNodeList || (input.length && input[0]?.type === 'radio')) {
      const stringValue = String(value ?? '').trim();
      for (const radio of input) {
        radio.checked = radio.value === stringValue;
      }
      continue;
    }
    if (input.type === 'checkbox') {
      input.checked = value === true || value === 'true' || value === 1;
      continue;
    }
    input.value = Array.isArray(value) ? value.join(', ') : value ?? '';
  }
  if (editorForm.elements.body) editorForm.elements.body.value = essay.body || '';
  if (editorForm.elements.source_role) editorForm.elements.source_role.value = essay.source_role || frontmatter.source_role || 'standalone';
  if (editorForm.elements.draft_of) editorForm.elements.draft_of.value = essay.draft_of || frontmatter.draft_of || '';
  const baselineBeforeDefaults = formPayload();
  const seeded = {
    title: essay.title,
    subtitle: essay.subtitle,
    summary: essay.summary,
    status: essay.status,
    totem: essay.totem,
  };
  for (const [key, value] of Object.entries(seeded)) {
    const input = editorForm.elements[key];
    if (!input || frontmatter[key] || value == null || value === '') continue;
    input.value = value;
  }
  applyMetadataDefaults(essay.metadata_defaults || {});
  state.editorBaseline = baselineBeforeDefaults;
  state.editorFileState = {
    mtime: essay.mtime,
    content_hash: essay.content_hash,
  };
  clearEditorValidation();
  renderEditorActiveTags();
}

function applyMetadataDefaults(defaults = {}) {
  const fields = [
    'publication',
    'subtitle',
    'summary',
    'seo_title',
    'seo_description',
    'social_title',
    'social_description',
    'thumbnail_prompt',
    'thumbnail_alt',
    'email_subject',
    'email_preview_text',
    'hero_image',
    'social_image',
  ];
  for (const field of fields) {
    fillEmptyEditorField(field, defaults[field]);
  }
  if (!currentEditorTags().length && Array.isArray(defaults.tags) && defaults.tags.length) {
    setEditorTags(defaults.tags);
  }
}

function currentEditorTags() {
  const raw = editorForm.elements.tags?.value || '';
  return raw.split(',').map((tag) => tag.trim()).filter(Boolean);
}

function setEditorTags(tags) {
  const unique = [...new Set(tags.map((tag) => tag.trim()).filter(Boolean))];
  if (editorForm.elements.tags) editorForm.elements.tags.value = unique.join(', ');
  renderEditorActiveTags();
}

function renderEditorActiveTags() {
  if (!editorActiveTagsEl) return;
  const tags = currentEditorTags();
  editorActiveTagsEl.innerHTML = '';
  if (!tags.length) {
    const empty = document.createElement('p');
    empty.textContent = 'no active tags yet.';
    editorActiveTagsEl.append(empty);
    return;
  }
  for (const tag of tags) {
    const chip = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = true;
    cb.value = tag;
    cb.addEventListener('change', () => {
      if (!cb.checked) setEditorTags(currentEditorTags().filter((t) => t !== tag));
    });
    const span = document.createElement('span');
    span.textContent = tag;
    chip.append(cb, span);
    editorActiveTagsEl.append(chip);
  }
}

function populateEditorTagControls() {
  const noPresets = !Object.keys(EDITOR_TOPIC_TAGS).length;
  editorTopicPresetEl?.closest('label')?.classList.toggle('hidden', noPresets);
  document.getElementById('editor-add-preset-tags')?.classList.toggle('hidden', noPresets);
  if (editorTopicPresetEl) {
    editorTopicPresetEl.innerHTML = '<option value="">choose a topic...</option>';
    for (const name of Object.keys(EDITOR_TOPIC_TAGS)) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      editorTopicPresetEl.append(option);
    }
  }
  if (editorTagDatalistEl) {
    editorTagDatalistEl.innerHTML = '';
    for (const tag of EDITOR_ALL_TAGS) {
      const opt = document.createElement('option');
      opt.value = tag;
      editorTagDatalistEl.append(opt);
    }
  }
}

function setEditorStatus(message, kind) {
  if (!editorStatusEl) return;
  editorStatusEl.textContent = message || '';
  editorStatusEl.dataset.kind = kind || '';
}

// Status line with one clickable anchor in the middle: `before`, the link, `after`.
function setEditorStatusWithLink(before, url, linkText, after, kind) {
  if (!editorStatusEl) return;
  editorStatusEl.textContent = '';
  editorStatusEl.append(document.createTextNode(before || ''));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.target = '_blank';
  anchor.rel = 'noopener';
  anchor.textContent = linkText;
  editorStatusEl.append(anchor, document.createTextNode(after || ''));
  editorStatusEl.dataset.kind = kind || '';
}

function showRailSoonStatus(message) {
  if (!railSoonStatusEl) return;
  railSoonStatusEl.textContent = message || '';
}



async function connectSubstack(reason, setStatus = setEditorStatus) {
  try {
    const res = await postJson('/api/substack/connect', {});
    setStatus(res.pending
      ? 'finish signing in to Substack in the Obsidian window, then retry send.'
      : 'Substack connected through Obsidian.', 'pending');
    // A pending sign-in is not a connection: the window merely opened. A
    // caller that retried on `pending` would re-post against the same dead
    // session before anyone could have signed in.
    return !!res.ok && !res.pending;
  } catch (err) {
    setStatus(`couldn't open Obsidian sign-in: ${err.message}`, 'bad');
    return false;
  }
}

function setSetupMessage(message, kind = '') {
  const el = document.getElementById('setup-messages');
  if (!el) return;
  el.textContent = message || '';
  el.dataset.kind = kind;
}

// Fill the setup form from /api/app/status. Runs on first run (where the
// settings view is all the app shows) and every time settings opens.
function renderSetupPanel(status) {
  const form = document.getElementById('setup-form');
  const setup = status?.setup;
  if (!form || !setup) return;
  const values = setup.form || {};
  const pathField = document.getElementById('setup-vault-path-field');
  pathField?.classList.toggle('hidden', !setup.paths_editable);
  if (!form.dataset.dirty) {
    for (const name of ['vault_path', 'essays_folder', 'vault_name', 'publication', 'publication_name', 'publish_day']) {
      if (form.elements[name]) form.elements[name].value = values[name] ?? '';
    }
    form.elements.totems_enabled.checked = Boolean(status.config?.totems?.enabled);
    renderSetupTotemRows(status.config?.totems?.slots || []);
    document.getElementById('setup-totem-list')?.classList.toggle('is-off', !form.elements.totems_enabled.checked);
  }
  const intro = document.getElementById('setup-intro');
  if (intro) {
    intro.textContent = setup.required
      ? 'Welcome. AirDate reads essays from a folder in your Obsidian vault and sends them to Substack as drafts. It never publishes. Point it at your vault to begin.'
      : 'AirDate reads essays from a folder in your Obsidian vault and sends them to Substack as drafts. It never publishes.';
  }
  const problems = [
    setup.load_error,
    ...(setup.errors || []),
    setup.vault_message,
    setup.essays_message,
    setup.paths_editable ? '' : 'The vault folder is set in config.json on the machine running AirDate.',
  ].filter(Boolean);
  document.getElementById('setup-create-essays')?.classList.toggle('hidden', !(setup.vault_ok && !setup.essays_ok));
  if (problems.length) setSetupMessage(problems.join('\n'), setup.required ? 'bad' : '');
  else setSetupMessage(setup.required ? '' : 'settings saved in config.json.', setup.required ? '' : 'good');
}

function renderSetupTotemRows(slots) {
  const list = document.getElementById('setup-totem-list');
  if (!list) return;
  list.innerHTML = slots.map((slot) => `<div class="setup-totem-row" data-totem-key="${escapeHtml(slot.key)}">
      <img src="${escapeHtml(slot.image)}" alt="">
      <input name="totem_label" value="${escapeHtml(slot.label)}" aria-label="Totem name" autocomplete="off">
      <input name="totem_color" type="color" value="${escapeHtml(slot.color || '#8092b0')}" aria-label="Totem color">
      <input class="setup-totem-image" name="totem_image" value="${escapeHtml(slot.image_path || '')}" placeholder="icon: path in your vault, or blank for the placeholder" aria-label="Totem icon path" autocomplete="off" spellcheck="false">
    </div>`).join('');
}

function setupFormPayload() {
  const form = document.getElementById('setup-form');
  const payload = {
    essays_folder: form.elements.essays_folder.value.trim(),
    vault_name: form.elements.vault_name.value.trim(),
    publication: form.elements.publication.value.trim(),
    publication_name: form.elements.publication_name.value.trim(),
    publish_day: form.elements.publish_day.value,
    totems_enabled: form.elements.totems_enabled.checked,
    totems: [...form.querySelectorAll('.setup-totem-row')].map((row) => ({
      key: row.dataset.totemKey,
      label: row.querySelector('[name="totem_label"]').value.trim(),
      color: row.querySelector('[name="totem_color"]').value,
      image: row.querySelector('[name="totem_image"]').value.trim(),
    })),
  };
  if (!document.getElementById('setup-vault-path-field')?.classList.contains('hidden')) {
    payload.vault_path = form.elements.vault_path.value.trim();
  }
  return payload;
}

async function saveSetup() {
  const form = document.getElementById('setup-form');
  setSetupMessage('saving...', '');
  let result;
  try {
    const response = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(setupFormPayload()),
    });
    result = await response.json();
    if (!response.ok && !result.errors) throw new Error(result.error || 'Saving settings failed.');
  } catch (err) {
    setSetupMessage(err.message, 'bad');
    return;
  }
  if (result.errors?.length) {
    setSetupMessage(result.errors.join('\n'), 'bad');
    return;
  }
  delete form.dataset.dirty;
  const wasRequired = state.setupRequired;
  await refreshAppStatus();
  if (!state.setupRequired) {
    state.shelfEssays = [];
    await loadEssays();
  }
  if (wasRequired && !state.setupRequired) {
    switchView('all-ideas');
    await handleInitialEssayDeepLink();
    return;
  }
  await renderSettings();
}

function bindSettingsEvents() {
  document.getElementById('settings-copy-url')?.addEventListener('click', async () => {
    try {
      await copyText(currentSettingsUrl());
      setSettingsStatus('app url copied.', 'good');
    } catch (err) {
      setSettingsStatus(`copy failed: ${err.message}`, 'bad');
    }
  });

  document.getElementById('settings-open-app')?.addEventListener('click', () => {
    window.open(currentSettingsUrl(), '_blank', 'noopener');
  });

  document.getElementById('settings-refresh-essays')?.addEventListener('click', async () => {
    setSettingsStatus('reloading essay index...', '');
    try {
      const payload = await postJson('/api/app/refresh-essays', {});
      await loadEssays();
      setSettingsStatus(`reloaded ${payload.count || 0} essays.`, 'good');
    } catch (err) {
      setSettingsStatus(`reload failed: ${err.message}`, 'bad');
    }
  });

  document.getElementById('settings-refresh-substack')?.addEventListener('click', async () => {
    setSettingsStatus('opening the secure Substack sign-in window...', '');
    await connectSubstack('Refresh Substack connection.', setSettingsStatus);
    await renderSettings();
  });

  const setupForm = document.getElementById('setup-form');
  setupForm?.addEventListener('input', () => { setupForm.dataset.dirty = '1'; });
  setupForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    await saveSetup();
  });
  setupForm?.elements.totems_enabled?.addEventListener('change', (event) => {
    const fieldset = setupForm.querySelector('.setup-totems');
    fieldset?.querySelector('.setup-totem-list')?.classList.toggle('is-off', !event.target.checked);
  });
  document.getElementById('setup-create-essays')?.addEventListener('click', async () => {
    setSetupMessage('creating the essays folder...', '');
    try {
      await postJson('/api/settings/create-essays-folder', {});
      await saveSetup();
    } catch (err) {
      setSetupMessage(err.message, 'bad');
    }
  });



}



function wireEditorDropFields() {
  for (const field of editorForm.querySelectorAll('.drop-field')) {
    const targetName = field.dataset.target;
    const input = editorForm.elements[targetName];
    if (!input) continue;
    ['dragenter', 'dragover'].forEach((evt) => field.addEventListener(evt, (e) => {
      e.preventDefault();
      field.classList.add('is-dragging');
    }));
    ['dragleave', 'drop'].forEach((evt) => field.addEventListener(evt, () => field.classList.remove('is-dragging')));
    field.addEventListener('drop', async (event) => {
      event.preventDefault();
      const file = event.dataTransfer?.files?.[0];
      if (!file) return;
      try {
        if (targetName === 'hero_image' && state.selectedId) {
          await attachHeroImage(state.selectedId, file, { setStatus: setEditorStatus });
          return;
        }
        const dataUrl = await readFileAsDataUrl(file);
        const result = await postJson('/api/upload-image', { filename: file.name, dataUrl });
        if (result?.relativePath) {
          input.value = result.relativePath;
          setEditorStatus(`uploaded ${file.name}`, 'good');
        }
      } catch (err) {
        setEditorStatus(`upload failed: ${err.message}`, 'bad');
      }
    });
  }
}

async function openEditor(essayId) {
  const returnFocus = overlayEl.classList.contains('hidden') ? document.activeElement : null;
  const essay = await getJson(`/api/essays/${essayId}`);
  if (returnFocus && returnFocus !== document.body) state.editorReturnFocus = returnFocus;
  state.selectedId = essayId;
  recordGardenAccess(essayId);
  editorTitle.textContent = `expanded essay editor: ${essay.title}`;
  editorForm.reset();
  fillForm(essay);
  updateEditorContext(essay);
  renderEditorDraftLink(essay.frontmatter?.substack_draft_url);
  overlayEl.classList.remove('hidden');
  overlayEl.setAttribute('aria-hidden', 'false');
  editorOutput.textContent = 'no preview yet.';
  setEditorStatus('', '');
  refreshSubstackConnectionStatus().catch(() => {
    setSettingValue('editor-substack-session', 'status unavailable', 'warn');
  });
  touchEssay(essayId, 'open');
  editorShellEl.focus({ preventScroll: true });
}

function closeEditor() {
  if (overlayEl.classList.contains('hidden')) return;
  overlayEl.classList.add('hidden');
  overlayEl.setAttribute('aria-hidden', 'true');
  state.selectedId = null;
  state.editorBaseline = null;
  state.editorFileState = null;
  const returnFocus = state.editorReturnFocus;
  state.editorReturnFocus = null;
  if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
}

function editorDialogFocusableElements() {
  const selector = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');
  return [...editorShellEl.querySelectorAll(selector)].filter((element) => element.offsetParent !== null);
}

function handleEditorDialogKeydown(event) {
  if (overlayEl.classList.contains('hidden')) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeEditor();
    return;
  }
  if (event.key !== 'Tab') return;

  const focusable = editorDialogFocusableElements();
  if (!focusable.length) {
    event.preventDefault();
    editorShellEl.focus({ preventScroll: true });
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement;
  if (event.shiftKey && (active === first || active === editorShellEl || !editorShellEl.contains(active))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (active === last || active === editorShellEl || !editorShellEl.contains(active))) {
    event.preventDefault();
    first.focus();
  }
}

function containEditorDialogFocus(event) {
  if (overlayEl.classList.contains('hidden') || editorShellEl.contains(event.target)) return;
  editorShellEl.focus({ preventScroll: true });
}

// Essay ids used to be path hashes; they are now the durable frontmatter uid
// once an essay has been stamped. Local state keyed by the old hash has to
// follow, or planted/tenure/garden history silently orphans. Self-limiting: it
// only acts on hashes still referenced locally, so it costs nothing once the
// catalog has converged (and it keeps working as essays are stamped lazily).
function migrateHashIdsToUid() {
  const referenced = new Set([
    ...state.planted.map(String),
    ...state.rainyDay.map(String),
    ...Object.keys(state.plantedAt),
    ...Object.keys(state.gardenWork),
    ...(Array.isArray(state.contact.uniqueEssayIds) ? state.contact.uniqueEssayIds.map(String) : []),
  ]);
  if (!referenced.size) return;
  for (const essay of state.essays) {
    const canonical = String(essay.id || '');
    const legacy = String(essay.legacy_path_id || '');
    if (legacy && canonical && legacy !== canonical && referenced.has(legacy)) {
      remapEssayId(legacy, canonical);
    }
  }
}

async function loadEssays() {
  const payload = await getJson('/api/essays');
  state.essays = payload.essays || [];
  migrateHashIdsToUid();
  renderAll();
}

function setDeepLinkNotice(message, kind = '') {
  if (!deepLinkNoticeEl) return;
  deepLinkNoticeEl.textContent = message || '';
  deepLinkNoticeEl.dataset.kind = kind;
}

async function handleInitialEssayDeepLink() {
  if (!window.AirdateDeepLink?.handleEssayDeepLink) {
    if (new URLSearchParams(window.location.search).has('essay')) {
      switchView('all-ideas', { persist: false });
      setDeepLinkNotice('The essay link could not be read. The catalog is open so you can choose an essay.', 'bad');
    }
    return;
  }

  let deepLinkEssays;
  try {
    deepLinkEssays = await window.AirdateDeepLink.loadEssayDeepLinkCatalog(
      window.location.search,
      state.essays,
      () => getJson('/api/essays?scope=all'),
    );
  } catch (error) {
    switchView('all-ideas', { persist: false });
    setDeepLinkNotice(`The full essay catalog could not load: ${error.message}`, 'bad');
    return;
  }

  await window.AirdateDeepLink.handleEssayDeepLink(window.location.search, deepLinkEssays, {
    openCatalog: () => switchView('all-ideas', { persist: false }),
    clearNotice: () => setDeepLinkNotice('', ''),
    showNotice: (message, kind) => setDeepLinkNotice(message, kind),
    openEditor,
  });
}

function bindEvents() {
  // View switching (garden / all ideas / calendar). The nav tile is the
  // front door: entering All Ideas this way always shows ALL ideas —
  // filters set by plaques or earlier visits are cleared.
  document.querySelectorAll('.rail-tile[data-view]').forEach((tile) => {
    tile.addEventListener('click', (event) => {
      const view = tile.dataset.view;
      if (VIEWS.includes(view)) {
        event.preventDefault();
        if (view === 'all-ideas') resetAiFilters();
        switchView(view);
      }
    });
  });




  // All Ideas controls
  const aiSearch = document.getElementById('ai-search');
  if (aiSearch) {
    aiSearch.value = state.aiFilters.search;
    aiSearch.addEventListener('input', (event) => {
      state.aiFilters.search = event.target.value;
      saveAiFilters();
      renderAllIdeas();
    });
  }
  document.getElementById('ai-topic')?.addEventListener('change', (event) => {
    state.aiFilters.topic = event.target.value;
    saveAiFilters();
    renderAllIdeas();
  });
  document.getElementById('ai-totem')?.addEventListener('change', (event) => {
    state.aiFilters.totem = event.target.value;
    saveAiFilters();
    renderAllIdeas();
  });
  document.getElementById('ai-sort')?.addEventListener('change', (event) => {
    state.aiFilters.sort = event.target.value;
    saveAiFilters();
    renderAllIdeas();
  });
  document.getElementById('ai-status')?.addEventListener('change', (event) => {
    state.aiFilters.status = event.target.value;
    saveAiFilters();
    renderAllIdeas();
  });
  document.getElementById('ai-clear')?.addEventListener('click', () => {
    resetAiFilters();
    renderAllIdeas();
  });
  document.getElementById('month-prev')?.addEventListener('click', () => {
    state.monthRailOffset -= 1;
    renderMonthRail();
  });
  document.getElementById('month-next')?.addEventListener('click', () => {
    state.monthRailOffset += 1;
    renderMonthRail();
  });
  document.getElementById('month-today')?.addEventListener('click', () => {
    state.monthRailOffset = 0;
    renderMonthRail();
  });
  document.getElementById('editor-close').addEventListener('click', closeEditor);
  document.addEventListener('keydown', handleEditorDialogKeydown);
  document.addEventListener('focusin', containEditorDialogFocus);
  document.getElementById('editor-preview').addEventListener('click', async () => {
    if (!state.selectedId) return;
    try {
      const payload = await postJson(`/api/essays/${state.selectedId}/preview`, {
        updates: frontmatterPayload({ changedOnly: true }),
        body: editorBody(),
      });
      editorOutput.textContent = JSON.stringify(payload, null, 2);
    } catch (err) {
      editorOutput.textContent = JSON.stringify(err.payload || { error: err.message }, null, 2);
      setEditorStatus(`preview failed: ${err.message}`, 'bad');
    }
  });
  document.getElementById('editor-save').addEventListener('click', async () => {
    if (!state.selectedId) return;
    if (!validateEditor('save')) return;
    const payloadForSave = saveRequestPayload();
    if (!Object.keys(payloadForSave.updates || {}).length && payloadForSave.body == null) {
      setEditorStatus('no changes to save.', 'good');
      editorOutput.textContent = JSON.stringify({ ok: true, changes: {} }, null, 2);
      return;
    }
    try {
      const payload = await postJson(`/api/essays/${state.selectedId}/save`, payloadForSave);
      editorOutput.textContent = JSON.stringify(payload, null, 2);
      refreshEditorBaselineFromSave(payload);
      // A plain save can mint the durable uid, which changes the essay's id.
      // Remap before touchEssay so the access is recorded under the new id and
      // the open editor keeps pointing at the right essay.
      if (payload?.old_id && payload?.new_id && payload.old_id !== payload.new_id) {
        remapEssayId(payload.old_id, payload.new_id);
      }
      setEditorStatus('saved to Obsidian.', 'good');
      touchEssay(state.selectedId, 'save');
      await loadEssays();
    } catch (err) {
      if (err.status === 409) showEditorConflict(err);
      else setEditorStatus(`save failed: ${err.message}`, 'bad');
    }
  });
  document.getElementById('editor-create-draft')?.addEventListener('click', async () => {
    if (!state.selectedId) return;
    try {
      const payload = await createLinkedDraftFromSource(state.selectedId);
      editorOutput.textContent = JSON.stringify(payload, null, 2);
    } catch (err) {
      editorOutput.textContent = JSON.stringify(err.payload || { error: err.message }, null, 2);
      setEditorStatus(`draft creation failed: ${err.message}`, 'bad');
    }
  });
  document.getElementById('editor-preflight')?.addEventListener('click', async () => {
    if (!state.selectedId) return;
    setEditorStatus('checking readiness...', 'pending');
    try {
      await runEditorPreflight();
    } catch (err) {
      setEditorStatus(`readiness check failed: ${err.message}`, 'bad');
    }
  });
  document.getElementById('editor-attach-hero')?.addEventListener('click', async () => {
    if (!state.selectedId) return;
    await pickAndAttachHero(state.selectedId, setEditorStatus);
  });
  document.getElementById('editor-thumbnail-prompt')?.addEventListener('click', async () => {
    if (!state.selectedId) return;
    setEditorStatus('preparing thumbnail prompt...', 'pending');
    try {
      const payload = await postJson(`/api/essays/${state.selectedId}/thumbnail-prompt`, {
        publish: frontmatterPayload(),
      });
      const prompt = String(payload.prompt || '').trim();
      if (!prompt) throw new Error('the server returned an empty prompt');
      await copyText(prompt);
      editorOutput.textContent = prompt;
      window.open('https://chatgpt.com', '_blank', 'noopener');
      setEditorStatus('prompt copied. Generate in ChatGPT, then drag the image onto the hero field.', 'good');
      touchEssay(state.selectedId, 'copy_thumbnail_prompt');
    } catch (err) {
      setEditorStatus(`thumbnail prompt failed: ${err.message}`, 'bad');
    }
  });

  document.getElementById('editor-refresh-substack')?.addEventListener('click', async () => {
    setEditorStatus('opening the secure Substack sign-in window...', '');
    await connectSubstack('Refresh Substack connection.', setEditorStatus);
  });

  // Transport warnings (section, tags, metadata polish, body repairs) ride
  // along on both outcomes. The status line names them so a created draft
  // that silently lost its tags stops reading as clean.
  function transportWarningsNote(result) {
    const warnings = (Array.isArray(result.warnings) ? result.warnings : []).filter(Boolean);
    if (!warnings.length) return '';
    return ` ${warnings.length} transport warning${warnings.length === 1 ? '' : 's'}: ${warnings.join(' · ')}`;
  }

  // A send saves the editor's changes before it evaluates readiness or runs
  // the transport, so a failed send may still have rewritten the note. Adopt
  // the file state that save produced, or the next send (and the auth retry)
  // posts the pre-save mtime and 409s as "Obsidian changed this file" when
  // Air Date itself changed it (AD-014). Success with a Live flip adopts the
  // later Live-save state instead, in reportSendResult.
  function adoptFailedSendSaveState(result) {
    const saved = result?.saved_state;
    if (!saved || saved.mtime == null || !saved.content_hash) return;
    refreshEditorBaselineFromSave(saved);
    if (saved.old_id && saved.new_id && String(saved.old_id) !== String(saved.new_id)) {
      remapEssayId(saved.old_id, saved.new_id);
    }
  }

  function reportSendResult(result) {
    editorOutput.textContent = JSON.stringify(result, null, 2);
    const warningsNote = transportWarningsNote(result);
    if (result.ok) {
      // The server promotes edit_url onto the result; no stdout re-parse.
      const editUrl = String(result.edit_url || '').trim();
      const liveNote = result.status_after_send === 'Live' ? ' status is now live.' : '';
      if (editUrl) {
        setEditorStatusWithLink('draft created: ', editUrl, 'open in Substack ↗', `.${liveNote}${warningsNote}`, 'good');
        renderEditorDraftLink(editUrl);
      } else {
        setEditorStatus(`draft created in Substack.${liveNote}${warningsNote}`, 'good');
      }
      if (result.status_after_send === 'Live') {
        // The server flipped status → Live and rewrote the file; adopt the new
        // file state so the open editor doesn't 409 on its next save.
        if (editorForm.elements.status) editorForm.elements.status.value = 'Live';
        refreshEditorBaselineFromSave(result);
      }
      touchEssay(state.selectedId, 'send_to_substack');
      loadEssays();
      return true;
    }
    adoptFailedSendSaveState(result);
    // The server classifies every failure. The label follows the typed
    // error_kind, never a pattern match over the stringified result: the
    // result always embeds a preflight check keyed `substack_session`, which
    // is what made every failure look like an expired session.
    switch (result.error_kind) {
      case 'blocked':
        markPreflightBlockers(result.preflight || {});
        setEditorStatus(preflightStatusMessage(result.preflight || {}), 'bad');
        break;
      case 'auth':
        setEditorStatus(`Substack refused the session: ${result.message || 'sign in again through Obsidian.'}`, 'bad');
        break;
      case 'timeout':
        setEditorStatus(`send timed out: ${result.message || 'check Substack for the draft before sending again.'}${warningsNote}`, 'bad');
        break;
      default:
        setEditorStatus(`${result.message || 'saved locally, but the Substack draft was not created.'}${warningsNote}`, 'bad');
    }
    return false;
  }

  document.getElementById('editor-send').addEventListener('click', async () => {
    if (!state.selectedId) return;
    setEditorStatus('checking pipeline...', 'pending');
    try {
      // Preflight: is the pipeline ready, and is Substack connected?
      const status = await getJson('/api/substack/status').catch(() => ({}));
      if (!validateEditor('send', status)) return;
      if (status && !status.connected) {
        await connectSubstack('Substack isn\'t connected yet.');
      }

      // The server persists changed fields before it evaluates readiness. A
      // client-side preflight here would inspect the previous file state and
      // reject a complete editor form before AirDate has had a chance to save
      // it to its canonical Obsidian note.
      setEditorStatus('saving to Obsidian and checking readiness...', 'pending');
      let result = await postJson(`/api/essays/${state.selectedId}/send`, {
        ...saveRequestPayload(),
        publish: frontmatterPayload(),
      });

      // Only a typed auth failure earns the sign-in window, and only a
      // completed reconnect earns the single retry. Blocked, timeout and
      // transport failures never re-post: a timed-out send may already have
      // created the draft, and a blind retry is the duplicate-draft path.
      if (!result.ok && result.error_kind === 'auth') {
        // The refused send already saved the form; the retry must carry that
        // file state or it 409s against Air Date's own write.
        adoptFailedSendSaveState(result);
        const reconnected = await connectSubstack('Your Substack session looks expired.');
        if (!reconnected) {
          // The sign-in status line is the instruction that matters now.
          editorOutput.textContent = JSON.stringify(result, null, 2);
          return;
        }
        setEditorStatus('reconnected, retrying...', 'pending');
        result = await postJson(`/api/essays/${state.selectedId}/send`, {
          ...saveRequestPayload(),
          publish: frontmatterPayload(),
        });
      }

      reportSendResult(result);
    } catch (err) {
      if (err.status === 409) showEditorConflict(err);
      else setEditorStatus(`failed: ${err.message}`, 'bad');
    }
  });

  editorForm.elements.tags?.addEventListener('input', () => renderEditorActiveTags());

  document.getElementById('editor-add-preset-tags')?.addEventListener('click', () => {
    const selected = editorTopicPresetEl?.value;
    if (!selected || !EDITOR_TOPIC_TAGS[selected]) return;
    setEditorTags([...currentEditorTags(), ...EDITOR_TOPIC_TAGS[selected]]);
    setEditorStatus(`added preset: ${selected}`, 'good');
  });

  document.getElementById('editor-clear-tags')?.addEventListener('click', () => {
    setEditorTags([]);
    setEditorStatus('tags cleared', '');
  });


  populateEditorTagControls();
  populateAllIdeasFilters();
  bindSettingsEvents();
  wireEditorDropFields();
}






function reportBootError(message) {
  if (gardenCountsEl) gardenCountsEl.textContent = `failed to load: ${message}`;
  const tendingList = document.getElementById('tending-list');
  if (tendingList) {
    tendingList.innerHTML = `<li class="tending-empty">the catalog could not load: ${message}</li>`;
  }
}

// Settings drive the whole UI, so status loads before anything renders. On
// first run (no usable vault yet) the settings view is the only view.
async function refreshAppStatus() {
  const status = await getJson('/api/app/status');
  state.appStatus = status;
  state.setupRequired = Boolean(status.setup?.required);
  document.body.dataset.setup = state.setupRequired ? 'required' : 'done';
  applyUiConfig(status.config);
  return status;
}

async function init() {
  const requestedView = window.location.hash.replace(/^#/, '');
  if (!ROUTE_VIEW && VIEWS.includes(requestedView)) state.view = requestedView;
  bindEvents();
  await refreshAppStatus();
  if (state.setupRequired) {
    switchView('settings', { persist: false });
    return;
  }
  switchView(state.view, { persist: !ROUTE_VIEW });
  await loadEssays();
  await handleInitialEssayDeepLink();
}

init().catch((error) => {
  console.error(error);
  reportBootError(error.message);
});
