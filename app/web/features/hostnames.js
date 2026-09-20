'use strict';

(() => {
function generatorPatternTokens(pattern) {
  const automatic = new Set(['number', 'random', 'year']);
  return [...new Set(Array.from(String(pattern || '').matchAll(/{([a-z]+)}/g), match => match[1]))]
    .filter(token => !automatic.has(token));
}

function normalizeGeneratorPattern(pattern, padding = 3) {
  const raw = String(pattern || '').trim();
  const runs = [...raw.matchAll(/X{1,9}/g)];
  if (!raw.includes('{number}') && !raw.includes('{random}') && runs.length === 1) {
    return { pattern: raw.replace(runs[0][0], '{number}'), padding: runs[0][0].length };
  }
  return { pattern: raw, padding: Number(padding || 3) };
}

function generatorValueFields(pattern, values = {}) {
  const wrapper = node('div', { class: 'form-grid hostname-values wide' });
  const tokens = generatorPatternTokens(pattern);
  const globalTokens = tokens.filter(token => ['location', 'role'].includes(token));
  if (globalTokens.length) {
    wrapper.append(node('div', { class: 'blueprint-wizard-info wide' },
      node('strong', { text: 'Location i Role są pobierane z Narzędzi.' }),
      node('span', { text: globalTokens.map(token => '{' + token + '}').join(', ') + ' zostaną uzupełnione automatycznie.' })));
  }
  tokens.filter(token => !['location', 'role'].includes(token)).forEach(token => {
    const item = field(token, 'hostname_' + token, { value: values[token] || '', required: true });
    item.dataset.hostnameToken = token;
    wrapper.append(item);
  });
  if (!tokens.length) wrapper.append(node('p', { class: 'muted wide', text: 'Ten pattern nie wymaga dodatkowych wartości.' }));
  return wrapper;
}

function readGeneratorValues(form) {
  const result = {};
  form.querySelectorAll('[data-hostname-token]').forEach(wrapper => {
    const input = wrapper.querySelector('input,select,textarea');
    if (input?.value) result[wrapper.dataset.hostnameToken] = input.value.trim();
  });
  return result;
}

function hostnameSchemePreview(pattern, padding, nextNumber) {
  const normalized = normalizeGeneratorPattern(pattern, padding);
  let value = String(normalized.pattern || '')
    .replaceAll('{year}', String(new Date().getFullYear()))
    .replaceAll('{random}', 'a1b2c3')
    .replaceAll('{number}', String(nextNumber || 1).padStart(Number(normalized.padding || 3), '0'));
  const samples = {
    location: 'wro', environment: 'prod', env: 'prod', application: 'app',
    service: 'api', role: 'server', os: 'linux', cluster: 'c1', site: 'dc1',
  };
  generatorPatternTokens(normalized.pattern).forEach(token => {
    value = value.replaceAll('{' + token + '}', samples[token] || token);
  });
  return value.toLowerCase();
}

async function hostnamesView() {
  const [schemes, reservations, blueprintResult] = await Promise.all([
    api('/hostname-schemes?limit=200'),
    api('/hostnames?limit=200'),
    allowed('blueprints.read') ? api('/blueprints?limit=200') : Promise.resolve({ items: [] }),
  ]);
  const blueprintUsage = new Map();
  blueprintResult.items.forEach(blueprint => {
    const schemeId = Number(blueprint.deployment?.hostname_scheme_id || 0);
    if (!schemeId) return;
    if (!blueprintUsage.has(schemeId)) blueprintUsage.set(schemeId, []);
    blueprintUsage.get(schemeId).push(blueprint);
  });
  const actions = [];
  if (allowed('hostnames.create')) actions.push(button('Nowy pattern', () => hostnameSchemeForm(), 'primary'));
  if (allowed('hostnames.reserve')) actions.push(button('Generuj hostname', () => generateHostname(schemes.items)));
  if (allowed('blueprints.read')) actions.push(button('Przejdź do Blueprintów', () => navigate('blueprints')));
  dom.content.replaceChildren(
    heading('Generator hostname tworzy centralne patterny nazw VM. Pattern można przypisać do Blueprintu, a podczas jego uruchomienia Cloudportal zarezerwuje kolejny unikalny hostname i użyje go jako nazwy VM.', [
      button('← Narzędzia', () => navigate('tools')),
      ...actions,
    ]),
    node('section', { class: 'panel hostname-generator-info' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Jak działa generator' }), badge('Blueprint ready', 'ok')),
      node('div', { class: 'checks' },
        info('1. Pattern', 'np. {location}-{env}-{role}-{number}'),
        info('2. Blueprint', 'wybiera zapisany pattern'),
        info('3. Execute', 'rezerwuje kolejny hostname'),
        info('4. VM', 'hostname staje się nazwą deploymentu i VM'))),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Patterny hostname' })),
      table([
        { label: 'Nazwa', value: item => item.name },
        { label: 'Pattern', value: item => node('span', { class: 'mono', text: item.pattern }) },
        { label: 'Następny numer', value: item => item.next_number },
        { label: 'Blueprinty', value: item => {
          const usage = blueprintUsage.get(Number(item.id)) || [];
          return usage.length ? badge(String(usage.length), 'info') : '—';
        }},
        { label: 'Status', value: item => badge(statusLabel(item.is_active ? 'active' : 'inactive'), item.is_active ? 'ok' : 'danger') },
      ], schemes.items, item => {
        const result = [];
        if (allowed('blueprints.create') && item.is_active && allowed('providers.read') && allowed('credentials.read') && allowed('terraform.read') && hasCommand('blueprints.proxmoxWithHostnameScheme')) {
          result.push(button('Użyj w Blueprint', () => runCommand('blueprints.proxmoxWithHostnameScheme', item.id), 'primary'));
        }
        if (allowed('hostnames.update')) result.push(button('Edytuj', () => hostnameSchemeForm(item)));
        if (allowed('hostnames.delete')) result.push(button('Usuń', () => confirmAction(
          'Usuń pattern hostname',
          `Pattern „${item.name}” zostanie usunięty, jeśli nie ma historii rezerwacji.`,
          async () => {
            await api(`/hostname-schemes/${item.id}`, { method: 'DELETE' });
            toast('Pattern hostname usunięty.');
            navigate('hostnames');
          },
        ), 'danger'));
        return result;
      })),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Rezerwacje' })),
      table([
        { label: 'Nazwa hosta', value: item => node('strong', { class: 'mono', text: item.hostname }) },
        { label: 'Status', value: item => badge(statusLabel(item.status), statusKind(item.status)) },
        { label: 'Zasób', value: item => short(item.resource_id, 18) },
        { label: 'Utworzono', value: item => formatDate(item.created_at) },
      ], reservations.items, item => {
        const result = [];
        if (allowed('hostnames.reserve') && item.status === 'reserved') result.push(button('Przypisz', () => assignHostname(item), 'primary'));
        if (allowed('hostnames.release') && item.status !== 'released') result.push(button('Zwolnij', () => confirmAction(
          'Zwolnij nazwę hosta',
          `${item.hostname} będzie ponownie dostępny po wygaśnięciu historii kolizji.`,
          async () => {
            await api(`/hostnames/${item.id}/release`, { method: 'POST' });
            toast('Nazwa hosta zwolniona.');
            navigate('hostnames');
          },
        ), 'danger'));
        return result;
      }))
  );
}

async function assignHostname(item) {
  try {
    const [deploymentResult, resourceResult] = await Promise.all([
      allowed('deployments.read') ? api('/deployments?limit=200') : Promise.resolve({ items: [] }),
      allowed('inventory.read') ? api('/inventory/resources?limit=200') : Promise.resolve({ items: [] }),
    ]);
    const choices = [
      ...deploymentResult.items.map(row => ({ value: row.id, label: `Wdrożenie: ${row.name}` })),
      ...resourceResult.items.map(row => ({ value: row.id, label: `Zasób: ${row.name || row.external_id}` })),
    ];
    const fields = node('div', { class: 'form-grid' });
    if (choices.length) fields.append(selectField('Zasób', 'known_resource_id', [{ value: '', label: 'Inny identyfikator' }, ...choices], ''));
    fields.append(field('Identyfikator zasobu', 'resource_id', {
      required: !choices.length, wide: true,
      help: choices.length ? 'Wybierz zasób z listy albo wpisz własny identyfikator.' : 'Podaj ID wdrożenia lub innego zasobu.',
    }));
    openModal({
      title: `Przypisz ${item.hostname}`,
      eyebrow: 'Generator hostname',
      body: fields,
      submitLabel: 'Przypisz',
      onSubmit: async data => {
        const resourceId = data.get('known_resource_id') || data.get('resource_id')?.trim();
        if (!resourceId) throw new Error('Wybierz lub podaj identyfikator zasobu.');
        await api(`/hostnames/${item.id}/assign?resource_id=${encodeURIComponent(resourceId)}`, { method: 'POST' });
        toast('Nazwa hosta przypisana.');
        navigate('hostnames');
      },
    });
  } catch (error) { toast(error.message, 'error'); }
}

function hostnameSchemeForm(item = null) {
  const schemeId = item == null ? null : Number(item.id);
  const editing = Number.isInteger(schemeId) && schemeId > 0;
  if (item != null && !editing) {
    toast('Nieprawidłowy identyfikator patternu hostname. Odśwież widok i spróbuj ponownie.', 'error');
    return;
  }
  const patternField = field('Pattern hostname', 'pattern', {
    required: true,
    value: item?.pattern || '{location}-{env}-{role}-{number}',
    wide: true,
    placeholder: 'np. WRO-{env}-{role}-XXX',
    help: 'Obsługiwane: {number}, {random}, {year} oraz własne składniki, np. {location}, {env}, {role}. Zapis XXX zostanie zamieniony na {number} z odpowiednim dopełnieniem.',
  });
  const numberField = field('Następny numer', 'next_number', {
    type: 'number', min: 1, value: item?.next_number || 1,
    help: editing ? 'Sekwencji nie można cofnąć poniżej aktualnej wartości.' : 'Pierwszy numer użyty przy rezerwacji.',
  });
  const paddingField = field('Liczba cyfr', 'padding', {
    type: 'number', min: 1, max: 9, value: item?.padding || 3,
    help: 'Np. 3 daje 001, 002, 003.',
  });
  const preview = node('div', { class: 'hostname-pattern-preview wide' },
    node('span', { class: 'field-label', text: 'Podgląd wygenerowanego hostname' }),
    node('strong', { class: 'mono', 'data-generator-hostname-preview': 'true' }),
    node('small', { class: 'field-help', text: 'Podgląd używa przykładowych wartości dla zmiennych patternu.' }));
  const fields = node('div', { class: 'form-grid' },
    field('Nazwa patternu', 'name', { required: true, value: item?.name || '', placeholder: 'np. Produkcyjne serwery WRO' }),
    checkboxField('Aktywny', 'is_active', item?.is_active ?? true),
    patternField, numberField, paddingField, preview);
  const refreshPreview = () => {
    const pattern = patternField.querySelector('input').value;
    const padding = Number(paddingField.querySelector('input').value || 3);
    const nextNumber = Number(numberField.querySelector('input').value || 1);
    preview.querySelector('[data-generator-hostname-preview]').textContent = hostnameSchemePreview(pattern, padding, nextNumber) || '—';
  };
  [patternField, numberField, paddingField].forEach(wrapper => wrapper.querySelector('input').addEventListener('input', refreshPreview));
  refreshPreview();

  openModal({
    title: editing ? 'Edytuj pattern hostname' : 'Nowy pattern hostname',
    eyebrow: 'Narzędzia · Generator hostname',
    body: fields,
    submitLabel: editing ? 'Zapisz pattern' : 'Utwórz pattern',
    onSubmit: async data => {
      const normalized = normalizeGeneratorPattern(data.get('pattern'), data.get('padding'));
      await api(editing ? `/hostname-schemes/${schemeId}` : '/hostname-schemes', {
        method: editing ? 'PUT' : 'POST',
        body: {
          name: data.get('name'),
          pattern: normalized.pattern,
          next_number: Number(data.get('next_number')),
          padding: normalized.padding,
          is_active: data.has('is_active'),
        },
      });
      toast('Pattern hostname zapisany i jest dostępny w Blueprintach.');
      navigate('hostnames');
    },
  });
}

function generateHostname(schemes) {
  const active = schemes.filter(item => item.is_active);
  if (!active.length) return toast('Brak aktywnego patternu hostname.', 'error');
  const schemeField = selectField('Pattern', 'scheme_id', active.map(item => ({
    value: item.id, label: `${item.name} — ${item.pattern}`,
  })), active[0].id, { required: true });
  const valuesContainer = node('div', { class: 'wide' });
  const fields = node('div', { class: 'form-grid' },
    schemeField,
    checkboxField('Zarezerwuj nazwę', 'reserve', true),
    formSection('Składniki nazwy', 'Pola wynikają automatycznie z wybranego patternu.', valuesContainer));
  const render = () => {
    const scheme = active.find(item => String(item.id) === String(schemeField.querySelector('select').value));
    valuesContainer.replaceChildren(generatorValueFields(scheme?.pattern || ''));
  };
  schemeField.querySelector('select').addEventListener('change', render);
  render();

  openModal({
    title: 'Generuj hostname',
    eyebrow: 'Narzędzia · Generator hostname',
    body: fields,
    submitLabel: 'Generuj',
    onSubmit: async (_data, form) => {
      const result = await api('/hostnames/generate', {
        method: 'POST',
        body: {
          scheme_id: Number(form.elements.scheme_id.value),
          values: readGeneratorValues(form),
          reserve: form.elements.reserve.checked,
        },
      });
      toast(`Wygenerowano ${result.hostname}.`);
      navigate('hostnames');
    },
  });
}

registerView({
  id: 'hostnames',
  label: 'Generator hostname',
  iconName: 'network',
  navigation: false,
  navigationParent: 'tools',
  permission: 'hostnames.read',
  order: 156,
}, hostnamesView);
})();
