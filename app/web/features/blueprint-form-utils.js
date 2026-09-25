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

function blueprintTemplateVariableField(name, spec, value) {
  const type = schemaType(spec);
  const label = FIELD_LABELS[name] || spec.title || name;
  const current = value ?? spec.default ?? '';
  const wrapper = field(label, `deployment_var_${name}`, {
    tag: type === 'array' ? 'textarea' : 'input',
    value: Array.isArray(current) ? current.join('\n') : String(current),
    wide: type === 'array' || ['ssh_public_key', 'subnet_id'].includes(name),
    help: `Typ: ${type}. Możesz użyć wartości lub placeholdera, np. {{ cpu }}.`,
  });
  wrapper.dataset.blueprintTemplateVariable = name;
  return wrapper;
}

function readBlueprintTemplateVariables(root, template) {
  const result = {};
  for (const [name, spec] of Object.entries(template?.variables_schema?.properties || {})) {
    const control = root.elements?.[`deployment_var_${name}`] || root.querySelector?.(`[name="deployment_var_${name}"]`);
    if (!control) continue;
    const raw = String(control.value ?? '').trim();
    if (!raw) continue;
    if (/{{\s*[^}]+\s*}}/.test(raw)) { result[name] = raw; continue; }
    const type = schemaType(spec);
    if (type === 'integer') result[name] = Number.parseInt(raw, 10);
    else if (type === 'number') result[name] = Number(raw);
    else if (type === 'boolean') result[name] = ['true', '1', 'tak', 'yes'].includes(raw.toLowerCase());
    else if (type === 'array') result[name] = splitValues(raw);
    else result[name] = raw;
  }
  return result;
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
  const add = (id, type, overrides = {}) => {
    const defaultTimeout = ['terraform_plan', 'terraform_apply', 'terraform_destroy'].includes(type) ? 3600 : type === 'wait_for_ip' ? 180 : 600;
    steps.push({
      id,
      type,
      depends_on: [...previous],
      conditions: {},
      retry: overrides.retry ?? 0,
      timeout: overrides.timeout ?? defaultTimeout,
      rollback: null,
    });
    previous = [id];
  };
  if (options.cloudInit) add('cloud_init', 'cloud_init');
  add('apply', 'terraform_apply');
  if (options.waitAgent) add('agent', 'wait_for_agent');
  if (options.waitAgent || options.ansible || options.guestAccess || options.awx) add('guest_ip', 'wait_for_ip');
  if (options.guestAccess) add('guest_ssh', 'wait_for_ssh');
  if (options.ansible) add('ansible', 'run_ansible_playbook');
  if (options.awx) add('awx', 'register_awx', {
    retry: Number(options.awxRetry ?? 3),
    timeout: Number(options.awxTimeout ?? 300),
  });
  return steps;
}

registerExtension('blueprint-form-utils', () => {
  window.BlueprintFormUtils = Object.freeze({
    jsonValue, parseObject, parseArray, hostnamePatternTokens, normalizeHostnamePattern,
    blueprintTags, setSelectChoices, blueprintWorkflow,
    blueprintTemplateVariableField, readBlueprintTemplateVariables,
  });
});
})();
