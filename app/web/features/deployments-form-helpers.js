'use strict';

function deploymentVariableWrapper(container, name) {
  return container.querySelector('[data-template-variable="' + name + '"]');
}

function deploymentVariableRequired(template, name) {
  return new Set(template?.variables_schema?.required || []).has(name);
}

function deploymentSetSelectChoices(select, choices, selected = '', placeholder = '') {
  select.replaceChildren();
  if (placeholder) select.append(node('option', { value: '', text: placeholder }));
  choices.forEach(choice => select.append(node('option', {
    value: choice.value,
    text: choice.label,
    selected: String(choice.value) === String(selected),
  })));
  if (!select.value && choices.length === 1) select.value = String(choices[0].value);
}

function deploymentReplaceVariableWithSelect(container, template, name, label, choices, selected, placeholder, help) {
  const current = deploymentVariableWrapper(container, name);
  if (!current) return null;
  const replacement = selectField(label, name, choices, selected, {
    required: deploymentVariableRequired(template, name),
    placeholder,
  });
  replacement.setAttribute('data-template-variable', name);
  if (help) replacement.append(node('small', { class: 'field-help', text: help }));
  current.replaceWith(replacement);
  return replacement.querySelector('select');
}
