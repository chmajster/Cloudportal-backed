'use strict';

function searchable(value) {
  return String(value ?? '')
    .toLocaleLowerCase('pl-PL')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '');
}
function tablePreferenceKey(columns) {
  const signature = [state.view, ...columns.map(column => column.label)].join('|');
  let hash = 2166136261;
  for (let index = 0; index < signature.length; index += 1) {
    hash ^= signature.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `cloudportal.console.table.${(hash >>> 0).toString(16)}`;
}
function readTablePreferences(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '{}');
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
}
function writeTablePreferences(key, preferences) {
  try { localStorage.setItem(key, JSON.stringify(preferences)); } catch { /* Storage may be unavailable. */ }
}
function table(columns, rows, actions) {
  if (!rows.length) return node('div', { class: 'table-wrap' }, node('div', { class: 'empty', text: 'Brak danych do wyświetlenia.' }));

  const preferenceKey = tablePreferenceKey(columns);
  const saved = readTablePreferences(preferenceKey);
  const hiddenColumns = new Set(
    Array.isArray(saved.hidden_columns)
      ? saved.hidden_columns.filter(index => Number.isInteger(index) && index >= 0 && index < columns.length)
      : []
  );
  let sortIndex = Number.isInteger(saved.sort_index) && saved.sort_index >= 0 && saved.sort_index < columns.length
    ? saved.sort_index : null;
  let sortDirection = saved.sort_direction === 'desc' ? 'desc' : 'asc';
  let pageSize = [10, 25, 50, 100, 0].includes(Number(saved.page_size)) ? Number(saved.page_size) : 25;
  let density = saved.density === 'compact' ? 'compact' : 'normal';
  let currentPage = 1;

  const head = node('tr');
  const headerCells = [];
  columns.forEach((column, columnIndex) => {
    const sortButton = node('button', {
      class: 'table-sort-button',
      type: 'button',
      disabled: column.sortable === false,
      onClick: () => {
        if (column.sortable === false) return;
        if (sortIndex === columnIndex) sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
        else {
          sortIndex = columnIndex;
          sortDirection = 'asc';
        }
        currentPage = 1;
        persist();
        apply();
      },
    },
    node('span', { text: column.label }),
    node('span', { class: 'table-sort-indicator', 'aria-hidden': 'true', text: '↕' }));
    const th = node('th', { 'data-column-index': columnIndex }, sortButton);
    th.hidden = hiddenColumns.has(columnIndex);
    headerCells.push(th);
    head.append(th);
  });
  if (actions) head.append(node('th', { class: 'table-actions-column', text: 'Akcje' }));

  const body = node('tbody');
  const records = rows.map((row, rowIndex) => {
    const tr = node('tr');
    const sortValues = [];
    const searchableValues = [];
    columns.forEach((column, columnIndex) => {
      const value = column.value(row);
      const cell = node('td', { class: column.class || '', 'data-column-index': columnIndex }, value instanceof Node ? value : String(value ?? '—'));
      cell.hidden = hiddenColumns.has(columnIndex);
      const textValue = cell.textContent?.trim() || String(value ?? '');
      sortValues.push(column.sortValue ? String(column.sortValue(row) ?? '') : textValue);
      searchableValues.push(textValue);
      tr.append(cell);
    });
    if (actions) tr.append(node('td', { class: 'table-actions-column' }, node('div', { class: 'row-actions' }, actions(row))));
    const raw = (() => { try { return JSON.stringify(row); } catch { return ''; } })();
    return {
      originalIndex: rowIndex,
      element: tr,
      search: searchable([raw, ...searchableValues].join(' ')),
      sortValues,
    };
  });
  records.forEach(record => body.append(record.element));

  const noResults = node('tr', { class: 'table-search-empty', hidden: true },
    node('td', { colspan: columns.length + (actions ? 1 : 0), class: 'empty', text: 'Brak wyników.' }));
  body.append(noResults);

  const tableElement = node('table', {}, node('thead', {}, head), body);
  const scroll = node('div', { class: 'table-scroll' }, tableElement);
  const wrapper = node('div', { class: `table-wrap advanced-table ${density === 'compact' ? 'compact' : ''}` });

  const search = node('input', {
    class: 'table-search',
    type: 'search',
    placeholder: 'Szukaj w tabeli…',
    'aria-label': 'Szukaj w tabeli',
  });
  const count = node('span', { class: 'table-count', 'aria-live': 'polite' });
  const pageInfo = node('span', { class: 'table-page-info', 'aria-live': 'polite' });
  const previousPage = button('←', () => { currentPage -= 1; apply(); }, 'ghost');
  const nextPage = button('→', () => { currentPage += 1; apply(); }, 'ghost');
  previousPage.setAttribute('aria-label', 'Poprzednia strona');
  nextPage.setAttribute('aria-label', 'Następna strona');

  const pageSizeSelect = node('select', { class: 'table-page-size', 'aria-label': 'Liczba wierszy na stronę' },
    ...[
      [10, '10 / strona'], [25, '25 / strona'], [50, '50 / strona'], [100, '100 / strona'], [0, 'Wszystkie'],
    ].map(([value, label]) => node('option', { value, text: label, selected: Number(value) === pageSize })));
  pageSizeSelect.addEventListener('change', () => {
    pageSize = Number(pageSizeSelect.value);
    currentPage = 1;
    persist();
    apply();
  });

  const densitySelect = node('select', { class: 'table-density', 'aria-label': 'Gęstość tabeli' },
    node('option', { value: 'normal', text: 'Normalna', selected: density === 'normal' }),
    node('option', { value: 'compact', text: 'Kompaktowa', selected: density === 'compact' }));
  densitySelect.addEventListener('change', () => {
    density = densitySelect.value === 'compact' ? 'compact' : 'normal';
    wrapper.classList.toggle('compact', density === 'compact');
    persist();
  });

  const columnPickerBody = node('div', { class: 'table-column-picker-body' });
  columns.forEach((column, columnIndex) => {
    const checkbox = node('input', { type: 'checkbox', checked: !hiddenColumns.has(columnIndex) });
    checkbox.addEventListener('change', () => {
      if (!checkbox.checked && columns.length - hiddenColumns.size <= 1) {
        checkbox.checked = true;
        toast('Co najmniej jedna kolumna musi pozostać widoczna.', 'error');
        return;
      }
      if (checkbox.checked) hiddenColumns.delete(columnIndex);
      else hiddenColumns.add(columnIndex);
      headerCells[columnIndex].hidden = hiddenColumns.has(columnIndex);
      records.forEach(record => {
        const cell = record.element.querySelector(`[data-column-index="${columnIndex}"]`);
        if (cell) cell.hidden = hiddenColumns.has(columnIndex);
      });
      persist();
    });
    columnPickerBody.append(node('label', { class: 'table-column-option' }, checkbox, node('span', { text: column.label })));
  });
  const columnPicker = node('details', { class: 'table-column-picker' },
    node('summary', { text: 'Kolumny' }),
    columnPickerBody);

  const pagination = node('div', { class: 'table-pagination' }, previousPage, pageInfo, nextPage);
  const toolbar = node('div', { class: 'table-toolbar' },
    node('div', { class: 'table-toolbar-main' }, search, count),
    node('div', { class: 'table-toolbar-controls' },
      densitySelect, pageSizeSelect, columnPicker));
  const footer = node('div', { class: 'table-footer' }, count.cloneNode(true), pagination);

  function persist() {
    writeTablePreferences(preferenceKey, {
      hidden_columns: [...hiddenColumns],
      sort_index: sortIndex,
      sort_direction: sortDirection,
      page_size: pageSize,
      density,
    });
  }

  function apply() {
    const phrase = searchable(search.value.trim());
    let filtered = records.filter(record => !phrase || record.search.includes(phrase));
    if (sortIndex !== null) {
      filtered = [...filtered].sort((left, right) => {
        const result = left.sortValues[sortIndex].localeCompare(right.sortValues[sortIndex], 'pl', {
          numeric: true, sensitivity: 'base',
        });
        return sortDirection === 'desc' ? -result : result;
      });
    } else {
      filtered = [...filtered].sort((left, right) => left.originalIndex - right.originalIndex);
    }

    records.forEach(record => { record.element.hidden = true; });
    filtered.forEach(record => body.append(record.element));
    body.append(noResults);

    const effectivePageSize = pageSize || Math.max(filtered.length, 1);
    const totalPages = Math.max(1, Math.ceil(filtered.length / effectivePageSize));
    currentPage = Math.max(1, Math.min(currentPage, totalPages));
    const start = (currentPage - 1) * effectivePageSize;
    const visible = filtered.slice(start, start + effectivePageSize);
    visible.forEach(record => { record.element.hidden = false; });

    noResults.hidden = filtered.length > 0;
    const visibleStart = filtered.length ? start + 1 : 0;
    const visibleEnd = filtered.length ? Math.min(start + effectivePageSize, filtered.length) : 0;
    count.textContent = phrase ? `${filtered.length} z ${rows.length} pozycji` : `${rows.length} pozycji`;
    footer.firstChild.textContent = filtered.length ? `${visibleStart}–${visibleEnd} z ${filtered.length}` : '0 pozycji';
    pageInfo.textContent = `${currentPage} / ${totalPages}`;
    previousPage.disabled = currentPage <= 1;
    nextPage.disabled = currentPage >= totalPages;
    pagination.hidden = totalPages <= 1;

    headerCells.forEach((th, index) => {
      const buttonElement = th.querySelector('.table-sort-button');
      const indicator = th.querySelector('.table-sort-indicator');
      const active = sortIndex === index;
      th.setAttribute('aria-sort', active ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none');
      if (indicator) indicator.textContent = active ? (sortDirection === 'asc' ? '↑' : '↓') : '↕';
      buttonElement?.classList.toggle('active', active);
    });
  }

  search.addEventListener('input', () => {
    currentPage = 1;
    apply();
  });

  wrapper.append(toolbar, scroll, footer);
  apply();
  return wrapper;
}
