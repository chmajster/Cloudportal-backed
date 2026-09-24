'use strict';

(() => {
  function splitReferences(value) {
    if (Array.isArray(value)) {
      return [...new Set(value.map(item => String(item || '').trim()).filter(Boolean))];
    }
    return [...new Set(String(value || '').split(/[,\n]+/).map(item => item.trim()).filter(Boolean))];
  }

  function dependencyErrors(steps) {
    const rows = (steps || []).map(step => ({
      id: String(step?.id || '').trim(),
      depends_on: splitReferences(step?.depends_on || []),
    }));
    const errors = [];
    const ids = rows.map(step => step.id);
    const counts = new Map();
    ids.filter(Boolean).forEach(id => counts.set(id, (counts.get(id) || 0) + 1));

    rows.forEach((step, index) => {
      const label = step.id || ('krok #' + (index + 1));
      if (!step.id) {
        errors.push('Każdy krok workflow musi mieć ID.');
        return;
      }
      if ((counts.get(step.id) || 0) > 1) {
        errors.push('ID kroku „' + step.id + '” występuje więcej niż raz.');
      }
      if (step.depends_on.includes(step.id)) {
        errors.push('Krok „' + step.id + '” nie może zależeć od samego siebie.');
      }
      const missing = step.depends_on.filter(id => !counts.has(id));
      if (missing.length) {
        errors.push(
          'Krok „' + label + '” zależy od nieistniejących kroków: ' + missing.join(', ') + '.'
        );
      }
    });

    const uniqueIds = new Set(ids.filter(id => (counts.get(id) || 0) === 1));
    const graph = new Map(rows
      .filter(step => uniqueIds.has(step.id))
      .map(step => [step.id, step.depends_on.filter(id => uniqueIds.has(id))]));
    const visiting = new Set();
    const visited = new Set();
    const stack = [];
    let cycle = null;

    const visit = id => {
      if (cycle || visited.has(id)) return;
      if (visiting.has(id)) {
        const start = stack.indexOf(id);
        cycle = [...stack.slice(start), id];
        return;
      }
      visiting.add(id);
      stack.push(id);
      for (const parent of graph.get(id) || []) visit(parent);
      stack.pop();
      visiting.delete(id);
      visited.add(id);
    };
    graph.forEach((_parents, id) => visit(id));
    if (cycle) errors.push('Workflow zawiera cykl zależności: ' + cycle.join(' → ') + '.');

    return [...new Set(errors)];
  }

  function assertValid(steps) {
    const errors = dependencyErrors(steps);
    if (errors.length) throw new Error(errors[0]);
    return steps;
  }

  function rewriteReferences(root, oldId, newId) {
    const previous = String(oldId || '').trim();
    const next = String(newId || '').trim();
    if (!root || !previous || previous === next) return;

    root.querySelectorAll('[data-workflow-row]').forEach(row => {
      const depends = row.querySelector('[name="workflow_depends"]');
      if (depends) {
        const values = splitReferences(depends.value)
          .map(id => id === previous ? next : id)
          .filter(Boolean);
        depends.value = [...new Set(values)].join(', ');
      }
      const rollback = row.querySelector('[name="workflow_rollback"]');
      if (rollback && rollback.value.trim() === previous) rollback.value = next;
    });
  }

  function detachReferences(root, removedId) {
    const removed = String(removedId || '').trim();
    if (!root || !removed) return;
    root.querySelectorAll('[data-workflow-row]').forEach(row => {
      const depends = row.querySelector('[name="workflow_depends"]');
      if (depends) {
        depends.value = splitReferences(depends.value)
          .filter(id => id !== removed)
          .join(', ');
      }
      const rollback = row.querySelector('[name="workflow_rollback"]');
      if (rollback && rollback.value.trim() === removed) rollback.value = '';
    });
  }

  function bindIdTracking(row, workflowRoot, onChange) {
    const input = row?.querySelector?.('[name="workflow_id"]');
    if (!input) return;
    row.dataset.workflowStepId = String(input.value || '').trim();
    input.addEventListener('change', () => {
      const previous = String(row.dataset.workflowStepId || '').trim();
      const next = String(input.value || '').trim();
      if (previous && next && previous !== next) rewriteReferences(workflowRoot, previous, next);
      row.dataset.workflowStepId = next;
      if (typeof onChange === 'function') onChange();
    });
  }

  function removeRow(workflowRoot, row) {
    const id = row?.querySelector?.('[name="workflow_id"]')?.value?.trim() || '';
    detachReferences(workflowRoot, id);
    row?.remove?.();
  }

  window.BlueprintClassicWorkflow = Object.freeze({
    splitReferences,
    dependencyErrors,
    assertValid,
    rewriteReferences,
    detachReferences,
    bindIdTracking,
    removeRow,
  });
  registerExtension('blueprint-classic-workflow', () => {});
})();
