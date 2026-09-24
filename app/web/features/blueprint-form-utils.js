'use strict';

(() => {
function jsonValue(value) {
  return JSON.stringify(value, null, 2);
}

function parseObject(value, label) {
  try {
    const parsed = JSON.parse(value);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error();
    return parsed;
  } catch {
    throw new Error(label + ' musi zawierać poprawny obiekt JSON.');
  }
}

function parseArray(value, label) {
  try {
    const parsed = JSON.parse(value);
    if (!Array.isArray(parsed)) throw new Error();
    return parsed;
  } catch {
    throw new Error(label + ' musi zawierać poprawną tablicę JSON.');
  }
}

function hostnamePatternTokens(pattern) {
  const automatic = new Set(['number', 'random', 'year']);
  return [...new Set(Array.from(String(pattern || '').matchAll(/{([a-z]+)}/g), match => match[1]))]
    .filter(token => !automatic.has(token));
}

function normalizeHostnamePattern(pattern, padding = 3) {
  const raw = String(pattern || '').trim();
  const runs = [...raw.matchAll(/X{1,9}/g)];
  if (!raw.includes('{number}') && !raw.includes('{random}') && runs.length === 1) {
    return {
      pattern: raw.replace(runs[0][0], '{number}'),
      padding: runs[0][0].length,
    };
  }
  return { pattern: raw, padding: Number(padding || 3) };
}

function blueprintTags(value) {
  return [...new Set(String(value || '').split(/[,\n]+/).map(item => item.trim().toLowerCase()).filter(Boolean))];
}

function setSelectChoices(select, choices, selected = '', placeholder = '') {
  select.replaceChildren();
  if (placeholder) select.append(node('option', { value: '', text: placeholder }));
  choices.forEach(choice => select.append(node('option', {
    value: choice.value,
    text: choice.label,
    selected: String(choice.value) === String(selected),
  })));
}

function blueprintWorkflow(options) {
  const steps = [];
  let previous = [];
  const add = (id, type) => {
    const timeout = ['terraform_plan', 'terraform_apply', 'terraform_destroy'].includes(type) ? 3600 : 600;
    steps.push({ id, type, depends_on: [...previous], conditions: {}, retry: 0, timeout, rollback: null });
    previous = [id];
  };
  add('apply', 'terraform_apply');
  if (options.waitAgent) add('agent', 'wait_for_agent');
  if (options.waitAgent || options.ansible || options.guestAccess) add('guest_ip', 'wait_for_ip');
  if (options.guestAccess) add('guest_ssh', 'wait_for_ssh');
  if (options.ansible) add('ansible', 'run_ansible_playbook');
  return steps;
}

registerExtension('blueprint-form-utils', () => {
  window.BlueprintFormUtils = Object.freeze({
    jsonValue, parseObject, parseArray, hostnamePatternTokens, normalizeHostnamePattern,
    blueprintTags, setSelectChoices, blueprintWorkflow,
  });
});
})();
