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

  function summaryRow(label, value) {
    return node('div', { class: 'blueprint-wizard-review-row' },
      node('span', { text: label }),
      node('strong', { text: String(value ?? '—') }));
  }

  parts.ui = { errorText, summaryRow };
  registerExtension('blueprint-wizard-ui', () => {});
})();
