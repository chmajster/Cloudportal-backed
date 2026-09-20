'use strict';

(() => {
function fixedApmid(item) {
  const explicit = String(item?.deployment?.apmid || '').trim();
  if (explicit) return explicit.toUpperCase();

  const tags = Array.isArray(item?.deployment?.variables?.tags) ? item.deployment.variables.tags : [];
  for (const raw of tags) {
    const match = String(raw || '').trim().match(/^apmid-([a-z0-9][a-z0-9_-]{0,62})$/i);
    if (match) return match[1].toUpperCase();
  }
  return '';
}

function fixedEnvironment(item) {
  const explicit = String(item?.deployment?.environment || '').trim().toLowerCase();
  if (explicit) return explicit;

  const tags = Array.isArray(item?.deployment?.variables?.tags) ? item.deployment.variables.tags : [];
  for (const raw of tags) {
    const match = String(raw || '').trim().match(/^env-(test|dev|nonprod|prod)$/i);
    if (match) return match[1].toLowerCase();
  }
  return '';
}

function allowsRuntimeApmid(item, fixed) {
  const deployment = item?.deployment || {};
  if (deployment.select_apmid_on_execute === true) return true;
  if (Object.prototype.hasOwnProperty.call(deployment, 'select_apmid_on_execute')) return false;
  return !fixed;
}

function allowsRuntimeEnvironment(item) {
  return item?.deployment?.select_environment_on_execute === true;
}

async function prepare(item, fields) {
  const fixedAp = fixedApmid(item);
  const fixedEnv = fixedEnvironment(item);
  const apmidSelectable = item?.deployment?.template === 'proxmox-vm' && allowsRuntimeApmid(item, fixedAp);
  const environmentSelectable = item?.deployment?.template === 'proxmox-vm' && allowsRuntimeEnvironment(item);

  let classification = null;
  if (apmidSelectable || environmentSelectable) {
    classification = await api('/vm-classification/options');
  }

  if (environmentSelectable) {
    const environments = ['test', 'dev', 'nonprod', 'prod']
      .filter(name => classification?.environments?.[name] !== false);
    if (!environments.length) {
      throw new Error('Blueprint wymaga wyboru Environment, ale wszystkie środowiska są wyłączone.');
    }
    const selected = environments.includes(fixedEnv) ? fixedEnv : environments[0];
    fields.append(formSection(
      'Environment',
      'Ten Blueprint pozwala wybrać środowisko podczas tworzenia VM.',
      selectField('Environment', 'runtime_environment',
        environments.map(value => ({ value, label: value.toUpperCase() })),
        selected,
        {
          required: true,
          wide: true,
          help: 'Wybór zmieni tag env-*, tag APMID.ENV oraz token {env}/{environment} w hostname, jeśli pattern go używa.',
        })
    ));
  } else if (fixedEnv) {
    fields.append(node('div', {
      class: 'field-help wide',
      text: 'Environment jest zdefiniowany w Blueprintcie: ' + fixedEnv.toUpperCase() + '.',
    }));
  }

  if (apmidSelectable) {
    const apmids = [...new Set(
      (classification?.apmids || [])
        .map(value => String(value).trim().toUpperCase())
        .filter(Boolean)
    )];
    if (!apmids.length) {
      throw new Error('Blueprint wymaga wyboru APMID, ale w ustawieniach nie ma żadnego APMID.');
    }
    const selected = apmids.includes(fixedAp)
      ? fixedAp
      : apmids.includes('LEO') ? 'LEO' : apmids[0];
    fields.append(formSection(
      'APMID',
      'Ten Blueprint pozwala wybrać APMID podczas tworzenia VM.',
      selectField('APMID', 'runtime_apmid',
        apmids.map(value => ({ value, label: value })),
        selected,
        {
          required: true,
          wide: true,
          help: 'Wybrany APMID zostanie zapisany w tagach Proxmox i połączony z wybranym Environment.',
        })
    ));
  } else if (fixedAp) {
    fields.append(node('div', {
      class: 'field-help wide',
      text: 'APMID jest zdefiniowany w Blueprintcie: ' + fixedAp + '.',
    }));
  }

  return {
    fixedApmid: fixedAp,
    fixedEnvironment: fixedEnv,
    apmidSelectable,
    environmentSelectable,
  };
}

function read(form, context) {
  return {
    apmid: context?.apmidSelectable
      ? String(form.elements.runtime_apmid?.value || '').trim().toUpperCase()
      : '',
    environment: context?.environmentSelectable
      ? String(form.elements.runtime_environment?.value || '').trim().toLowerCase()
      : '',
  };
}

registerExtension('blueprint-runtime-apmid', () => {
  window.BlueprintRuntimeApmid = {
    fixedApmid,
    fixedEnvironment,
    allowsRuntimeApmid,
    allowsRuntimeEnvironment,
    prepare,
    read,
  };
});
})();
