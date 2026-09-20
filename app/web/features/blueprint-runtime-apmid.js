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

async function prepare(item, fields) {
  const fixed = fixedApmid(item);
  const runtimeRequired = item?.deployment?.template === 'proxmox-vm' && !fixed;

  if (runtimeRequired) {
    const classification = await api('/vm-classification/options');
    const apmids = [...new Set(
      (classification.apmids || [])
        .map(value => String(value).trim().toUpperCase())
        .filter(Boolean)
    )];

    if (!apmids.length) {
      throw new Error('Blueprint nie ma zdefiniowanego APMID, a w ustawieniach nie ma żadnego APMID do wyboru.');
    }

    fields.append(formSection(
      'Klasyfikacja VM',
      'Ten Blueprint nie ma przypisanego APMID. Wybierz APMID dla tworzonej maszyny.',
      selectField('APMID', 'runtime_apmid', [
        { value: '', label: 'Wybierz APMID' },
        ...apmids.map(value => ({ value, label: value })),
      ], '', {
        required: true,
        wide: true,
        help: 'Wybrany APMID zostanie zapisany jako tag Proxmox. Jeśli Blueprint ma tag środowiska, zostanie również dodany tag APMID.ENV.',
      })
    ));
  } else if (fixed) {
    fields.append(node('div', {
      class: 'field-help wide',
      text: 'APMID jest zdefiniowany w Blueprintcie: ' + fixed + '.',
    }));
  }

  return { fixed, runtimeRequired };
}

function read(form, context) {
  if (!context?.runtimeRequired) return '';
  return String(form.elements.runtime_apmid?.value || '').trim().toUpperCase();
}

registerExtension('blueprint-runtime-apmid', () => {
  window.BlueprintRuntimeApmid = { fixedApmid, prepare, read };
});
})();
