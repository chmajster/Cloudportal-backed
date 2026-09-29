'use strict';

(() => {
function showTemplateFields(template) {
  const schema = template.variables_schema || {};
  const required = new Set(schema.required || []);
  const rows = Object.entries(schema.properties || {}).map(([name, spec]) => {
    const base = schemaVariant(spec);
    const type = schemaType(spec);
    const enums = schemaEnum(spec);
    let constraints = '—';
    if (enums?.length) constraints = enums.join(', ');
    else if (base.minimum !== undefined || base.maximum !== undefined) constraints = `${base.minimum ?? '—'} – ${base.maximum ?? '—'}`;
    return {
      name,
      label: FIELD_LABELS[name] || spec.title || name,
      type,
      required: required.has(name),
      default: spec.default,
      constraints,
    };
  });
  const typeLabels = { string: 'Tekst', integer: 'Liczba całkowita', number: 'Liczba', boolean: 'Tak / nie', array: 'Lista' };
  dom.modal.classList.add('modal-wide');
  dom.modalTitle.textContent = `Pola: ${template.name}`;
  dom.modalEyebrow.textContent = `Szablon v${template.version}`;
  dom.modalBody.replaceChildren(rows.length ? table([
    { label: 'Pole', value: row => node('div', {}, node('strong', { text: row.label }), node('div', { class: 'mono muted', text: row.name })) },
    { label: 'Typ', value: row => typeLabels[row.type] || row.type },
    { label: 'Wymagane', value: row => row.required ? badge('Tak', 'warning') : 'Nie' },
    { label: 'Domyślnie', value: row => displayValue(row.default) },
    { label: 'Opcje / zakres', value: row => row.constraints },
  ], rows) : node('p', { class: 'muted', text: 'Szablon nie ma parametrów wejściowych.' }));
  dom.modalActions.replaceChildren(button('Zamknij', closeModal));
  if (!dom.modal.open) dom.modal.showModal();
}

function openProxmoxTemplateWizard(item = null) {
  if (!hasCommand('blueprints.proxmoxTemplateWizard')) {
    toast('Kreator szablonu IaC nie jest dostępny.', 'error');
    return;
  }
  return runCommand('blueprints.proxmoxTemplateWizard', item, { mode: 'template', returnTo: 'catalog' });
}

function canManageCatalogTemplate(item) {
  const required = new Set((item?.manager_role_ids || []).map(Number));
  if (!required.size) return true;
  const owned = new Set((state.identity?.roles || []).map(role => Number(role.id)));
  return [...required].some(id => owned.has(id));
}

async function toggleGeneratedTemplate(item) {
  try {
    await api('/blueprints/' + item.id + '/enabled', {
      method: 'PUT',
      body: { enabled: !item.is_active },
      headers: { 'If-Match': String(item.version) },
    });
    toast((item.is_active ? 'Wyłączono' : 'Włączono') + ' szablon „' + item.name + '”.');
    navigate('catalog');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function toggleCatalogItem(kind, item) {
  const enabled = item.enabled === false;
  const label = kind === 'templates' ? 'szablon' : 'playbook';
  try {
    await api('/catalog/' + kind + '/' + encodeURIComponent(item.id) + '/enabled', {
      method: 'PUT',
      body: { enabled },
    });
    toast((enabled ? 'Włączono ' : 'Wyłączono ') + label + ' „' + item.name + '”.');
    navigate('catalog');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function catalogView() {
  const [templates, blueprintResult, roleResult] = await Promise.all([
    api('/templates'),
    allowed('blueprints.read') ? api('/blueprints?limit=200') : Promise.resolve({ items: [] }),
    allowed('roles.read') ? api('/roles?limit=200') : Promise.resolve({ items: [] }),
  ]);
  const rolesById = new Map(roleResult.items.map(role => [Number(role.id), role.name]));

  const proxmoxModuleEnabled = templates.items.some(item => item.id === 'proxmox-vm' && item.enabled !== false);
  const canCreateTemplate = proxmoxModuleEnabled
    && allowed('blueprints.create')
    && allowed('providers.read')
    && allowed('credentials.read')
    && allowed('hostnames.read')
    && allowed('hostnames.create')
    && allowed('ipam.read');

  const actions = [];
  if (canCreateTemplate) {
    actions.push(button('Nowy szablon Terraform / OpenTofu', () => openProxmoxTemplateWizard(), 'primary'));
  }
  const sections = [
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' },
        node('div', {},
          node('h2', { text: 'Szablony Terraform / OpenTofu' }),
          node('p', { class: 'muted', text: 'Bazowe, zatwierdzone moduły IaC używane przez deploymenty i kreator.' }))),
      table([
        { label: 'Szablon', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: item.id })) },
        { label: 'Platforma', value: item => badge(CREDENTIAL_TYPE_CONFIG[item.provider]?.label || item.provider, 'info') },
        { label: 'Wersja', value: item => `v${item.version}` },
        { label: 'Status', value: item => badge(item.enabled === false ? 'Wyłączony' : 'Aktywny', item.enabled === false ? 'danger' : 'ok') },
        { label: 'Import', value: item => badge(item.importable ? 'Obsługiwany' : 'Tylko tworzenie', item.importable ? 'ok' : 'info') },
      ], templates.items, item => {
        const rowActions = [button('Pola', () => navigate('/catalog/templates/' + encodeURIComponent(item.id) + '/fields'))];
        if (allowed('settings.update')) {
          rowActions.push(button(item.enabled === false ? 'Włącz' : 'Wyłącz', () => toggleCatalogItem('templates', item), item.enabled === false ? 'primary' : 'danger'));
        }
        return rowActions;
      })
    ),
  ];

  if (allowed('blueprints.read')) {
    const generated = blueprintResult.items.filter(item => item.deployment?.template === 'proxmox-vm');
    sections.push(node('section', { class: 'panel' },
      node('div', { class: 'panel-header' },
        node('div', {},
          node('h2', { text: 'Szablony utworzone w kreatorze' }),
          node('p', { class: 'muted', text: 'Gotowe presety Proxmox z zapisaną VM bazową, parametrami, sposobem provisioningu i generatorem hostname.' }))),
      table([
        { label: 'Nazwa', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: item.slug })) },
        { label: 'Provisioning', value: item => badge(
          item.deployment?.executor === 'proxmox'
            ? 'Proxmox API'
            : item.deployment?.executor === 'opentofu' ? 'OpenTofu' : 'Terraform',
          'info'
        ) },
        { label: 'Provider', value: item => '#' + (item.deployment?.provider_id ?? '—') },
        { label: 'VM bazowa', value: item => {
          const nodeName = item.deployment?.variables?.template_node || item.deployment?.variables?.node || '—';
          const vmid = item.deployment?.variables?.template_id ?? '—';
          return nodeName + ' / VMID ' + vmid;
        } },
        { label: 'Wersja', value: item => 'v' + item.version },
        { label: 'Role zarządzające', value: item => (item.manager_role_ids || []).length
          ? (item.manager_role_ids || []).map(id => rolesById.get(Number(id)) || ('Rola #' + id)).join(', ')
          : 'Brak roli zarządzającej' },
        { label: 'Status', value: item => badge(item.is_active ? 'Aktywny' : 'Nieaktywny', item.is_active ? 'ok' : 'danger') },
      ], generated, item => {
        const rowActions = [];
        const canManage = canManageCatalogTemplate(item);
        if (allowed('blueprints.update') && canManage) {
          if (canCreateTemplate || item.is_active === false) {
            rowActions.push(button('Edytuj', () => openProxmoxTemplateWizard(item)));
          }
          rowActions.push(button(item.is_active ? 'Wyłącz' : 'Włącz', () => toggleGeneratedTemplate(item), item.is_active ? 'danger' : 'primary'));
        }
        const deploymentTemplateEnabled = templates.items.some(template =>
          template.id === item.deployment?.template && template.enabled !== false);
        if (allowed('blueprints.execute') && deploymentTemplateEnabled && item.is_active) {
          rowActions.push(button('Użyj', () => {
            if (!hasCommand('blueprints.execute')) {
              toast('Uruchamianie szablonu nie jest dostępne.', 'error');
              return;
            }
            runCommand('blueprints.execute', item);
          }, 'primary'));
        }
        return rowActions;
      })
    ));
  }

  dom.content.replaceChildren(
    heading('Katalog IaC i kreator wielokrotnie używalnych szablonów VM. Kreator nie przyjmuje arbitralnego HCL — generuje bezpieczny preset na bazie zatwierdzonego modułu Proxmox.', actions),
    ...sections,
  );
}

registerRoutedForm({
  id: 'catalog-template-fields',
  pattern: /^\/catalog\/templates\/(?<id>[^/]+)\/fields$/,
  parent: 'catalog',
  permission: 'terraform.read',
  label: 'Katalog IaC',
}, async match => {
  const templates = (await api('/templates')).items;
  const item = templates.find(value => String(value.id) === String(match.params.id));
  if (!item) throw new Error('Nie znaleziono szablonu.');
  showTemplateFields(item);
});
registerView({ id: 'catalog', label: 'Katalog IaC', icon: 'C', permission: 'terraform.read', order: 60 }, catalogView);
})();
