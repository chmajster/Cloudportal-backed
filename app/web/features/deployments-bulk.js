'use strict';

(() => {
const selection = new Set();
const DEFAULT_BATCH_SIZE = 25;

document.addEventListener('cloudportal:app-hidden', () => selection.clear());

const POWER_ACTIONS = Object.freeze([
  { id: 'power_on', label: 'Uruchom', kind: 'primary' },
  { id: 'shutdown', label: 'Wyłącz', kind: 'ghost' },
  { id: 'reboot', label: 'Restart', kind: 'ghost' },
  { id: 'power_off', label: 'Wymuś stop', kind: 'danger' },
]);

function resourceId(item) {
  return item?.id === undefined || item?.id === null ? '' : String(item.id);
}

function canSelect(item) {
  return Boolean(resourceId(item))
    && item.lifecycle_status === 'active'
    && allowed('day2.view')
    && allowed('day2.power');
}

function isBulkLimitError(error) {
  return error?.status === 422
    && /bulk action exceeds the configured maximum size/i.test(String(error.message || ''));
}

function requestPayload(actionId, ids, label) {
  return {
    action: actionId,
    resource_ids: ids,
    parameters: {},
    reason: `Masowa akcja „${label}” z widoku Moje zasoby`,
  };
}

async function submitChunk(actionId, ids, label) {
  try {
    const result = await api('/day2-actions/bulk', {
      method: 'POST',
      idempotent: true,
      body: requestPayload(actionId, ids, label),
    });
    return [result];
  } catch (error) {
    if (!isBulkLimitError(error) || ids.length <= 1) throw error;
    const middle = Math.ceil(ids.length / 2);
    return [
      ...(await submitChunk(actionId, ids.slice(0, middle), label)),
      ...(await submitChunk(actionId, ids.slice(middle), label)),
    ];
  }
}

async function submitBulk(actionId, items, label) {
  const ids = [...new Set(items.map(resourceId).filter(Boolean))];
  const batches = [];
  for (let offset = 0; offset < ids.length; offset += DEFAULT_BATCH_SIZE) {
    batches.push(ids.slice(offset, offset + DEFAULT_BATCH_SIZE));
  }

  const results = [];
  for (const batch of batches) {
    results.push(...(await submitChunk(actionId, batch, label)));
  }

  return results.reduce((summary, result) => {
    summary.children.push(...(result.children || []));
    summary.errors.push(...(result.errors || []));
    return summary;
  }, { children: [], errors: [] });
}

function actionMeta(actionId) {
  return POWER_ACTIONS.find(item => item.id === actionId)
    || { id: actionId, label: actionId, kind: 'ghost' };
}

function requestAction(actionId, items, onComplete) {
  const selected = items.filter(item => canSelect(item) && selection.has(resourceId(item)));
  if (!selected.length) {
    toast('Wybierz co najmniej jedną VM.', 'warning');
    return;
  }

  const action = actionMeta(actionId);
  const preview = selected.slice(0, 5).map(item => item.name || ('VM ' + item.vm_id)).join(', ');
  const remainder = selected.length > 5 ? ` i ${selected.length - 5} więcej` : '';

  confirmAction(
    `Masowa akcja: ${action.label}`,
    `${action.label} zostanie zlecone dla ${selected.length} VM: ${preview}${remainder}.`,
    async () => {
      const result = await submitBulk(action.id, selected, action.label);
      const accepted = result.children.length;
      const rejected = result.errors.length;
      selection.clear();

      if (rejected) {
        const first = result.errors[0];
        toast(
          `Zlecono ${accepted} z ${selected.length} VM. Odrzucono ${rejected}: ${first?.message || first?.code || 'błąd walidacji'}.`,
          accepted ? 'warning' : 'error'
        );
      } else {
        toast(`Zlecono „${action.label}” dla ${accepted} VM.`);
      }

      if (typeof onComplete === 'function') await onComplete();
    },
  );
}

function decorateCard(card, item, onSelectionChange = null) {
  const id = resourceId(item);
  const selectable = canSelect(item);
  if (!id) return card;

  card.dataset.vmSelectionId = id;
  const selector = node('input', {
    type: 'checkbox',
    class: 'my-resource-select',
    checked: selection.has(id),
    disabled: !selectable,
    'aria-label': 'Wybierz ' + (item.name || ('VM ' + item.vm_id)),
  });

  selector.addEventListener('change', () => {
    if (selector.checked) selection.add(id);
    else selection.delete(id);
    card.classList.toggle('selected', selector.checked);
    if (typeof onSelectionChange === 'function') onSelectionChange();
  });

  card.classList.toggle('selected', selector.checked);
  card.querySelector('.my-resource-card-head')?.prepend(selector);
  return card;
}

function toolbar(vms, grid, onComplete) {
  if (!allowed('day2.view') || !allowed('day2.power')) return null;

  const selectable = vms.filter(canSelect);
  const selectableIds = new Set(selectable.map(resourceId));
  for (const id of [...selection]) {
    if (!selectableIds.has(id)) selection.delete(id);
  }
  if (!selectable.length) return null;

  const selectAll = node('input', {
    type: 'checkbox',
    class: 'vm-bulk-select-all',
    'aria-label': 'Wybierz wszystkie maszyny wirtualne',
  });
  const selectedCount = node('strong', { class: 'vm-bulk-selected-count' });
  const clear = button('Wyczyść', () => {
    selection.clear();
    sync();
  }, 'ghost');

  const actionButtons = POWER_ACTIONS.map(action => {
    const control = button(action.label, () => requestAction(action.id, vms, onComplete), action.kind);
    control.dataset.bulkVmAction = action.id;
    return control;
  });

  const element = node('div', { class: 'my-resources-bulk-bar' },
    node('label', { class: 'my-resources-select-all' },
      selectAll,
      node('span', { text: 'Wybierz wszystkie' })),
    node('span', { class: 'vm-bulk-selection-summary' },
      selectedCount,
      node('span', { class: 'muted', text: ' zaznaczonych' })),
    node('div', { class: 'my-resources-bulk-actions' }, ...actionButtons, clear));

  function sync() {
    const count = [...selection].filter(id => selectableIds.has(id)).length;
    selectedCount.textContent = String(count);
    selectAll.checked = count > 0 && count === selectable.length;
    selectAll.indeterminate = count > 0 && count < selectable.length;
    clear.disabled = count === 0;
    actionButtons.forEach(control => { control.disabled = count === 0; });

    grid.querySelectorAll('.my-resource-vm-card').forEach(card => {
      const id = String(card.dataset.vmSelectionId || '');
      const selected = selection.has(id);
      card.classList.toggle('selected', selected);
      const checkbox = card.querySelector('.my-resource-select');
      if (checkbox) checkbox.checked = selected;
    });
  }

  selectAll.addEventListener('change', () => {
    if (selectAll.checked) selectableIds.forEach(id => selection.add(id));
    else selectableIds.forEach(id => selection.delete(id));
    sync();
  });

  sync();
  return { element, sync };
}

registerExtension('deployments-bulk-vm-actions', () => {
  window.vmBulkActions = Object.freeze({
    decorateCard,
    toolbar,
  });
});
})();
