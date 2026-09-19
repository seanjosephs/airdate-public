// Tag preset editor, shared by settings and the first-run wizard. A preset is
// a name, a color and up to five tags. Nothing ships prefilled.
(function installAirdatePresets(root) {
  const MAX_TAGS = 5;
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function rowHtml(preset = {}) {
    return `<div class="preset-row">
      <input name="preset_name" value="${esc(preset.name)}" placeholder="preset name" aria-label="Preset name" autocomplete="off">
      <input name="preset_color" type="color" value="${esc(preset.color || '#8092b0')}" aria-label="Preset color">
      <input name="preset_tags" value="${esc((preset.tags || []).join(', '))}" placeholder="up to five tags, comma-separated" aria-label="Preset tags" autocomplete="off" spellcheck="false">
      <button class="settings-button preset-remove" type="button" aria-label="Remove this preset">remove</button>
    </div>`;
  }

  function mount(container, presets) {
    if (!container) return;
    container.innerHTML = `<div class="preset-rows">${(presets || []).map(rowHtml).join('')}</div>
      <p class="setup-note preset-empty">No presets yet. A preset fills an essay's tags in one click and colors its card.</p>
      <button class="settings-button preset-add" type="button">add a preset</button>`;
    const rows = container.querySelector('.preset-rows');
    const sync = () => container.querySelector('.preset-empty').classList.toggle('hidden', rows.children.length > 0);
    if (!container.dataset.bound) {
      container.dataset.bound = '1';
      container.addEventListener('click', (event) => {
        const list = container.querySelector('.preset-rows');
        if (event.target.closest('.preset-add')) {
          list.insertAdjacentHTML('beforeend', rowHtml());
          list.lastElementChild.querySelector('[name="preset_name"]').focus();
        } else if (event.target.closest('.preset-remove')) {
          event.target.closest('.preset-row').remove();
        } else return;
        container.querySelector('.preset-empty').classList.toggle('hidden', list.children.length > 0);
        container.dispatchEvent(new Event('input', { bubbles: true }));
      });
    }
    sync();
  }

  // Rows left entirely blank are dropped; the server validates the rest.
  function read(container) {
    return [...(container?.querySelectorAll('.preset-row') || [])].map((row) => ({
      name: row.querySelector('[name="preset_name"]').value.trim(),
      color: row.querySelector('[name="preset_color"]').value,
      tags: row.querySelector('[name="preset_tags"]').value.split(',').map((t) => t.trim()).filter(Boolean),
    })).filter((preset) => preset.name || preset.tags.length);
  }

  root.AirdatePresets = { mount, read, MAX_TAGS };
})(typeof globalThis === 'object' ? globalThis : this);
