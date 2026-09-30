// The essay editor's rules, as pure functions (build spec §8, §15.2).
//
// Nothing in here touches the DOM or the network, so node can test all of it
// (tests/test_room_editor_js.py). editor-view.js draws the page and owns the
// round trips.
//
// The essay lives in one state object that outlives every section of the
// page, so a section the writer has switched off loses nothing: its values
// are still here, and they are still saved. Nothing is read back out of the
// page the way the old editor did.
//
// Three rules keep a save from harming the note:
//   - a blank value deletes a key on the server, so only a field the writer
//     changed is ever sent, plus the draft details the note does not have yet;
//   - `status`, `scheduled_at` and `source_note` are never sent: the stamp is
//     read-only, the board schedules, and source note left the editor without
//     its key leaving the note;
//   - the dirty baseline is taken before airdate's suggested values are
//     filled in, so those suggestions are saved into the note, as the old
//     editor did.
(function installRoomEditor(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RoomEditor = api;
})(typeof globalThis === 'object' ? globalThis : this, function createRoomEditor() {
  // Every field the editor holds, by kind. The last four are not drawn: they
  // are draft details the note must carry before a send, so they are held
  // and saved like the rest (server.py PERSISTED_DRAFT_FIELDS).
  const FIELDS = {
    title: 'text',
    subtitle: 'text',
    summary: 'text',
    post_type: 'text',
    totem: 'key',
    hero_image: 'text',
    tags: 'list',
    slug: 'text',
    section: 'text',
    email_subject: 'text',
    email_preview_text: 'text',
    audience: 'text',
    publish_on_web: 'bool',
    send_email: 'bool',
    free_preview: 'bool',
    comment_permissions: 'text',
    test_email_recipients: 'list',
    seo_title: 'text',
    canonical_url: 'text',
    seo_description: 'text',
    thumbnail_prompt: 'text',
    notes: 'text',
    note_pad: 'key',
    note_color: 'key',
    publication: 'text',
    thumbnail_alt: 'text',
    social_title: 'text',
    social_description: 'text',
  };

  // Keys the editor never sends, whatever the state holds.
  const NEVER_SEND = new Set([
    'status', 'scheduled_at', 'source_note', 'published_date', 'substack_url',
    'substack_draft_id', 'substack_draft_url', 'airdate_uid', 'uid', 'body',
  ]);

  // What a control shows when the note says nothing: the old form's own
  // defaults, which it saved the same way.
  const CONTROL_DEFAULTS = {
    post_type: 'text',
    audience: 'everyone',
    publish_on_web: true,
    send_email: true,
    free_preview: false,
    comment_permissions: 'everyone',
  };

  // A complete Substack draft is a property of the note, so these are saved
  // whenever the editor holds a value the note does not have yet. Mirrors
  // server.py PERSISTED_DRAFT_FIELDS, plus tags, which the old editor saved
  // the same way.
  const PERSISTED_DRAFT_FIELDS = [
    'title', 'subtitle', 'summary', 'publication', 'audience',
    'comment_permissions', 'email_subject', 'email_preview_text',
    'hero_image', 'thumbnail_alt', 'seo_title', 'seo_description',
    'social_title', 'social_description', 'thumbnail_prompt', 'tags',
  ];

  // airdate's suggestions (server metadata_defaults), filled into a field
  // only while it is empty.
  const DEFAULTED_FIELDS = [
    'publication', 'subtitle', 'summary', 'seo_title', 'seo_description',
    'social_title', 'social_description', 'thumbnail_prompt', 'thumbnail_alt',
    'email_subject', 'email_preview_text', 'hero_image',
  ];

  // The switchable sections. Title, subtitle, summary, the script and post
  // settings are always on.
  const SECTION_KEYS = ['totem', 'on_the_board', 'advanced', 'email_comments', 'seo_social', 'thumbnail', 'notes'];
  const SECTION_NAMES = {
    advanced: 'advanced',
    email_comments: 'email and comments',
    seo_social: 'seo and social',
    thumbnail: 'thumbnail',
    notes: 'internal notes',
  };
  const PRESETS = {
    simplified: {
      totem: true, on_the_board: true, advanced: false, email_comments: false,
      seo_social: false, thumbnail: false, notes: false,
    },
    complete: {
      totem: true, on_the_board: true, advanced: true, email_comments: true,
      seo_social: true, thumbnail: true, notes: true,
    },
  };

  const PADS = ['sticky', 'paper', 'index'];
  const PAD_COLORS = ['canary', 'blue', 'orange', 'pink', 'green'];
  const TOTEM_NONE = 'none';

  const SCRIPT_HEIGHT_MIN = 240;
  const SCRIPT_HEIGHT_MAX = 2400;
  const WORDS_PER_MINUTE = 250;

  const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
  const WEEKDAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

  // ---- values ---------------------------------------------------------------

  function emptyOf(key) {
    const kind = FIELDS[key];
    if (kind === 'list') return [];
    if (kind === 'bool') return false;
    return '';
  }

  function toList(value) {
    const items = Array.isArray(value) ? value : String(value ?? '').split(',');
    const out = [];
    for (const item of items) {
      const text = String(item ?? '').trim();
      if (text && !out.includes(text)) out.push(text);
    }
    return out;
  }

  function normalize(key, value) {
    const kind = FIELDS[key];
    if (kind === 'list') return toList(value);
    if (kind === 'bool') return value === true || value === 'true' || value === 1;
    if (value === undefined || value === null) return '';
    const text = typeof value === 'string' ? value : String(value);
    if (kind === 'key') return text.trim().toLowerCase();
    // Text is compared as the server stores it: trimmed.
    return text.trim();
  }

  function equal(a, b) {
    if (Array.isArray(a) || Array.isArray(b)) {
      const left = Array.isArray(a) ? a : [];
      const right = Array.isArray(b) ? b : [];
      return left.length === right.length && left.every((value, index) => value === right[index]);
    }
    return a === b;
  }

  function isBlank(value) {
    if (Array.isArray(value)) return value.length === 0;
    if (typeof value === 'boolean') return false;
    return String(value ?? '').trim() === '';
  }

  function copyValues(values) {
    const out = {};
    for (const key of Object.keys(FIELDS)) {
      const value = values[key];
      out[key] = Array.isArray(value) ? [...value] : value;
    }
    return out;
  }

  function has(frontmatter, key) {
    return Object.prototype.hasOwnProperty.call(frontmatter || {}, key);
  }

  // The state for an essay the server just returned (GET /api/essays/<id>).
  function fromEssay(essay) {
    const data = essay || {};
    const frontmatter = data.frontmatter && typeof data.frontmatter === 'object' ? data.frontmatter : {};
    const baseline = {};
    for (const key of Object.keys(FIELDS)) {
      if (has(frontmatter, key) && frontmatter[key] !== null && frontmatter[key] !== '') {
        baseline[key] = normalize(key, frontmatter[key]);
      } else if (key in CONTROL_DEFAULTS) {
        baseline[key] = CONTROL_DEFAULTS[key];
      } else {
        baseline[key] = emptyOf(key);
      }
    }
    // The baseline is taken here, before airdate fills anything in, so what
    // it fills in counts as a change and is saved into the note.
    const values = copyValues(baseline);
    for (const key of ['title', 'subtitle', 'summary']) {
      if (isBlank(values[key]) && !isBlank(data[key])) values[key] = normalize(key, data[key]);
    }
    const defaults = data.metadata_defaults && typeof data.metadata_defaults === 'object' ? data.metadata_defaults : {};
    for (const key of DEFAULTED_FIELDS) {
      if (isBlank(values[key]) && !isBlank(defaults[key])) values[key] = normalize(key, defaults[key]);
    }
    if (isBlank(values.tags) && Array.isArray(defaults.tags) && defaults.tags.length) {
      values.tags = toList(defaults.tags);
    }
    const body = String(data.body ?? '');
    return {
      id: String(data.id || ''),
      values,
      baseline,
      // What the page showed when it opened: the close guard asks only about
      // what the writer changed since, never about airdate's suggestions.
      opened: copyValues(values),
      body,
      bodyBaseline: body,
      bodyOpened: body,
      frontmatter: { ...frontmatter },
      fileState: { mtime: data.mtime ?? null, content_hash: data.content_hash ?? null },
    };
  }

  function changedFields(values, baseline) {
    return Object.keys(FIELDS).filter((key) => !equal(normalize(key, values[key]), normalize(key, baseline[key])));
  }

  // Draft details the editor holds that the note does not have yet.
  function missingDetails(state) {
    const frontmatter = state.frontmatter || {};
    return PERSISTED_DRAFT_FIELDS.filter((key) => {
      const value = normalize(key, state.values[key]);
      if (isBlank(value)) return false;
      return !has(frontmatter, key) || isBlank(frontmatter[key]);
    });
  }

  // What a save sends. Only what changed, plus the draft details the note is
  // missing; never a key in NEVER_SEND; the body only when it changed.
  function savePayload(state) {
    const updates = {};
    for (const key of changedFields(state.values, state.baseline)) {
      updates[key] = normalize(key, state.values[key]);
    }
    for (const key of missingDetails(state)) {
      updates[key] = normalize(key, state.values[key]);
    }
    // "none" is how the server hears "take the totem off": a blank totem is
    // dropped there so a note's own totem is never lost by accident.
    if ('totem' in updates && updates.totem === '') updates.totem = TOTEM_NONE;
    for (const key of Object.keys(updates)) {
      if (NEVER_SEND.has(key) || !(key in FIELDS)) delete updates[key];
    }
    const payload = {
      updates,
      expected_mtime: state.fileState ? state.fileState.mtime : null,
      expected_content_hash: state.fileState ? state.fileState.content_hash : null,
    };
    if (state.body !== state.bodyBaseline) payload.body = state.body;
    return payload;
  }

  // Would a save write anything? This is what "unsaved changes" means.
  function isDirty(state) {
    const payload = savePayload(state);
    return Object.keys(payload.updates).length > 0 || 'body' in payload;
  }

  // Has the writer changed anything since the page opened? Only this asks
  // before closing: airdate's own suggestions are made again on the next open.
  function writerEdited(state) {
    return state.body !== state.bodyOpened || changedFields(state.values, state.opened).length > 0;
  }

  // What the page was showing when save was pressed. The writer can keep
  // typing while it is in flight; only this much is known to be on disk.
  function snapshot(state) {
    return { values: copyValues(state.values), body: state.body };
  }

  // After a save: the sent values are the new baseline, the file state and id
  // are the server's, and the note's frontmatter is what it wrote.
  function adoptSave(state, sent, result) {
    const res = result || {};
    const next = {
      ...state,
      id: String(res.new_id || state.id),
      baseline: copyValues(sent.values),
      opened: copyValues(sent.values),
      bodyBaseline: sent.body,
      bodyOpened: sent.body,
      frontmatter: res.saved_frontmatter && typeof res.saved_frontmatter === 'object'
        ? { ...res.saved_frontmatter }
        : { ...state.frontmatter },
      fileState: { mtime: res.mtime ?? null, content_hash: res.content_hash ?? null },
    };
    return next;
  }

  // After attach-hero, which wrote the image into the vault and saved these
  // keys to the note itself: they are on disk now, and the rest of what the
  // writer has typed is still unsaved.
  function adoptHero(state, result) {
    const res = result || {};
    const saved = res.saved || {};
    const updates = res.updates || {};
    const values = copyValues(state.values);
    const baseline = copyValues(state.baseline);
    const opened = copyValues(state.opened);
    for (const [key, value] of Object.entries(updates)) {
      if (!(key in FIELDS)) continue;
      values[key] = normalize(key, value);
      baseline[key] = normalize(key, value);
      opened[key] = normalize(key, value);
    }
    return {
      ...state,
      id: String(saved.new_id || state.id),
      values,
      baseline,
      opened,
      frontmatter: saved.saved_frontmatter && typeof saved.saved_frontmatter === 'object'
        ? { ...saved.saved_frontmatter }
        : { ...state.frontmatter, ...updates },
      fileState: { mtime: saved.mtime ?? null, content_hash: saved.content_hash ?? null },
    };
  }

  // ---- sections -------------------------------------------------------------

  // Which sections show. A preset mode is exactly its preset; custom starts
  // from simplified and takes the writer's switches. Totem shows only when
  // the writer uses totems.
  function sectionVisibility(editorConfig, options) {
    const cfg = editorConfig && typeof editorConfig === 'object' ? editorConfig : {};
    const opts = options || {};
    const mode = cfg.mode === 'complete' || cfg.mode === 'custom' ? cfg.mode : 'simplified';
    const out = { ...PRESETS[mode === 'complete' ? 'complete' : 'simplified'] };
    if (mode === 'custom' && cfg.sections && typeof cfg.sections === 'object') {
      for (const key of SECTION_KEYS) {
        if (typeof cfg.sections[key] === 'boolean') out[key] = cfg.sections[key];
      }
    }
    if (opts.totemsEnabled === false) out.totem = false;
    return { mode, ...out };
  }

  function joinWords(items) {
    if (items.length <= 1) return items.join('');
    return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
  }

  // The line under the page that says what is switched off, or '' when
  // nothing is.
  function hiddenSentence(visibility) {
    const off = Object.keys(SECTION_NAMES).filter((key) => !visibility[key]).map((key) => SECTION_NAMES[key]);
    if (!off.length) return '';
    const mode = visibility.mode === 'custom' ? 'custom' : visibility.mode === 'complete' ? 'complete' : 'simplified';
    const verb = off.length === 1 ? 'is' : 'are';
    return `you chose ${mode} metadata. ${joinWords(off)} ${verb} switched off.`;
  }

  // ---- the script -----------------------------------------------------------

  function wordCount(text) {
    const matches = String(text || '').match(/[\p{L}\p{N}_]+/gu);
    return matches ? matches.length : 0;
  }

  function wordLine(text) {
    const words = wordCount(text);
    const minutes = Math.max(1, Math.round(words / WORDS_PER_MINUTE));
    const noun = words === 1 ? 'word' : 'words';
    return `${words.toLocaleString('en-US')} ${noun} · about ${minutes} min`;
  }

  function clampScriptHeight(value) {
    const n = Math.round(Number(value));
    if (!Number.isFinite(n)) return null;
    return Math.min(SCRIPT_HEIGHT_MAX, Math.max(SCRIPT_HEIGHT_MIN, n));
  }

  // ---- tags -----------------------------------------------------------------

  function addTags(tags, more) {
    return toList([...(Array.isArray(tags) ? tags : []), ...toList(more)]);
  }

  function removeTag(tags, tag) {
    return (Array.isArray(tags) ? tags : []).filter((item) => item !== tag);
  }

  // ---- the board note -------------------------------------------------------

  function noteFor(values, board) {
    const defaults = board && typeof board === 'object' ? board : {};
    const pad = normalize('note_pad', values && values.note_pad);
    const color = normalize('note_color', values && values.note_color);
    const fallbackPad = PADS.includes(defaults.default_pad) ? defaults.default_pad : 'sticky';
    const fallbackColor = PAD_COLORS.includes(defaults.default_color) ? defaults.default_color : 'canary';
    return {
      pad: PADS.includes(pad) ? pad : fallbackPad,
      color: PAD_COLORS.includes(color) ? color : fallbackColor,
    };
  }

  // ---- dates and the status line --------------------------------------------

  function calendarDay(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || '').trim());
    if (!match) return null;
    const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
    const check = new Date(Date.UTC(year, month - 1, day));
    if (check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day) return null;
    return { year, month, day, weekday: check.getUTCDay() };
  }

  function formatDay(value) {
    const parts = calendarDay(value);
    return parts ? `${WEEKDAYS[parts.weekday]} ${MONTHS[parts.month - 1]} ${parts.day}` : '';
  }

  function localIso(date) {
    const d = date instanceof Date ? date : new Date(date);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  // The air date block in the right rail. The board is the only place that
  // schedules, so this says where it is and how to move it, and nothing more.
  function airDate(essay) {
    const status = String(essay && essay.status || '');
    const day = formatDay(essay && essay.scheduled_at);
    if ((status === 'Ready for Air' || status === 'Live') && day) {
      return {
        onBoard: true,
        text: day,
        hint: status === 'Live' ? '' : 'to move it, unschedule on the board and drag it to another week.',
      };
    }
    return { onBoard: false, text: 'not on the board yet', hint: '' };
  }

  // The line under the title in the header.
  function statusLine(essay, now) {
    const status = String(essay && essay.status || '');
    const scheduled = calendarDay(essay && essay.scheduled_at) ? String(essay.scheduled_at).slice(0, 10) : '';
    const today = localIso(now === undefined ? new Date() : now);
    if (status === 'Writers Likey') return 'in writers likey. put it on the board to give it an air date.';
    if (status === 'Ready for Air') {
      if (!scheduled) return 'ready for air.';
      if (scheduled === today) return 'on the board. airs today.';
      if (scheduled < today) return `on the board. aired ${formatDay(scheduled)}, not marked live yet.`;
      return `on the board. airs ${formatDay(scheduled)}.`;
    }
    if (status === 'Live') return scheduled ? `live. aired ${formatDay(scheduled)}.` : 'live.';
    if (status === 'Archived') return 'saved for a rainy day.';
    return 'in the writers room. move it to writers likey before it can be scheduled.';
  }

  // "saved to obsidian 2:14pm"
  function savedLine(date) {
    const d = date instanceof Date ? date : new Date(date);
    let hours = d.getHours();
    const suffix = hours >= 12 ? 'pm' : 'am';
    hours %= 12;
    if (hours === 0) hours = 12;
    return `saved to obsidian ${hours}:${String(d.getMinutes()).padStart(2, '0')}${suffix}`;
  }

  // ---- the deep link --------------------------------------------------------

  function essayFromSearch(search) {
    const params = new URLSearchParams(search || '');
    return String(params.get('essay') || '').trim();
  }

  function editorUrl(id) {
    return `/airdate/room?essay=${encodeURIComponent(String(id || ''))}`;
  }

  return {
    FIELDS,
    NEVER_SEND,
    CONTROL_DEFAULTS,
    PERSISTED_DRAFT_FIELDS,
    SECTION_KEYS,
    PRESETS,
    PADS,
    PAD_COLORS,
    SCRIPT_HEIGHT_MIN,
    SCRIPT_HEIGHT_MAX,
    normalize,
    equal,
    fromEssay,
    changedFields,
    missingDetails,
    savePayload,
    isDirty,
    writerEdited,
    snapshot,
    adoptSave,
    adoptHero,
    sectionVisibility,
    hiddenSentence,
    wordCount,
    wordLine,
    clampScriptHeight,
    addTags,
    removeTag,
    noteFor,
    formatDay,
    airDate,
    statusLine,
    savedLine,
    essayFromSearch,
    editorUrl,
  };
});
