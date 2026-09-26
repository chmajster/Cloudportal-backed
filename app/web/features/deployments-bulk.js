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

function canPower(item) {
  return Boolean(resourceId(item))
    && item.lifecycle_status === 'active'
    && allowed('day2.view')
    && allowed('day2.power');
}

function isTerraformManaged(item) {
  return item?.management_mode === 'terraform' && Boolean(item?.deployment_id);
}

function canDelete(item) {
  if (!resourceId(item) || item.lifecycle_status !== 'active') return false;
  if (isTerraformManaged(item)) {
    return allowed('deployments.destroy')
      && allowed('jobs.execute')
      && allowed('terraform.execute');
  }
  return allowed('day2.view') && allowed('day2.delete');
}

function canSelect(item) {
  return canPower(item) || canDelete(item);
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

function chunkKey(actionId, ids) {
  return actionId + ':' + ids.join(',');
}

function idempotencyKeyFor(keys, actionId, ids) {
  const key = chunkKey(actionId, ids);
  if (!keys.has(key)) keys.set(key, crypto.randomUUID());
  return keys.get(key);
}

async function submitChunk(actionId, ids, label, keys, onSubmitted) {
  try {
    const result = await api('/day2-actions/bulk', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKeyFor(keys, actionId, ids) },
      body: requestPayload(actionId, ids, label),
    });
    if (typeof onSubmitted === 'function') onSubmitted(ids, result);
    return [result];
  } catch (error) {
    if (!isBulkLimitError(error) || ids.length <= 1) throw error;
    const middle = Math.ceil(ids.length / 2);
    return [
      ...(await submitChunk(actionId, ids.slice(0, middle), label, keys, onSubmitted)),
      ...(await submitChunk(actionId, ids.slice(middle), label, keys, onSubmitted)),
    ];
  }
}

async function submitBulk(actionId, items, label, keys) {
  const ids = [...new Set(items.map(resourceId).filter(Boolean))];
  const batches = [];
  for (let offset = 0; offset < ids.length; offset += DEFAULT_BATCH_SIZE) {
    batches.push(ids.slice(offset, offset + DEFAULT_BATCH_SIZE));
  }

  const results = [];
  const markSubmitted = submittedIds => {
    submittedIds.forEach(id => selection.delete(String(id)));
  };
  for (const batch of batches) {
    results.push(...(await submitChunk(actionId, batch, label, keys, markSubmitted)));
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
  const selected = items.filter(item => canPower(item) && selection.has(resourceId(item)));
  if (!selected.length) {
    toast('Wybierz co najmniej jedną VM.', 'warning');
    return;
  }

  const action = actionMeta(actionId);
  const preview = selected.slice(0, 5).map(item => item.name || ('VM ' + item.vm_id)).join(', ');
  const remainder = selected.length > 5 ? ` i ${selected.length - 5} więcej` : '';
  const idempotencyKeys = new Map();

  confirmAction(
    `Masowa akcja: ${action.label}`,
    `${action.label} zostanie zlecone dla ${selected.length} VM: ${preview}${remainder}.`,
    async () => {
      const pending = selected.filter(item => selection.has(resourceId(item)));
      if (!pending.length) {
        toast('Wszystkie wybrane VM zostały już obsłużone.');
        if (typeof onComplete === 'function') await onComplete();
        return;
      }

      const result = await submitBulk(action.id, pending, action.label, idempotencyKeys);
      const accepted = result.children.length;
      const rejected = result.errors.length;

      if (rejected) {
        const first = result.errors[0];
        toast(
          `Zlecono ${accepted} z ${pending.length} VM. Odrzucono ${rejected}: ${first?.message || first?.code || 'błąd walidacji'}.`,
          accepted ? 'warning' : 'error'
        );
      } else {
        toast(`Zlecono „${action.label}” dla ${accepted} VM.`);
      }

      if (typeof onComplete === 'function') await onComplete();
    },
  );
}

function deleteConfirmationName(item) {
  return String(item?.name || ('vm-' + item?.vm_id));
}

async function submitDeleteItem(item, idempotencyKeys) {
  const id = resourceId(item);
  if (isTerraformManaged(item)) {
    const key = 'terraform-destroy:' + item.deployment_id;
    if (!idempotencyKeys.has(key)) idempotencyKeys.set(key, crypto.randomUUID());
    const result = await api('/deployments/' + encodeURIComponent(item.deployment_id) + '/destroy', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKeys.get(key) },
      body: {},
    });
    selection.delete(id);
    return { item, result, mode: 'terraform' };
  }

  const key = 'day2-delete:' + id;
  if (!idempotencyKeys.has(key)) idempotencyKeys.set(key, crypto.randomUUID());
  const result = await api('/resources/' + encodeURIComponent(id) + '/actions/delete_vm', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKeys.get(key) },
    body: {
      parameters: {
        confirmation: deleteConfirmationName(item),
        purge: false,
        destroy_unreferenced_disks: false,
      },
      reason: 'Masowe usuwanie VM z widoku Moje zasoby',
    },
  });
  selection.delete(id);
  return { item, result, mode: 'day2' };
}

function requestDelete(items, onComplete) {
  const selected = items.filter(item => canDelete(item) && selection.has(resourceId(item)));
  if (!selected.length) {
    toast('Wybierz co najmniej jedną VM, którą możesz usunąć.', 'warning');
    return;
  }

  const preview = selected.slice(0, 5).map(item => item.name || ('VM ' + item.vm_id)).join(', ');
  const remainder = selected.length > 5 ? ` i ${selected.length - 5} więcej` : '';
  const terraformCount = selected.filter(isTerraformManaged).length;
  const providerCount = selected.length - terraformCount;
  const modes = [
    terraformCount ? `${terraformCount} przez Terraform destroy` : '',
    providerCount ? `${providerCount} bezpośrednio u providera` : '',
  ].filter(Boolean).join(', ');
  const idempotencyKeys = new Map();

  confirmAction(
    'Usuń zaznaczone VM',
    `Trwale usuń ${selected.length} VM: ${preview}${remainder}. Tryb: ${modes}. Operacji nie można cofnąć.`,
    async () => {
      const pending = selected.filter(item => canDelete(item) && selection.has(resourceId(item)));
      if (!pending.length) {
        toast('Wszystkie wybrane VM zostały już obsłużone.');
        if (typeof onComplete === 'function') await onComplete();
        return;
      }

      const accepted = [];
      const rejected = [];
      for (const item of pending) {
        try {
          accepted.push(await submitDeleteItem(item, idempotencyKeys));
        } catch (error) {
          rejected.push({ item, error });
        }
      }

      if (rejected.length) {
        const first = rejected[0];
        toast(
          `Zlecono usunięcie ${accepted.length} z ${pending.length} VM. Odrzucono ${rejected.length}: ${first.error?.message || 'błąd walidacji'}.`,
          accepted.length ? 'warning' : 'error'
        );
      } else {
        toast(`Zlecono usunięcie ${accepted.length} VM.`);
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
  card.classList.add('has-selection-control');
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
  const powerEnabled = allowed('day2.view') && allowed('day2.power');
  const deleteEnabled = vms.some(canDelete);
  if (!powerEnabled && !deleteEnabled) return null;

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

  const actionButtons = powerEnabled ? POWER_ACTIONS.map(action => {
    const control = button(action.label, () => requestAction(action.id, vms, onComplete), action.kind);
    control.dataset.bulkVmAction = action.id;
    return control;
  }) : [];
  const deleteButton = deleteEnabled
    ? button('Usuń', () => requestDelete(vms, onComplete), 'danger')
    : null;
  if (deleteButton) deleteButton.dataset.bulkVmAction = 'delete_vm';

  const element = node('div', { class: 'my-resources-bulk-bar' },
    node('label', { class: 'my-resources-select-all' },
      selectAll,
      node('span', { text: 'Wybierz wszystkie' })),
    node('span', { class: 'vm-bulk-selection-summary' },
      selectedCount,
      node('span', { class: 'muted', text: ' zaznaczonych' })),
    node('div', { class: 'my-resources-bulk-actions' }, ...actionButtons, deleteButton, clear));

  function sync() {
    const count = [...selection].filter(id => selectableIds.has(id)).length;
    selectedCount.textContent = String(count);
    selectAll.checked = count > 0 && count === selectable.length;
    selectAll.indeterminate = count > 0 && count < selectable.length;
    clear.disabled = count === 0;
    const selectedItems = vms.filter(item => selection.has(resourceId(item)));
    const powerCount = selectedItems.filter(canPower).length;
    const deleteCount = selectedItems.filter(canDelete).length;
    actionButtons.forEach(control => { control.disabled = powerCount === 0; });
    if (deleteButton) deleteButton.disabled = deleteCount === 0;

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
