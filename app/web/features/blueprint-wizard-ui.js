'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};

  function errorText(root, errors) {
    root.querySelectorAll('.blueprint-wizard-field-error').forEach(value => value.remove());
    const messages = Object.values(errors || {});
    const summary = root.querySelector('[data-wizard-error-summary]');
    if (summary) {
      summary.hidden = !messages.length;
      summary.replaceChildren(...messages.map(message => node('div', { text: message })));
    }
    for (const [name, message] of Object.entries(errors || {})) {
      const control = root.querySelector('[name="' + CSS.escape(name) + '"]');
      if (!control) continue;
      const wrapper = control.closest('label') || control.parentElement;
      wrapper?.append(node('span', { class: 'form-error blueprint-wizard-field-error', text: message }));
      control.setAttribute('aria-invalid', 'true');
    }
  }

  function workflowVisual(steps) {
    const visual = node('div', { class: 'blueprint-wizard-workflow-visual' });
    steps.forEach((step, index) => {
      visual.append(node('div', { class: 'blueprint-wizard-workflow-card' },
        node('span', { class: 'workflow-dag-index', text: String(index + 1) }),
        node('strong', { text: parts.core.workflowLabel(step.type) }),
        node('small', { text: step.depends_on?.length ? 'po: ' + step.depends_on.join(', ') : 'start' })));
      if (index < steps.length - 1) visual.append(node('span', { class: 'blueprint-wizard-workflow-arrow', text: '↓' }));
    });
    return visual;
  }

  function summaryRow(label, value) {
    return node('div', { class: 'blueprint-wizard-review-row' },
      node('span', { text: label }),
      node('strong', { text: String(value ?? '—') }));
  }

  function avatarPicker(state, avatars = []) {
    const avatarField = selectField('Avatar Blueprintu', 'avatar_id', [
      { value: '', label: 'Domyślny — ikona Blueprintu' },
      ...avatars.map(item => ({ value: item.id, label: item.name + ' · ' + item.id })),
    ], state.avatarId, {
      wide: true,
      help: 'Awatary są zarządzane w Narzędzia → Awatary Blueprintów.',
    });
    const avatarVisual = node('div', { class: 'blueprint-wizard-avatar-visual', 'aria-hidden': 'true' });
    const avatarCopy = node('div', { class: 'blueprint-wizard-avatar-copy' });
    const avatarPreview = node('div', { class: 'blueprint-wizard-avatar-picker wide' }, avatarVisual, avatarCopy);
    const avatarSelect = avatarField.querySelector('select');

    const renderAvatar = () => {
      const selectedAvatar = avatars.find(item => String(item.id) === String(avatarSelect.value || ''));
      avatarVisual.replaceChildren(selectedAvatar?.data_uri
        ? node('img', { src: selectedAvatar.data_uri, alt: '', loading: 'lazy', decoding: 'async' })
        : appIcon('box'));
      avatarCopy.replaceChildren(
        node('strong', { text: selectedAvatar?.name || 'Domyślny avatar' }),
        node('small', { class: 'muted', text: selectedAvatar
          ? 'ID: ' + selectedAvatar.id
          : 'Jeśli nie wybierzesz awatara, produkt użyje standardowej ikony Blueprintu.' }));
    };
    avatarSelect.addEventListener('change', () => {
      state.avatarId = avatarSelect.value || '';
      renderAvatar();
    });
    renderAvatar();
    return [avatarField, avatarPreview];
  }

  function dualListGroup(title, name, rows, selected, description = '') {
    const picked = new Set((selected || []).map(value => String(value)));
    const labelFor = row => {
      const primary = row.name || row.username || ('#' + row.id);
      return row.email ? primary + ' — ' + row.email : primary;
    };
    const sorted = rows.slice().sort((a, b) => labelFor(a).localeCompare(labelFor(b), 'pl'));
    const availableSelect = node('select', {
      class: 'blueprint-wizard-dual-select',
      multiple: true,
      size: 8,
      'aria-label': title + ' — dostępne',
    });
    const selectedSelect = node('select', {
      class: 'blueprint-wizard-dual-select',
      multiple: true,
      size: 8,
      'aria-label': title + ' — wybrane',
      'data-dual-list-name': name,
    });

    const optionFor = row => node('option', {
      value: String(row.id),
      text: labelFor(row),
      title: labelFor(row),
    });
    const refill = () => {
      const current = new Set([...selectedSelect.options].map(option => option.value));
      availableSelect.replaceChildren(...sorted.filter(row => !current.has(String(row.id))).map(optionFor));
      selectedSelect.replaceChildren(...sorted.filter(row => current.has(String(row.id))).map(optionFor));
    };
    sorted.forEach(row => (picked.has(String(row.id)) ? selectedSelect : availableSelect).append(optionFor(row)));

    const move = (source, target, all = false) => {
      const moving = [...source.options].filter(option => all || option.selected).map(option => option.value);
      if (!moving.length) return;
      const targetValues = new Set([...target.options].map(option => option.value));
      moving.forEach(value => targetValues.add(value));
      const selectedValues = target === selectedSelect
        ? targetValues
        : new Set([...selectedSelect.options].map(option => option.value).filter(value => !moving.includes(value)));
      selectedSelect.replaceChildren(...sorted.filter(row => selectedValues.has(String(row.id))).map(optionFor));
      refill();
      selectedSelect.dispatchEvent(new Event('change', { bubbles: true }));
    };

    availableSelect.addEventListener('dblclick', () => move(availableSelect, selectedSelect));
    selectedSelect.addEventListener('dblclick', () => move(selectedSelect, availableSelect));

    const controls = node('div', { class: 'blueprint-wizard-dual-controls', 'aria-label': 'Przenoszenie pozycji' },
      node('button', { type: 'button', class: 'button ghost', title: 'Dodaj zaznaczone', 'aria-label': 'Dodaj zaznaczone', onClick: () => move(availableSelect, selectedSelect) }, '›'),
      node('button', { type: 'button', class: 'button ghost', title: 'Dodaj wszystkie', 'aria-label': 'Dodaj wszystkie', onClick: () => move(availableSelect, selectedSelect, true) }, '»'),
      node('button', { type: 'button', class: 'button ghost', title: 'Usuń zaznaczone', 'aria-label': 'Usuń zaznaczone', onClick: () => move(selectedSelect, availableSelect) }, '‹'),
      node('button', { type: 'button', class: 'button ghost', title: 'Usuń wszystkie', 'aria-label': 'Usuń wszystkie', onClick: () => move(selectedSelect, availableSelect, true) }, '«'));

    const body = node('div', { class: 'blueprint-wizard-dual-list' },
      node('div', { class: 'blueprint-wizard-dual-column' },
        node('span', { class: 'blueprint-wizard-dual-title', text: 'Dostępne' }),
        availableSelect),
      controls,
      node('div', { class: 'blueprint-wizard-dual-column' },
        node('span', { class: 'blueprint-wizard-dual-title', text: 'Wybrane' }),
        selectedSelect));

    if (!rows.length) body.replaceChildren(node('span', { class: 'muted', text: 'Brak dostępnych pozycji.' }));
    return node('fieldset', { class: 'blueprint-wizard-dual-fieldset' },
      node('legend', { text: title }),
      description ? node('p', { class: 'muted', text: description }) : null,
      body);
  }


  function credentialPicker(title, name, rows, selectedValue, onChange, options = {}) {
    const selected = String(selectedValue || '');
    const input = node('input', { type: 'hidden', name, value: selected });
    const grid = node('div', { class: 'blueprint-wizard-access-card-grid' });

    const update = value => {
      input.value = String(value || '');
      wrapper.querySelectorAll('.blueprint-wizard-access-card').forEach(card => {
        const active = card.dataset.value === input.value;
        card.classList.toggle('selected', active);
        card.setAttribute('aria-pressed', String(active));
      });
      input.dispatchEvent(new Event('change', { bubbles: true }));
      if (typeof onChange === 'function') onChange(input.value);
    };

    const card = (value, heading, detail, meta = '', icon = 'key') => {
      const active = String(value || '') === selected;
      return node('button', {
        type: 'button',
        class: 'blueprint-wizard-access-card' + (active ? ' selected' : ''),
        'data-value': String(value || ''),
        'aria-pressed': String(active),
        onClick: () => update(value),
      },
      node('span', { class: 'blueprint-wizard-access-card-icon', 'aria-hidden': 'true' }, appIcon(icon)),
      node('span', { class: 'blueprint-wizard-access-card-copy' },
        node('strong', { text: heading }),
        detail ? node('small', { text: detail }) : null,
        meta ? node('code', { text: meta }) : null),
      active ? node('span', { class: 'blueprint-wizard-card-check' }, appIcon('check')) : null);
    };

    if (options.allowNone !== false) {
      grid.append(card('', options.noneLabel || 'Bez zapisanego dostępu',
        options.noneDescription || 'Użyj ustawień ręcznych albo nie konfiguruj tego dostępu.', '', 'x'));
    }

    rows.forEach(row => {
      const type = credentialTypeLabel(row.type || 'ssh');
      const detail = [row.username, row.endpoint].filter(Boolean).join(' · ');
      grid.append(card(
        row.id,
        row.name || ('Dostęp #' + row.id),
        detail || type,
        type,
        row.type === 'ssh' ? 'key' : 'shield'
      ));
    });

    const wrapper = node('fieldset', { class: 'blueprint-wizard-access-picker' },
      node('legend', { text: title }),
      options.description ? node('p', { class: 'muted', text: options.description }) : null,
      input,
      rows.length || options.allowNone !== false
        ? grid
        : node('div', { class: 'blueprint-wizard-empty' },
            node('strong', { text: options.emptyTitle || 'Brak dostępnych pozycji' }),
            node('p', { class: 'muted', text: options.emptyText || 'Dodaj odpowiedni Dostęp i wróć do kreatora.' })));
    return wrapper;
  }

  function multiCardGroup(title, name, rows, selectedValues = [], description = '') {
    const selected = new Set((selectedValues || []).map(value => String(value)));
    const labelFor = row => row.name || row.username || ('#' + row.id);
    const wrapper = node('fieldset', {
      class: 'blueprint-wizard-access-multi',
      'data-simple-access-name': name,
    },
    node('legend', { text: title }),
    description ? node('p', { class: 'muted', text: description }) : null);

    if (!rows.length) {
      wrapper.append(node('div', { class: 'blueprint-wizard-empty' },
        node('strong', { text: 'Brak dostępnych pozycji' }),
        node('p', { class: 'muted', text: 'Nie ma elementów, które można przypisać w tym zakresie.' })));
      return wrapper;
    }

    const grid = node('div', { class: 'blueprint-wizard-access-multi-grid' });
    rows.slice().sort((a, b) => labelFor(a).localeCompare(labelFor(b), 'pl')).forEach(row => {
      const checked = selected.has(String(row.id));
      const checkbox = node('input', { type: 'checkbox', name, value: String(row.id), checked });
      const item = node('label', { class: 'blueprint-wizard-access-multi-card' + (checked ? ' selected' : '') },
        checkbox,
        node('span', { class: 'blueprint-wizard-access-multi-copy' },
          node('strong', { text: labelFor(row) }),
          row.email ? node('small', { text: row.email }) : null,
          row.permissions ? node('small', { text: row.permissions.length + ' uprawnień' }) : null));
      checkbox.addEventListener('change', () => item.classList.toggle('selected', checkbox.checked));
      grid.append(item);
    });
    wrapper.append(grid);
    return wrapper;
  }

  function toggleCard(title, name, checked, description = '') {
    const checkbox = node('input', { type: 'checkbox', name, checked });
    const wrapper = node('label', { class: 'blueprint-wizard-access-toggle' + (checked ? ' selected' : '') },
      checkbox,
      node('span', { class: 'blueprint-wizard-access-toggle-copy' },
        node('strong', { text: title }),
        description ? node('small', { text: description }) : null));
    checkbox.addEventListener('change', () => wrapper.classList.toggle('selected', checkbox.checked));
    return wrapper;
  }

  parts.ui = { errorText, summaryRow, workflowVisual, avatarPicker, dualListGroup, credentialPicker, multiCardGroup, toggleCard };
  registerExtension('blueprint-wizard-ui', () => {});
})();
