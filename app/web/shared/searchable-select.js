'use strict';

(() => {
  let searchableSelectSequence = 0;
  
  function normalizeSearchText(value) {
    return String(value || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLocaleLowerCase('pl-PL')
      .trim();
  }
  
  function searchableSelectField(labelText, name, choices, value, options = {}) {
    const controlId = 'searchable-select-' + (++searchableSelectSequence);
    const listboxId = controlId + '-listbox';
    const valueInput = node('input', { type: 'hidden', name, value: '' });
    const searchInput = node('input', {
      id: controlId,
      type: 'search',
      class: 'searchable-select-input',
      autocomplete: 'off',
      spellcheck: 'false',
      required: options.required,
      placeholder: options.placeholder || 'Wpisz, aby filtrować…',
      role: 'combobox',
      'aria-autocomplete': 'list',
      'aria-expanded': 'false',
      'aria-controls': listboxId,
    });
    const listbox = node('div', {
      id: listboxId,
      class: 'searchable-select-options',
      role: 'listbox',
      hidden: true,
    });
    const control = node('div', { class: 'searchable-select' },
      searchInput,
      node('span', { class: 'searchable-select-chevron', 'aria-hidden': 'true', text: '⌄' }),
      valueInput,
      listbox);
    const wrapper = node('div', { class: 'searchable-select-field' + (options.wide ? ' wide' : '') },
      node('label', { for: controlId }, formFieldLabel(labelText, Boolean(options.required))),
      control);
    if (options.help) wrapper.append(node('span', { class: 'field-help', text: options.help }));
  
    let rows = [];
    let selectedValue = '';
    let activeIndex = -1;
    let editing = false;
    const listeners = new Set();
  
    function selectedChoice() {
      return rows.find(choice => String(choice.value) === String(selectedValue)) || null;
    }
  
    function updateValidity() {
    if (!options.required) return;
    searchInput.setCustomValidity(valueInput.value ? '' : 'Wybierz wartość z listy.');
  }

  function closeList() {
      listbox.hidden = true;
      searchInput.setAttribute('aria-expanded', 'false');
      searchInput.removeAttribute('aria-activedescendant');
      activeIndex = -1;
    }
  
    function visibleChoices() {
      const query = editing ? normalizeSearchText(searchInput.value) : '';
      if (!query) return rows;
      return rows.filter(choice => normalizeSearchText(choice.label).includes(query));
    }
  
    function optionButtons() {
      return Array.from(listbox.querySelectorAll('.searchable-select-option'));
    }
  
    function activate(index) {
      const buttons = optionButtons();
      if (!buttons.length) {
        activeIndex = -1;
        searchInput.removeAttribute('aria-activedescendant');
        return;
      }
      activeIndex = Math.max(0, Math.min(index, buttons.length - 1));
      buttons.forEach((element, itemIndex) => element.classList.toggle('active', itemIndex === activeIndex));
      const active = buttons[activeIndex];
      searchInput.setAttribute('aria-activedescendant', active.id);
      active.scrollIntoView({ block: 'nearest' });
    }
  
    function renderOptions() {
      const matches = visibleChoices();
      const selected = String(valueInput.value || '');
      if (!matches.length) {
        listbox.replaceChildren(node('div', {
          class: 'searchable-select-empty',
          role: 'presentation',
          text: 'Brak pasujących wyników',
        }));
        activeIndex = -1;
        return;
      }
      listbox.replaceChildren(...matches.map((choice, index) => {
        const option = node('button', {
          type: 'button',
          id: listboxId + '-option-' + index,
          class: 'searchable-select-option',
          role: 'option',
          'aria-selected': String(choice.value) === selected ? 'true' : 'false',
          onMouseDown: event => event.preventDefault(),
          onClick: () => commit(choice, true),
        }, node('span', { text: choice.label }));
        option._searchableChoice = choice;
        return option;
      }));
      activeIndex = -1;
    }
  
    function openList() {
      if (searchInput.disabled) return;
      renderOptions();
      listbox.hidden = false;
      searchInput.setAttribute('aria-expanded', 'true');
    }
  
    function commit(choice, notify = false) {
      if (!choice) return;
      selectedValue = String(choice.value);
      valueInput.value = selectedValue;
      searchInput.value = String(choice.label || '');
      editing = false;
      updateValidity();
      closeList();
      if (notify) listeners.forEach(listener => listener(selectedValue, choice));
    }
  
    function clearSelection() {
      valueInput.value = '';
      editing = true;
    }
  
    function setChoices(nextChoices, preferred = '') {
      rows = (nextChoices || []).map(choice => ({
        value: String(choice.value),
        label: String(choice.label ?? choice.value),
      }));
      const preferredChoice = rows.find(choice => String(choice.value) === String(preferred));
      const retainedChoice = rows.find(choice => String(choice.value) === String(selectedValue));
      const next = preferredChoice || retainedChoice || (options.selectFirst === false ? null : rows[0]) || null;
      searchInput.disabled = rows.length === 0;
      if (next) commit(next, false);
      else {
        selectedValue = '';
        valueInput.value = '';
        searchInput.value = '';
        editing = false;
        closeList();
      }
    }
  
    searchInput.addEventListener('focus', () => {
      searchInput.select();
      editing = false;
      openList();
    });
    searchInput.addEventListener('click', () => {
      if (listbox.hidden) openList();
    });
    searchInput.addEventListener('input', () => {
      clearSelection();
      openList();
    });
    searchInput.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (listbox.hidden) openList();
        const buttons = optionButtons();
        if (!buttons.length) return;
        const delta = event.key === 'ArrowDown' ? 1 : -1;
        const start = activeIndex < 0 ? (delta > 0 ? 0 : buttons.length - 1) : activeIndex + delta;
        activate(Math.max(0, Math.min(start, buttons.length - 1)));
        return;
      }
      if (event.key === 'Enter' && !listbox.hidden) {
        const buttons = optionButtons();
        const target = activeIndex >= 0 ? buttons[activeIndex] : (buttons.length === 1 ? buttons[0] : null);
        if (target) {
          event.preventDefault();
          commit(target._searchableChoice, true);
        }
        return;
      }
      if (event.key === 'Escape') {
        const previous = selectedChoice();
        if (previous) commit(previous, false);
        else closeList();
        event.stopPropagation();
      }
      if (event.key === 'Tab') closeList();
    });
    searchInput.addEventListener('blur', () => {
      window.setTimeout(closeList, 0);
    });
  
    wrapper.searchableSelect = Object.freeze({
      setChoices,
      value: () => String(valueInput.value || ''),
      onChange: listener => listeners.add(listener),
      focus: () => searchInput.focus(),
    });
    setChoices(choices, value);
    return wrapper;
  }

  window.searchableSelectField = searchableSelectField;
})();
