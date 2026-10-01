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
  // Every field the editor holds, by kind. `publication` is not drawn: it is
  // a draft detail the note must carry before a send, so it is held and
  // saved like the rest (server.py PERSISTED_DRAFT_FIELDS). The social
  // fields, alt text and free unlock date are drawn in their sections (slice
  // 7): the send reads all of them from the note's frontmatter.
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
    social_image: 'text',
    free_unlock_at: 'text',
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

  // ---- the gear sheet (slice 4c) ---------------------------------------------

  // What the sheet shows: the mode (custom when the writer flipped a switch)
  // and where each switch sits. The switches are the setting itself, before
  // the app-wide "no totems" is applied, so turning totems back on in
  // settings brings the writer's choice back.
  function gearState(editorConfig, options) {
    const opts = options || {};
    const visible = sectionVisibility(editorConfig);
    const switches = {};
    for (const key of SECTION_KEYS) switches[key] = Boolean(visible[key]);
    return { mode: visible.mode, switches, totemsEnabled: opts.totemsEnabled !== false };
  }

  // Picking a preset is the whole setting: its sections follow the preset.
  function presetSettings(mode) {
    return { mode: mode === 'complete' ? 'complete' : 'simplified', sections: {} };
  }

  // Flipping one switch makes the mode custom and writes every switch, so
  // custom shows exactly what the sheet showed plus the one change.
  function flipSection(editorConfig, key) {
    if (!SECTION_KEYS.includes(key)) return null;
    const { switches } = gearState(editorConfig);
    switches[key] = !switches[key];
    return { mode: 'custom', sections: switches };
  }

  // ---- the editor tour (slice 4c) -------------------------------------------

  const TOUR_PHASES = {
    'writers room': 'writers room',
    'writers likey': 'writers likey',
    'ready for air': 'ready for air',
    live: 'live',
    archived: 'saved for a rainy day',
  };

  // Step 1 points at the stamp. The artboard's "writers room today." is only
  // true of an essay in the writers room, so the first sentence names the
  // phase this essay is in.
  function stampTourText(status) {
    const rest = 'airdate stamps it when you star it, when you schedule it, and when it airs. you never set it by hand.';
    const name = TOUR_PHASES[String(status || '').trim().toLowerCase()];
    return name ? `${name} today. ${rest}` : rest;
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

  // "2:14pm", local time.
  function clockTime(date) {
    const d = date instanceof Date ? date : new Date(date);
    let hours = d.getHours();
    const suffix = hours >= 12 ? 'pm' : 'am';
    hours %= 12;
    if (hours === 0) hours = 12;
    return `${hours}:${String(d.getMinutes()).padStart(2, '0')}${suffix}`;
  }

  // "saved to obsidian 2:14pm"
  function savedLine(date) {
    return `saved to obsidian ${clockTime(date)}`;
  }

  // ---- send: the readiness list ---------------------------------------------
  //
  // The server's preflight is the only judge of what Substack would reject.
  // These functions only route its rows: which belong to the list, which to
  // the connection gate, which fold together, and where each one sends the
  // writer. No check is added here that the server does not make.

  // Where a row puts the writer: the id of the control for a field.
  const FIELD_TARGETS = {
    title: 'ed-f-title',
    subtitle: 'ed-f-subtitle',
    summary: 'ed-f-summary',
    body: 'ed-f-body',
    hero_image: 'ed-hero-file',
    tags: 'ed-tag-input',
    slug: 'ed-f-slug',
    section: 'ed-f-section',
    email_subject: 'ed-f-email_subject',
    email_preview_text: 'ed-f-email_preview_text',
    comment_permissions: 'ed-f-comment_permissions',
    audience: 'ed-f-audience-everyone',
    seo_title: 'ed-f-seo_title',
    seo_description: 'ed-f-seo_description',
    canonical_url: 'ed-f-canonical_url',
    social_title: 'ed-f-social_title',
    social_description: 'ed-f-social_description',
    social_image: 'ed-social-file',
    thumbnail_prompt: 'ed-f-thumbnail_prompt',
    thumbnail_alt: 'ed-f-thumbnail_alt',
    free_unlock_at: 'ed-f-free_unlock_at',
  };

  // Details the note must carry that airdate fills in from these fields when
  // the essay opens. Their own controls sit in switchable sections; while a
  // section is off, the row sends the writer to the field it is filled from.
  const FILLED_FROM = {
    thumbnail_alt: 'title',
    social_title: 'title',
    social_description: 'summary',
  };

  // The switchable section a field sits in, when it is not always shown.
  const FIELD_SECTIONS = {
    slug: 'advanced',
    section: 'advanced',
    email_subject: 'email_comments',
    email_preview_text: 'email_comments',
    audience: 'email_comments',
    comment_permissions: 'email_comments',
    seo_title: 'seo_social',
    seo_description: 'seo_social',
    canonical_url: 'seo_social',
    social_title: 'seo_social',
    social_description: 'seo_social',
    social_image: 'seo_social',
    thumbnail_prompt: 'thumbnail',
    thumbnail_alt: 'thumbnail',
    free_unlock_at: 'advanced',
  };

  const FIELD_NAMES = {
    title: 'the title',
    subtitle: 'the subtitle',
    summary: 'the summary',
    body: 'the script',
    hero_image: 'the hero image',
    tags: 'tags',
    audience: 'who gets it',
    comment_permissions: 'comments',
    email_subject: 'the email subject',
    email_preview_text: 'the email preview text',
    seo_title: 'the seo title',
    seo_description: 'the seo description',
    thumbnail_prompt: 'the thumbnail prompt',
    thumbnail_alt: 'the thumbnail alt text',
    social_title: 'the social title',
    social_description: 'the social description',
    social_image: 'the social image',
    free_unlock_at: 'the free unlock date',
    publication: 'your substack address',
  };

  // What an empty field's row says, in the room's voice.
  const EMPTY_SENTENCES = {
    title: ['the title is empty', 'write it'],
    subtitle: ['the subtitle is empty. substack shows it under the title', 'write it'],
    summary: ['the summary is empty. substack uses it as the preview', 'write it'],
    body: ['the script is empty', 'write it'],
    hero_image: ['the hero image is missing', 'attach'],
    tags: ['there are no tags. substack needs at least one', 'add one'],
  };

  // Order on the page, top to bottom. Save sits in the header.
  const PAGE_ORDER = [
    'ed-save', 'ed-f-title', 'ed-f-subtitle', 'ed-f-summary', 'ed-f-body', 'ed-hero-file', 'ed-tag-input',
    'ed-f-slug', 'ed-f-section', 'ed-f-free_unlock_at', 'ed-f-email_subject', 'ed-f-email_preview_text',
    'ed-f-audience-everyone', 'ed-f-comment_permissions', 'ed-f-seo_title', 'ed-f-canonical_url', 'ed-f-seo_description',
    'ed-f-social_title', 'ed-f-social_description', 'ed-social-file', 'ed-f-thumbnail_prompt', 'ed-f-thumbnail_alt',
  ];

  // Rows that are about the connection, not the note. They shut gate 1.
  const CONNECTION_KEYS = new Set(['publication', 'substack_command', 'substack_session']);

  // A blocker the server keys to the note directly, and the field it names.
  const DIRECT_FIELDS = { title: 'title', subtitle: 'subtitle', hero: 'hero_image', hero_path: 'hero_image', body: 'body' };

  // What a body-fidelity finding is, short enough for one row.
  const BODY_KINDS = {
    table: 'a table. substack has no tables',
    block_swallowed: 'this block would be dropped',
    embed: 'an obsidian embed substack cannot show',
    local_image: 'an image that is only on this computer',
  };

  function lowerFirst(text) {
    const value = String(text || '').trim();
    return value ? value.charAt(0).toLowerCase() + value.slice(1) : '';
  }

  function plural(n, one, many) {
    return `${n} ${n === 1 ? one : many}`;
  }

  function rowFor(key, text, target, action, extra) {
    return { key, text, target: target || '', action: action || '', line: null, ...(extra || {}) };
  }

  // A detail the note does not have, as a row. What the editor holds
  // decides it: a value the writer only has to save folds into one row;
  // an empty one sends them to the field.
  function emptyRow(field, visibility) {
    const own = FIELD_SECTIONS[field];
    const ownShowing = Boolean(own && visibility && visibility[own] !== false);
    if (FILLED_FROM[field] && !ownShowing) {
      const from = FILLED_FROM[field];
      return rowFor(`persisted_${field}`, `${FIELD_NAMES[field]} is empty. airdate fills it in from ${FIELD_NAMES[from]}`, FIELD_TARGETS[from], 'write it');
    }
    const [text, action] = EMPTY_SENTENCES[field] || [`${FIELD_NAMES[field] || field.replace(/_/g, ' ')} is empty`, 'fill it in'];
    const section = FIELD_SECTIONS[field];
    if (section && visibility && visibility[section] === false) {
      return rowFor(`persisted_${field}`, `${text}. it is in ${SECTION_NAMES[section] || section}, which is switched off`, '', '');
    }
    return rowFor(`persisted_${field}`, text, FIELD_TARGETS[field] || '', FIELD_TARGETS[field] ? action : '');
  }

  function bodyRow(blocker) {
    const key = String(blocker.key || '');
    if (key === 'body_blockers_more') {
      const count = Number.isInteger(blocker.count) ? blocker.count : 0;
      const text = count ? `and ${plural(count, 'more problem', 'more problems')} in the script` : 'and more problems in the script';
      return rowFor(key, text, 'ed-f-body', 'go to it');
    }
    const kind = key.slice(5, key.lastIndexOf('_'));
    const line = Number.isInteger(blocker.line) ? blocker.line : null;
    const what = BODY_KINDS[kind] || lowerFirst(String(blocker.message || '').replace(/^line \d+:\s*/i, ''));
    const text = line ? `line ${line}: ${what}` : what;
    return rowFor(key, text, 'ed-f-body', 'go to it', { line });
  }

  // The list the rail shows for a preflight.
  //   values      what the editor holds now (state.values)
  //   visibility  which sections are on (sectionVisibility)
  //   body        the script the editor holds now (state.body)
  // Returns { source, rows, connection } where `connection` holds the keys
  // that shut gate 1 instead of joining the list.
  function readinessList(preflight, values, visibility, body) {
    const result = preflight && typeof preflight === 'object' ? preflight : {};
    const blockers = Array.isArray(result.blockers) ? result.blockers : [];
    const held = values || {};
    const connection = blockers.filter((b) => CONNECTION_KEYS.has(String(b.key || ''))).map((b) => String(b.key));
    if (result.source_role === 'source' || blockers.some((b) => b.key === 'source_role')) {
      return {
        source: true,
        connection,
        rows: [rowFor('source_role', 'this is a source note. make a linked draft to send it', '', 'make a linked draft', { kind: 'source' })],
      };
    }
    const direct = new Set();
    for (const blocker of blockers) {
      const field = DIRECT_FIELDS[String(blocker.key || '')];
      if (field) direct.add(field);
    }
    const rows = [];
    const toSave = [];
    for (const blocker of blockers) {
      const key = String(blocker.key || '');
      if (CONNECTION_KEYS.has(key) || key === 'source_role') continue;
      if (key.startsWith('persisted_')) {
        const field = key.slice('persisted_'.length);
        // The same gap the server also named directly: one row, not two.
        if (direct.has(field)) continue;
        const holds = field === 'body' ? String(body || '').trim() !== '' : !isBlank(normalize(field, held[field]));
        if (!holds) rows.push(emptyRow(field, visibility));
        else toSave.push(field);
        continue;
      }
      if (key.startsWith('body_')) {
        rows.push(bodyRow(blocker));
        continue;
      }
      const field = DIRECT_FIELDS[key];
      if (field === 'hero_image' && key === 'hero_path') {
        rows.push(rowFor(key, 'the hero image is not in your vault any more', FIELD_TARGETS.hero_image, 'attach'));
        continue;
      }
      if (field) {
        const [text, action] = EMPTY_SENTENCES[field];
        rows.push(rowFor(key, text, FIELD_TARGETS[field], action));
        continue;
      }
      // Anything this list does not know yet still shows, in the server's words.
      const target = FIELD_TARGETS[String(blocker.field || '')] || '';
      rows.push(rowFor(key, lowerFirst(blocker.message) || key.replace(/_/g, ' '), target, target ? 'fix it' : ''));
    }
    if (toSave.length) {
      rows.push(rowFor('persisted', `press save: ${plural(toSave.length, 'detail is', 'details are')} not in the note yet`, 'ed-save', 'save', { fields: toSave }));
    }
    const rank = (row) => {
      const at = PAGE_ORDER.indexOf(row.target);
      return at === -1 ? PAGE_ORDER.length : at;
    };
    rows.sort((a, b) => rank(a) - rank(b) || (a.line || 0) - (b.line || 0));
    return { source: false, connection, rows };
  }

  // "3 things to fix. checked 2:14pm" or "all clear. checked 2:14pm".
  function readinessHeading(count, checkedAt) {
    const time = clockTime(checkedAt);
    return count ? `${plural(count, 'thing', 'things')} to fix. checked ${time}` : `all clear. checked ${time}`;
  }

  // Where the Nth line of the script starts and ends in the textarea. The
  // server counts from the first non-blank line, as the script is sent.
  function lineRange(body, line) {
    const text = String(body || '');
    const first = text.search(/\S/);
    const skipped = first === -1 ? 0 : (text.slice(0, first).match(/\n/g) || []).length;
    const lines = text.split('\n');
    const index = Math.min(Math.max(0, skipped + Math.max(1, Number(line) || 1) - 1), lines.length - 1);
    let start = 0;
    for (let i = 0; i < index; i += 1) start += lines[i].length + 1;
    return { start, end: start + lines[index].length, index };
  }

  // ---- send: the three gates ------------------------------------------------
  //
  // 1 connected, which includes a real substack address; 2 on the board;
  // 3 readiness clear. Send is disabled while any is shut. The line under
  // the button names the first shut gate in full and the rest after it.

  // Gate 1, from /api/substack/status (null when it could not be read) and
  // the connection rows of the last preflight.
  function connectionGate(substack, connectionKeys) {
    const keys = new Set(connectionKeys || []);
    if (substack === undefined) return { problem: 'checking substack', action: 'wait a moment', clause: 'wait a moment', pending: true };
    if (!substack || typeof substack !== 'object') {
      return { problem: 'airdate could not check substack', action: 'press check readiness to look again', clause: 'check again' };
    }
    const connector = substack.connector || {};
    if (!substack.connected) {
      if (!connector.paired) return { problem: 'the obsidian connector is not paired', action: 'pair it in obsidian', clause: 'pair it' };
      if (!connector.available) return { problem: 'obsidian is not answering', action: 'open obsidian with the airdate connector on', clause: 'open obsidian' };
      return { problem: 'substack is not connected', action: 'connect it through obsidian', clause: 'connect it' };
    }
    if (!substack.publication_configured) return { problem: 'airdate has no substack address yet', action: 'add it in settings', clause: 'add it' };
    if (keys.has('publication')) {
      return { problem: 'this note names a placeholder substack address', action: 'fix publication in the note in obsidian', clause: 'fix it' };
    }
    if (keys.has('substack_command') || keys.has('substack_session')) {
      return { problem: 'the obsidian connector stopped answering', action: 'check obsidian, then check readiness again', clause: 'check obsidian' };
    }
    return null;
  }

  // Gate 2. The server has no "on the board" check for send; the old page
  // kept this in the browser too.
  const SEND_PHASES = ['Ready for Air', 'Live'];

  function boardGate(status) {
    const phase = String(status || '');
    if (SEND_PHASES.includes(phase)) return null;
    if (phase === 'Archived') return { problem: 'saved for a rainy day', action: 'bring it back to the room, then schedule it', clause: 'bring it back and schedule it' };
    if (phase === 'Writers Likey') return { problem: 'not on the board yet', action: 'schedule it', clause: 'schedule it' };
    return { problem: 'not on the board yet', action: 'star it for writers likey, then schedule it', clause: 'star it and schedule it' };
  }

  // Gate 3. readiness: { state: 'loading' | 'error' | 'done', count, source }.
  // How many readiness rows shut the send button. The 'press save' row is not
  // one of them: pressing send saves first, which fills in exactly those
  // details, so it would only force a save and then a send. It stays in the
  // list, because it is still true; it just does not block. (Sean, 2026-09-30.)
  function blockingCount(rows) {
    if (!Array.isArray(rows)) return 0;
    return rows.filter((row) => row && row.key !== 'persisted').length;
  }

  function readinessGate(readiness) {
    const r = readiness || {};
    if (r.state === 'loading' || !r.state) return { problem: 'checking readiness', action: 'wait a moment', clause: 'wait for readiness', pending: true };
    if (r.state === 'error') return { problem: 'readiness was not checked', action: 'press check readiness', clause: 'check readiness' };
    if (r.source) return { problem: '', action: 'make a linked draft below', clause: 'make a linked draft below' };
    const count = Number(r.count) || 0;
    if (!count) return null;
    const things = `fix ${plural(count, 'thing', 'things')} below`;
    return { problem: '', action: things, clause: things };
  }

  // All three, and the line under the button ('' when send is open).
  function sendGates(input) {
    const opts = input || {};
    const gates = [
      ['connected', connectionGate(opts.substack, opts.connection)],
      ['board', boardGate(opts.status)],
      ['readiness', readinessGate(opts.readiness)],
    ].filter(([, gate]) => gate);
    if (!gates.length) return { open: true, shut: [], line: '' };
    if (gates.some(([, gate]) => gate.pending)) {
      return { open: false, shut: gates.map(([name]) => name), line: 'checking whether this can go to substack…' };
    }
    const [[, first], ...rest] = gates;
    let line = first.problem ? `${first.problem}. ${first.action}` : first.action;
    // One "then" per line: an action that already has one takes the rest with "and".
    const joiner = first.action.includes(', then ') ? ' and ' : ', then ';
    line += rest.length ? `${joiner}${rest.map(([, gate]) => gate.clause).join(' and ')}` : ' to send';
    return { open: false, shut: gates.map(([name]) => name), line: `${line}.` };
  }

  // The substack line at the top of the rail. `refused` mirrors an auth
  // failure from the last send, so it shows from anywhere in the rail.
  function connectionLine(substack, refused) {
    if (substack === undefined) return { tone: 'checking', name: 'checking substack…', sub: '' };
    if (!substack || typeof substack !== 'object') {
      return { tone: 'off', name: 'substack status unknown', sub: 'airdate could not check. check readiness looks again.' };
    }
    const host = String(substack.publication || '').replace(/^https?:\/\//i, '').replace(/\/+$/, '');
    if (refused) return { tone: 'off', name: 'substack: session refused', sub: 'sign in to substack again through obsidian.' };
    const connector = substack.connector || {};
    if (substack.connected) {
      return { tone: 'connected', name: 'substack connected', sub: substack.publication_configured ? host : 'add your substack address in settings.' };
    }
    if (!connector.paired) return { tone: 'off', name: 'substack not connected', sub: 'pair the airdate connector in obsidian.' };
    if (!connector.available) return { tone: 'off', name: 'substack not connected', sub: 'open obsidian with the airdate connector on.' };
    return { tone: 'off', name: 'substack not connected', sub: 'connect substack through obsidian.' };
  }

  // ---- send: what the result says ------------------------------------------

  // Only a refused session earns the one automatic retry, and only after the
  // writer finished signing in again. A timeout never does: the draft may
  // already exist, and a blind retry is how a second copy gets made.
  function shouldRetrySend(result, attempts, reconnected) {
    return Boolean(result && !result.ok && result.error_kind === 'auth' && attempts === 1 && reconnected === true);
  }

  // The status code or reason, for the brackets. Display only: the failure
  // is classified by error_kind, never by this.
  function failureCode(result) {
    if (result && result.error_kind === 'timeout') return 'timed out';
    const text = `${(result && result.message) || ''} ${(result && result.error) || ''}`;
    const match = /\b([45]\d\d)\b/.exec(text);
    return match ? match[1] : '';
  }

  // Did the send get as far as asking the connector for a draft?
  function reachedConnector(result) {
    return Boolean(result && result.transport === 'obsidian_connector');
  }

  // The red box under the button: one sentence (who refused, why with the
  // code, whether anything was sent) and the actions that go with it.
  //   outcome  the /send result, or { error_kind: 'network' | 'http' | 'setup'
  //            | 'refused', message } when the request itself failed
  //   substack the /api/substack/status read after the failure, if any
  // Actions: 'connect' signs in through obsidian, 'retry' sends again,
  // 'open-substack' opens substack to check, 'forget' forgets a stale link.
  function sendFailure(outcome, substack, blockedCount) {
    const result = outcome || {};
    const kind = String(result.error_kind || 'transport');
    const code = failureCode(result);
    const bracket = code ? ` (${code})` : '';
    const check = 'check substack before you send again';
    if (kind === 'blocked') {
      const n = Number(blockedCount) || 0;
      const what = n ? `fix ${plural(n, 'thing', 'things')} below` : 'the readiness list has something to fix';
      return { sentence: `airdate stopped before substack: ${what}. nothing was sent.`, sent: 'no', actions: [], refused: false };
    }
    if (kind === 'auth') {
      const sentence = reachedConnector(result)
        ? `substack refused the session obsidian holds${bracket}. no draft was made.`
        : 'substack is not signed in inside obsidian. nothing was sent.';
      return { sentence, sent: 'no', actions: ['connect', 'retry'], refused: true };
    }
    if (kind === 'timeout') {
      return {
        sentence: `substack did not answer in time${bracket}. a draft may already exist, so ${check}.`,
        sent: 'maybe',
        actions: ['open-substack'],
        refused: false,
      };
    }
    if (kind === 'network') {
      return {
        sentence: `airdate stopped answering during the send. it cannot tell whether a draft was made, so ${check}.`,
        sent: 'maybe',
        actions: ['open-substack'],
        refused: false,
      };
    }
    if (kind === 'setup') {
      return { sentence: 'airdate is not set up yet. finish setup in settings. nothing was sent.', sent: 'no', actions: [], refused: false };
    }
    if (kind === 'refused') {
      return { sentence: `airdate would not send this: ${lowerFirst(result.message) || 'it refused'}. nothing was sent.`, sent: 'no', actions: [], refused: false };
    }
    if (kind === 'http') {
      return {
        sentence: `airdate hit a problem while sending${bracket}. it cannot tell whether a draft was made, so ${check}.`,
        sent: 'maybe',
        actions: ['open-substack'],
        refused: false,
      };
    }
    // transport
    const stale = String(result.stale_draft_id || '').trim();
    if (stale) {
      return {
        sentence: `substack no longer has draft ${stale}, so nothing was sent. if you deleted it there, forget the link and send this as a new draft.`,
        sent: 'no',
        actions: ['forget'],
        stale,
        refused: false,
      };
    }
    if (!reachedConnector(result)) {
      const connector = (substack && substack.connector) || {};
      if (substack && !connector.paired) {
        return { sentence: 'the obsidian connector is not paired, so nothing was sent. in obsidian, run "pair with airdate".', sent: 'no', actions: ['retry'], refused: false };
      }
      if (substack && !connector.available) {
        return { sentence: 'obsidian is not answering, so nothing was sent. open obsidian with the airdate connector on.', sent: 'no', actions: ['retry'], refused: false };
      }
      return { sentence: `the obsidian connector did not take the draft${bracket}, so nothing was sent.`, sent: 'no', actions: ['retry'], refused: false };
    }
    return {
      sentence: `the obsidian connector could not make the draft${bracket}. airdate cannot tell whether substack kept anything, so ${check}.`,
      sent: 'maybe',
      actions: ['open-substack'],
      refused: false,
    };
  }

  // Where "open substack" goes: the draft itself when the note knows it,
  // otherwise the publication's posts. '' when neither is known.
  function substackCheckUrl(substack, frontmatter) {
    const draft = String((frontmatter && frontmatter.substack_draft_url) || '').trim();
    if (/^https:\/\//i.test(draft)) return draft;
    const publication = String((substack && substack.publication) || '').trim().replace(/\/+$/, '');
    if (!publication || !(substack && substack.publication_configured)) return '';
    const base = /^https?:\/\//i.test(publication) ? publication : `https://${publication}`;
    return `${base}/publish/posts`;
  }

  // The green line after a draft lands.
  function sendSuccess(result) {
    const res = result || {};
    const url = String(res.edit_url || '').trim();
    const warnings = (Array.isArray(res.warnings) ? res.warnings : []).map(lowerFirst).filter(Boolean);
    return {
      text: res.updated ? 'the draft in substack is updated. airdate did not publish it.' : 'the draft is in substack. airdate did not publish it.',
      url: /^https:\/\//i.test(url) ? url : '',
      warnings,
    };
  }

  // After a send. The send saved first, so whenever it reports the state that
  // save left, the sent values are the new baseline, whatever became of the
  // send; and a draft it recorded wrote the note once more.
  //   sent     snapshot(state) taken when send was pressed
  //   payload  the savePayload sent with it
  function adoptSend(state, sent, payload, result) {
    const res = result || {};
    const saved = res.saved_state && typeof res.saved_state === 'object' ? res.saved_state : null;
    let next = { ...state, frontmatter: { ...state.frontmatter } };
    if (saved && saved.mtime != null) {
      next = {
        ...next,
        id: String(saved.new_id || state.id),
        baseline: copyValues(sent.values),
        opened: copyValues(sent.values),
        bodyBaseline: sent.body,
        bodyOpened: sent.body,
        fileState: { mtime: saved.mtime ?? null, content_hash: saved.content_hash ?? null },
      };
      const written = res.saved && typeof res.saved === 'object' && res.saved.saved_frontmatter;
      if (written && typeof written === 'object') {
        next.frontmatter = { ...written };
      } else {
        for (const [key, value] of Object.entries((payload && payload.updates) || {})) {
          if (isBlank(value) || (key === 'totem' && value === TOTEM_NONE)) delete next.frontmatter[key];
          else next.frontmatter[key] = Array.isArray(value) ? [...value] : value;
        }
      }
    }
    if (res.ok && res.draft_recorded) {
      next.fileState = { mtime: res.mtime ?? null, content_hash: res.content_hash ?? null };
      if (res.draft_id) next.frontmatter.substack_draft_id = String(res.draft_id);
      if (res.edit_url) next.frontmatter.substack_draft_url = String(res.edit_url);
    }
    return next;
  }

  // After forget-draft-link, which rewrote the note without the two keys
  // and answers with the essay as it is now.
  function adoptForget(state, result) {
    const res = result || {};
    const frontmatter = { ...state.frontmatter };
    delete frontmatter.substack_draft_id;
    delete frontmatter.substack_draft_url;
    return {
      ...state,
      id: String(res.new_id || state.id),
      frontmatter: res.essay && res.essay.frontmatter && typeof res.essay.frontmatter === 'object'
        ? { ...res.essay.frontmatter }
        : frontmatter,
      fileState: res.mtime != null ? { mtime: res.mtime, content_hash: res.content_hash ?? null } : state.fileState,
    };
  }

  // What preflight, preview and the thumbnail prompt are told the editor
  // holds, so they judge what a save would write, not only what is on disk.
  function publishValues(state) {
    const out = {};
    for (const key of Object.keys(FIELDS)) {
      if (NEVER_SEND.has(key)) continue;
      const value = normalize(key, state.values[key]);
      if (!isBlank(value)) out[key] = value;
    }
    return out;
  }

  // ---- the deep link --------------------------------------------------------

  function essayFromSearch(search) {
    const params = new URLSearchParams(search || '');
    return String(params.get('essay') || '').trim();
  }

  // ---- the send fields (slice 7) ---------------------------------------------

  // What a blank social title, social description or alt text falls back to,
  // shown as the field's placeholder. The same fallbacks as the server's
  // metadata_defaults, read from what the page holds now, so the hint follows
  // the title and summary as the writer types them.
  function sendFieldPlaceholders(values) {
    const v = values || {};
    const title = String(v.title || '').trim();
    const summary = String(v.summary || '').trim() || String(v.subtitle || '').trim();
    return {
      social_title: title,
      social_description: summary,
      thumbnail_alt: title,
    };
  }

  // A date field shows a day. A note can hold a full time ("2026-10-05T09:00")
  // that a date control would read as nothing, so the day is shown; the note
  // keeps its own value until the writer picks another day.
  function dayValue(value) {
    const text = String(value ?? '').trim();
    return /^\d{4}-\d{2}-\d{2}/.test(text) ? text.slice(0, 10) : '';
  }

  // The image the social image points at, as the page names it.
  function imageName(path) {
    const parts = String(path || '').trim().split('/');
    return parts[parts.length - 1] || '';
  }

  // An image /api/upload-image just stored: the note will point at it once
  // the writer saves, the same as anything else typed on the page.
  function adoptSocialImage(state, result) {
    const path = String((result && result.relativePath) || '').trim();
    if (!path) return state;
    return { ...state, values: { ...copyValues(state.values), social_image: path } };
  }

  // The room lives at /airdate; the editor is the room with ?essay=.
  const ROOM_PATH = '/airdate';

  function editorUrl(id) {
    return `${ROOM_PATH}?essay=${encodeURIComponent(String(id || ''))}`;
  }

  // The room as it is, with the editor closed: the view (the hash) stays.
  function roomUrl(hash) {
    return `${ROOM_PATH}${hash || ''}`;
  }

  // The essay a clicked link opens in place, or '' when the browser should
  // follow it. Only a link to the room itself, on this origin, with ?essay=,
  // is the editor's; anything else (another page, another site) is a real
  // navigation. Getting the path wrong here turns every card click into a
  // full page load.
  function essayFromLink(href, here) {
    let url;
    let base;
    try {
      base = new URL(String(here || ''));
      url = new URL(String(href || ''), base);
    } catch (error) {
      return '';
    }
    if (url.origin !== base.origin || url.pathname !== ROOM_PATH) return '';
    return essayFromSearch(url.search);
  }

  return {
    blockingCount,
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
    gearState,
    presetSettings,
    flipSection,
    stampTourText,
    wordCount,
    wordLine,
    clampScriptHeight,
    addTags,
    removeTag,
    noteFor,
    formatDay,
    airDate,
    statusLine,
    clockTime,
    savedLine,
    essayFromSearch,
    sendFieldPlaceholders,
    dayValue,
    imageName,
    adoptSocialImage,
    ROOM_PATH,
    editorUrl,
    roomUrl,
    essayFromLink,
    FIELD_TARGETS,
    readinessList,
    readinessHeading,
    lineRange,
    connectionGate,
    boardGate,
    readinessGate,
    sendGates,
    connectionLine,
    shouldRetrySend,
    failureCode,
    sendFailure,
    substackCheckUrl,
    sendSuccess,
    adoptSend,
    adoptForget,
    publishValues,
  };
});
