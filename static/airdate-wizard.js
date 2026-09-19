// First-run wizard. One step at a time; each step is checked against
// /api/settings/check, which saves nothing. config.json is written once, at
// finish. The only earlier write is the optional "create the essays folder".
(function installAirdateWizard(root) {
  const el = (id) => document.getElementById(id);

  function open({ status, getJson, postJson, escapeHtml, onFinish }) {
    const wizard = el('wizard');
    const form = el('wizard-form');
    if (!wizard || !form) return;
    const steps = [...form.querySelectorAll('.wizard-step')];
    const setup = status.setup || {};
    const pathsEditable = Boolean(setup.paths_editable);
    let index = 0;
    let busy = false;

    function setMessage(message, kind = '') {
      const node = el('wizard-messages');
      node.textContent = message || '';
      node.dataset.kind = kind;
    }

    function payload() {
      const f = form.elements;
      const atRoot = f.essays_at_root.checked;
      const out = {
        essays_folder: atRoot ? '.' : f.essays_folder.value.trim(),
        vault_name: f.vault_name.value.trim(),
        publication: f.publication.value.trim(),
        publication_name: f.publication_name.value.trim(),
        category_mode: f.category_mode.value,
        publish_day: f.publish_day.value,
        totems_enabled: f.totems_enabled.checked,
        tag_presets: root.AirdatePresets.read(el('wizard-presets')),
        totems: [...form.querySelectorAll('.setup-totem-row')].map((row) => ({
          key: row.dataset.totemKey,
          label: row.querySelector('[name="totem_label"]').value.trim(),
          color: row.querySelector('[name="totem_color"]').value,
          image: row.querySelector('[name="totem_image"]').value.trim(),
        })),
      };
      if (pathsEditable) out.vault_path = f.vault_path.value.trim();
      return out;
    }

    function renderIndicator() {
      el('wizard-steps').innerHTML = steps.map((step, i) => {
        const stateName = i === index ? 'current' : i < index ? 'done' : 'todo';
        return `<li data-state="${stateName}"${i === index ? ' aria-current="step"' : ''}>${escapeHtml(step.dataset.label)}</li>`;
      }).join('');
    }

    function renderSummary() {
      const p = payload();
      const rows = [
        ['essays', p.essays_folder === '.' ? 'at the vault root' : `the ${p.essays_folder} folder`],
        ['publication', p.publication || 'not set yet'],
        ['organize', p.category_mode === 'folders' ? 'topic folders' : 'no folders'],
        ['totems', p.totems_enabled ? p.totems.map((t) => t.label).join(', ') : 'off'],
        ['tags', p.tag_presets.length ? p.tag_presets.map((t) => t.name).join(', ') : 'no presets yet'],
        ['calendar', p.publish_day ? `${p.publish_day}s` : 'off'],
      ];
      el('wizard-summary').innerHTML = rows
        .map(([name, value]) => `<div><dt>${escapeHtml(name)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('');
    }

    async function renderConnect() {
      const list = el('wizard-checks');
      list.innerHTML = '<li>checking...</li>';
      const names = {
        connector_paired: 'install the AirDate connector and run "Pair with AirDate"',
        substack_command: 'keep Obsidian open with the connector enabled',
        substack_session: 'run "Connect Substack" in Obsidian',
      };
      try {
        const [substack, check] = await Promise.all([
          getJson('/api/substack/status'),
          postJson('/api/settings/check', payload()),
        ]);
        list.innerHTML = (substack.setup_checks || [])
          .filter((item) => names[item.key])
          .map((item) => `<li data-ok="${item.ok ? 'yes' : 'no'}"><strong>${escapeHtml(names[item.key])}</strong><span>${item.ok ? 'done' : 'not yet'}</span></li>`)
          .join('');
        const allDone = (substack.setup_checks || []).filter((item) => names[item.key]).every((item) => item.ok);
        if (steps[index].dataset.step === 'connect') el('wizard-next').textContent = allDone ? 'next' : "I'll do this later";
        el('wizard-connector-folder').innerHTML = check.connector_folder
          ? `From the AirDate folder, <code>python3 scripts/install-connector</code> copies the connector to:<br><code>${escapeHtml(check.connector_folder)}</code>`
          : 'From the AirDate folder on the machine running it, <code>python3 scripts/install-connector</code> copies the connector into your vault.';
      } catch (err) {
        list.innerHTML = '';
        setMessage(`could not check the connection: ${err.message}`, 'bad');
      }
    }

    function show(next) {
      index = Math.max(0, Math.min(steps.length - 1, next));
      steps.forEach((step, i) => step.classList.toggle('hidden', i !== index));
      el('wizard-back').classList.toggle('hidden', index === 0);
      const name = steps[index].dataset.step;
      el('wizard-next').textContent = name === 'finish' ? 'finish' : 'next';
      setMessage('');
      renderIndicator();
      if (name === 'connect') renderConnect();
      if (name === 'finish') renderSummary();
      (steps[index].querySelector('input:not([type="checkbox"]):not([type="radio"]), select') || el('wizard-next')).focus();
    }

    // "tag_presets[0].name is required." reads better as "preset 1: name is required."
    const presetMessage = (e) => e.replace(/^tag_presets\[(\d+)\]\./, (_, i) => `preset ${Number(i) + 1}: `);

    // Returns true when the current step may be left.
    async function checkStep() {
      const name = steps[index].dataset.step;
      if (name === 'welcome' || name === 'connect') return true;
      const check = await postJson('/api/settings/check', payload());
      if (name === 'vault') {
        el('wizard-create-essays').classList.toggle('hidden', !(check.vault_ok && !check.essays_ok));
        const problems = [check.vault_message, check.essays_message,
          ...(check.errors || []).filter((e) => e.startsWith('vault.'))].filter(Boolean);
        if (problems.length) { setMessage(problems.join('\n'), 'bad'); return false; }
        if (!form.elements.vault_name.value.trim()) form.elements.vault_name.value = check.vault_name || '';
        return true;
      }
      const errors = name === 'tags' ? (check.errors || []).map(presetMessage) : check.errors;
      if (errors?.length) { setMessage(errors.join('\n'), 'bad'); return false; }
      return true;
    }

    async function finish() {
      const result = await postJson('/api/settings', payload());
      if (result.errors?.length) { setMessage(result.errors.join('\n'), 'bad'); return; }
      if (result.setup?.required) {
        setMessage([result.setup.vault_message, result.setup.essays_message].filter(Boolean).join('\n') || 'Setup is not complete yet.', 'bad');
        return;
      }
      wizard.classList.add('hidden');
      document.body.classList.remove('wizard-open');
      await onFinish();
    }

    async function guarded(action) {
      if (busy) return;
      busy = true;
      try { await action(); } catch (err) { setMessage(err.payload?.errors?.join('\n') || err.message, 'bad'); } finally { busy = false; }
    }

    form.addEventListener('submit', (event) => {
      event.preventDefault();
      guarded(async () => {
        if (steps[index].dataset.step === 'finish') { await finish(); return; }
        if (await checkStep()) show(index + 1);
      });
    });
    // A message describes the form as it was checked; editing makes it stale.
    form.addEventListener('input', () => setMessage(''));
    el('wizard-back').addEventListener('click', () => show(index - 1));
    el('wizard-recheck').addEventListener('click', () => guarded(renderConnect));
    el('wizard-create-essays').addEventListener('click', () => guarded(async () => {
      await postJson('/api/settings/create-essays-folder', payload());
      if (await checkStep()) show(index + 1);
    }));
    form.elements.essays_at_root.addEventListener('change', (event) => {
      form.elements.essays_folder.disabled = event.target.checked;
    });
    form.elements.totems_enabled.addEventListener('change', (event) => {
      el('wizard-totem-list').classList.toggle('is-off', !event.target.checked);
    });

    // Prefill from the neutral defaults; Monday is the wizard's own default.
    const values = setup.form || {};
    form.elements.vault_path.value = values.vault_path || '';
    form.elements.essays_folder.value = values.essays_folder || 'Essays';
    form.elements.publication.value = values.publication || '';
    form.elements.publication_name.value = values.publication_name || '';
    form.elements.publish_day.value = values.publish_day || 'monday';
    el('wizard-vault-path-field').classList.toggle('hidden', !pathsEditable);
    el('wizard-vault-path-note').classList.toggle('hidden', pathsEditable);
    el('wizard-totem-list').innerHTML = (status.config?.totems?.slots || []).map((slot) => `<div class="setup-totem-row" data-totem-key="${escapeHtml(slot.key)}">
      <img src="${escapeHtml(slot.image)}" alt="">
      <input name="totem_label" value="${escapeHtml(slot.label)}" aria-label="Totem name" autocomplete="off">
      <input name="totem_color" type="color" value="${escapeHtml(slot.color || '#8092b0')}" aria-label="Totem color">
      <input class="setup-totem-image" name="totem_image" value="${escapeHtml(slot.image_path || '')}" placeholder="icon: path in your vault, or blank for the placeholder" aria-label="Totem icon path" autocomplete="off" spellcheck="false">
    </div>`).join('');

    root.AirdatePresets.mount(el('wizard-presets'), status.config?.tag_presets || []);

    wizard.classList.remove('hidden');
    document.body.classList.add('wizard-open');
    show(0);
  }

  root.AirdateWizard = { open };
})(typeof globalThis === 'object' ? globalThis : this);
